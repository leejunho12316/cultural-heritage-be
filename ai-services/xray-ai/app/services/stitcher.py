from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import config
from app.schemas.stitch_request import LocalStitchJobRequest


class StitchExecutionError(RuntimeError):
    """X-ray 결합 엔진 실행 또는 결과 검증 실패."""


@dataclass(frozen=True)
class StitchExecutionResult:
    return_code: int
    output_dir: str
    report_path: str
    layout_path: str
    assembled_image_path: str
    stdout_log: str
    stderr_log: str


class Stitcher:
    """Process-local workspace의 컬러 기준 이미지와 X-ray 조각을 결합한다."""

    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

    def __init__(
        self,
        engine_dir: Path | None = None,
        single_script: Path | None = None,
    ) -> None:
        self.engine_dir = (engine_dir or config.STITCH_ENGINE_DIR).resolve()
        self.single_script = (
            single_script or config.STITCH_SINGLE_SCRIPT
        ).resolve()

    def run(self, request: LocalStitchJobRequest) -> StitchExecutionResult:
        color_directory = Path(request.colorDirectory).resolve()
        xray_directory = Path(request.xrayDirectory).resolve()
        output_root = Path(request.outputDirectory).resolve()

        self._require_directory(color_directory, "colorDirectory")
        self._require_directory(xray_directory, "xrayDirectory")
        self._require_file(self.single_script, "assemble_xray.py")

        reference_image = self._find_reference_image(color_directory)
        fragment_images = self._list_images(xray_directory)
        if len(fragment_images) < 2:
            raise StitchExecutionError(
                "X-ray 조각 이미지는 2장 이상이어야 합니다: "
                f"actual={len(fragment_images)}"
            )

        config_path = self._resolve_config(request.configName)

        job_directory = output_root.parent
        run_output = output_root / "assembly"
        artifact_output = run_output / "artifacts" / request.artifactId
        log_directory = job_directory / "logs"

        if run_output.exists():
            shutil.rmtree(run_output)

        artifact_output.mkdir(parents=True, exist_ok=True)
        log_directory.mkdir(parents=True, exist_ok=True)

        stdout_path = log_directory / "stitch.stdout.log"
        stderr_path = log_directory / "stitch.stderr.log"

        command = [
            sys.executable,
            str(self.single_script),
            "--reference",
            str(reference_image),
            "--fragments",
            str(xray_directory),
            "--output",
            str(artifact_output),
            "--config",
            str(config_path),
        ]

        try:
            with (
                stdout_path.open("w", encoding="utf-8") as stdout_file,
                stderr_path.open("w", encoding="utf-8") as stderr_file,
            ):
                completed = subprocess.run(
                    command,
                    cwd=self.engine_dir,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    text=True,
                    check=False,
                )
        except OSError as exc:
            raise StitchExecutionError(
                f"X-ray 결합 엔진을 실행하지 못했습니다: {exc}"
            ) from exc

        if completed.returncode != 0:
            raise StitchExecutionError(
                "X-ray 결합 엔진이 실패했습니다. "
                f"return_code={completed.returncode}, "
                f"stderr_tail={self._read_tail(stderr_path)}"
            )

        report_path = artifact_output / "report.json"
        layout_path = artifact_output / "layout.json"
        assembled_image_path = artifact_output / "assembled_xray.png"

        self._attach_artifact_metadata(
            artifact_id=request.artifactId,
            report_path=report_path,
            layout_path=layout_path,
        )
        self._validate_assembly_outputs(
            artifact_id=request.artifactId,
            report_path=report_path,
            layout_path=layout_path,
            assembled_image_path=assembled_image_path,
        )

        return StitchExecutionResult(
            return_code=completed.returncode,
            output_dir=str(run_output),
            report_path=str(report_path),
            layout_path=str(layout_path),
            assembled_image_path=str(assembled_image_path),
            stdout_log=str(stdout_path),
            stderr_log=str(stderr_path),
        )

    def _resolve_config(self, config_name: str) -> Path:
        clean_name = Path(config_name).name
        config_dir = config.STITCH_CONFIG_DIR.resolve()
        candidates = [
            config_dir / clean_name,
            config_dir / f"{clean_name}.json",
            config_dir / f"config.{clean_name}.json",
            self.engine_dir / "configs" / clean_name,
            self.engine_dir / "configs" / f"{clean_name}.json",
            self.engine_dir / "configs" / f"config.{clean_name}.json",
            self.engine_dir / clean_name,
            self.engine_dir / f"{clean_name}.json",
            self.engine_dir / f"config.{clean_name}.json",
        ]
        return self._first_existing_file(
            candidates,
            f"설정 파일({config_name})",
        )

    @classmethod
    def _find_reference_image(cls, directory: Path) -> Path:
        images = cls._list_images(directory)
        if len(images) != 1:
            raise StitchExecutionError(
                "컬러 기준 이미지는 정확히 1장이어야 합니다: "
                f"actual={len(images)}, files={[path.name for path in images]}"
            )
        return images[0]

    @classmethod
    def _list_images(cls, directory: Path) -> list[Path]:
        return sorted(
            (
                path.resolve()
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in cls.IMAGE_EXTENSIONS
            ),
            key=lambda path: [
                (0, int(part)) if part.isdigit() else (1, part.casefold())
                for part in re.findall(r"\d+|\D+", path.name)
            ],
        )

    @staticmethod
    def _attach_artifact_metadata(
        artifact_id: str,
        report_path: Path,
        layout_path: Path,
    ) -> None:
        report = Stitcher._read_json_object(report_path, "report.json")
        layout = Stitcher._read_json_object(layout_path, "layout.json")

        report["artifact"] = {"id": artifact_id}
        layout["artifact"] = {"id": artifact_id}

        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        layout_path.write_text(
            json.dumps(layout, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _validate_assembly_outputs(
        artifact_id: str,
        report_path: Path,
        layout_path: Path,
        assembled_image_path: Path,
    ) -> None:
        required_files = [report_path, layout_path, assembled_image_path]
        missing = [str(path) for path in required_files if not path.is_file()]
        if missing:
            raise StitchExecutionError(
                "결합 엔진은 정상 종료했지만 필수 결과 파일이 없습니다: "
                + ", ".join(missing)
            )

        if assembled_image_path.stat().st_size <= 0:
            raise StitchExecutionError(
                f"결합 이미지가 비어 있습니다: {assembled_image_path}"
            )

        report = Stitcher._read_json_object(report_path, "report.json")
        layout = Stitcher._read_json_object(layout_path, "layout.json")

        report_status = str(report.get("status", "")).lower()
        if report_status != "completed":
            raise StitchExecutionError(
                f"report.status가 completed가 아닙니다: {report_status!r}"
            )

        fragments = layout.get("fragments")
        if not isinstance(fragments, list):
            raise StitchExecutionError("layout.fragments가 배열이 아닙니다.")

        fragment_count = report.get("fragmentCount")
        if fragment_count != len(fragments):
            raise StitchExecutionError(
                "report.fragmentCount와 layout.fragments 개수가 다릅니다: "
                f"{fragment_count!r} != {len(fragments)}"
            )

        quality_flags = report.get("qualityFlags")
        invariant = (
            quality_flags.get("runtimeInvariantValidation")
            if isinstance(quality_flags, dict)
            else None
        )
        invariant_passed = (
            invariant.get("passed") if isinstance(invariant, dict) else None
        )
        if invariant_passed is not True:
            raise StitchExecutionError(
                "runtimeInvariantValidation.passed가 true가 아닙니다."
            )

        artifact = layout.get("artifact")
        layout_artifact_id = (
            artifact.get("id") if isinstance(artifact, dict) else None
        )
        if layout_artifact_id != artifact_id:
            raise StitchExecutionError(
                "layout.artifact.id가 요청 artifactId와 다릅니다: "
                f"{layout_artifact_id!r} != {artifact_id!r}"
            )

    @staticmethod
    def _read_json_object(path: Path, label: str) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StitchExecutionError(
                f"{label}을 읽을 수 없습니다: {path}: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise StitchExecutionError(
                f"{label} 최상위 값이 객체가 아닙니다: {path}"
            )
        return data

    @staticmethod
    def _first_existing_file(candidates: list[Path], label: str) -> Path:
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        raise StitchExecutionError(
            f"{label}을 찾을 수 없습니다. 확인한 경로: "
            + ", ".join(str(path) for path in candidates)
        )

    @staticmethod
    def _require_directory(path: Path, label: str) -> None:
        if not path.is_dir():
            raise StitchExecutionError(f"{label} 디렉터리가 없습니다: {path}")

    @staticmethod
    def _require_file(path: Path, label: str) -> None:
        if not path.is_file():
            raise StitchExecutionError(f"{label} 파일이 없습니다: {path}")

    @staticmethod
    def _read_tail(path: Path, limit: int = 4000) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
