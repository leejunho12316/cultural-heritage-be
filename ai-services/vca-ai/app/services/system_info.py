"""FE의 system-info 푸터를 위한, 표시 전용 환경 진단.

vca-ai 자신의 프로세스는 torch/transformers를 전혀 import하지 않는다 - VCA
엔진은 (assessment_runs.py 참고) 별도의 `uv run` 서브프로세스로 실행되며,
이 컨테이너가 아니라 베어 호스트(네이티브 GPU/MPS)에서 돌 수도 있다.
그래서 엔진이 실제로 확정한 디바이스/라이브러리/모델 버전을 보고하려면,
같은 uv 환경의 `modules.orchestration.system_info`를 셸아웃으로 실행하고
그 JSON stdout을 파싱한다. 이 서비스가 계속 실행되는 동안 결과가 바뀔 수
없으므로 프로세스 내부에 캐싱된다.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_PROBE_TIMEOUT_SECONDS: Final = 20


@dataclass(frozen=True, slots=True)
class SystemInfoModel:
    key: str
    repo_id: str
    revision: str


@dataclass(frozen=True, slots=True)
class SystemInfo:
    os_label: str
    python_version: str
    device: str
    libraries: dict[str, str]
    models: tuple[SystemInfoModel, ...]


_FALLBACK = SystemInfo(
    os_label="알 수 없음",
    python_version="알 수 없음",
    device="알 수 없음",
    libraries={},
    models=(),
)

_cached_system_info: SystemInfo | None = None


def _engine_root() -> Path:
    return Path(os.environ.get("VCA_ENGINE_ROOT", "/vca_v2"))


def _probe_engine() -> SystemInfo:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            ["uv", "run", "python", "-m", "modules.orchestration.system_info"],
            cwd=_engine_root(),
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=True,
        )
        raw: object = json.loads(completed.stdout)
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ):
        return _FALLBACK
    if not isinstance(raw, dict):
        return _FALLBACK
    models = tuple(
        SystemInfoModel(
            key=str(model.get("key", "")),
            repo_id=str(model.get("repoId", "")),
            revision=str(model.get("revision", "")),
        )
        for model in raw.get("models", [])
        if isinstance(model, dict)
    )
    libraries = raw.get("libraries", {})
    return SystemInfo(
        os_label=str(raw.get("os", _FALLBACK.os_label)),
        python_version=str(raw.get("pythonVersion", _FALLBACK.python_version)),
        device=str(raw.get("device", _FALLBACK.device)),
        libraries=libraries if isinstance(libraries, dict) else {},
        models=models,
    )


def get_system_info() -> SystemInfo:
    """Return cached engine environment diagnostics, probing once on first success.

    A failed probe (e.g. a transient `uv` PATH issue right after startup) is not
    cached, so a later call can retry instead of being stuck at the fallback for
    the rest of the process's lifetime.
    """
    global _cached_system_info  # noqa: PLW0603 - process-lifetime cache, single-writer
    if _cached_system_info is None:
        probed = _probe_engine()
        if probed == _FALLBACK:
            return probed
        _cached_system_info = probed
    return _cached_system_info
