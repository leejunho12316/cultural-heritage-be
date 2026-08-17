"""표시 목적으로만 쓰는, 읽기 전용 환경/모델 진단.

파이프라인의 일부가 아니다. vca-ai의 system-info 엔드포인트는 이걸
`uv run python -m modules.orchestration.system_info`로 셸아웃해서 실행한다
(이 uv 환경에서, vca-ai 자신의 FastAPI 프로세스와는 별도의 프로세스).
그래야 torch/transformers가 설치돼 있지 않은 vca-ai 자체 런타임이 아니라,
이 엔진이 실제로 확정한 CPU/GPU 디바이스와 여기 설치된 라이브러리/모델
버전을 보고할 수 있다.
"""

from __future__ import annotations

import json
import platform
from importlib import metadata
from pathlib import Path
from typing import Final

_REPORTED_LIBRARIES: Final = ("torch", "transformers", "opencv-python-headless")


def _library_version(name: str) -> str | None:
    """Return the installed version of a package, or None if absent."""
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _resolved_device() -> str:
    """Return the GPU/CPU backend this engine would resolve to for --device auto."""
    from modules.preprocessing.model_runtime.runtime import (  # noqa: PLC0415
        resolve_runtime_device,
    )
    from modules.preprocessing.preflight.request import Device  # noqa: PLC0415

    try:
        return resolve_runtime_device(Device.AUTO)
    except Exception:  # noqa: BLE001 - diagnostics endpoint, never fail the request
        return "unknown"


def _model_inventory(engine_root: Path) -> list[dict[str, str]]:
    """Return the configured model key/repo/revision list, best-effort."""
    inventory_path = engine_root / "models" / "inventory" / "model_inventory.json"
    try:
        raw: object = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    models = raw.get("models", []) if isinstance(raw, dict) else []
    return [
        {
            "key": str(model.get("key", "")),
            "repoId": str(model.get("repo_id", "")),
            "revision": str(model.get("revision", "")),
        }
        for model in models
        if isinstance(model, dict)
    ]


def collect_system_info() -> dict[str, object]:
    """Collect OS/device/library/model diagnostics for a display-only footer."""
    engine_root = Path(__file__).resolve().parents[2]
    libraries = {
        name: version
        for name in _REPORTED_LIBRARIES
        if (version := _library_version(name)) is not None
    }
    return {
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "pythonVersion": platform.python_version(),
        "device": _resolved_device(),
        "libraries": libraries,
        "models": _model_inventory(engine_root),
    }


def main() -> None:
    """Print collected system info as JSON to stdout."""
    print(json.dumps(collect_system_info()))  # noqa: T201 - CLI output is the contract


if __name__ == "__main__":
    main()
