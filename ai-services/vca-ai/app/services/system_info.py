"""Display-only environment diagnostics for the FE's system-info footer.

vca-ai's own process never imports torch/transformers - the VCA engine runs
as a separate `uv run` subprocess (see assessment_runs.py), possibly on a
bare host (native GPU/MPS) rather than in this container. So to report the
actual device/library/model versions the engine resolves to, this shells
out to `modules.orchestration.system_info` in that same uv environment and
parses its JSON stdout. The result is cached in-process since it can't
change while this service keeps running.
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
    """Return cached engine environment diagnostics, probing once on first call."""
    global _cached_system_info  # noqa: PLW0603 - process-lifetime cache, single-writer
    if _cached_system_info is None:
        _cached_system_info = _probe_engine()
    return _cached_system_info
