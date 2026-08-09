"""Read-only environment/model diagnostics for display purposes only.

Not part of the pipeline. vca-ai's system-info endpoint shells this out as
`uv run python -m modules.orchestration.system_info` (a separate process
from vca-ai's own FastAPI process, in this uv environment) so it can report
the CPU/GPU device this engine actually resolves to, plus the library and
model versions installed here - rather than vca-ai's own runtime, which
does not have torch/transformers installed.
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
