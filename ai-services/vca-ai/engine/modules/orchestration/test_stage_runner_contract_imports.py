from __future__ import annotations

import subprocess
import sys
from typing import Final

CONTRACT_IMPORT_POLICY_SCRIPT: Final = (
    "import sys; import modules.orchestration.stage_runner_contracts; "
    "print(','.join(sorted(set(sys.modules) & "
    "{'PIL','torch','transformers','sam2'})))"
)


def test_stage_runner_contract_import_does_not_load_visual_runtimes() -> None:
    # Given: a fresh process that imports only startup runner contracts.
    # When: the contracts module is imported.
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", CONTRACT_IMPORT_POLICY_SCRIPT],
        check=True,
        capture_output=True,
        text=True,
    )

    # Then: optional visual/model runtimes remain unloaded.
    assert completed.stdout == "\n"
