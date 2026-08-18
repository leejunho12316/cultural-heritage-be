"""Project-stage adapter for report generation without orchestration changes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from modules.report_generating.models import ReportGeneratingRequest
from modules.report_generating.runner import run_report_generation

if TYPE_CHECKING:
    from modules.orchestration.stage_paths import StagePathMap


class _ProjectStageRequest(Protocol):
    @property
    def paths(self) -> StagePathMap: ...


# 오케스트레이션이 report_generating 스테이지를 실행할 때 호출하는 프로젝트
# 어댑터 진입점. 직전의 report_trace_assembly 스테이지가 자기 디렉터리 없이
# report_generating 디렉터리에 남긴 report_trace_source.json을 입력으로
# run_report_generation을 실행한다.
def run_report_generating_stage(request: _ProjectStageRequest) -> int:
    """Run report generation from the report trace source sidecar."""
    workspace_root = request.paths.report_generating.parents[2]
    trace_source_path = request.paths.report_generating / "report_trace_source.json"
    result = run_report_generation(
        ReportGeneratingRequest(
            workspace_root,
            request.paths.report_generating,
            trace_source_path,
        )
    )
    return 0 if result["verification_status"] == "pass" else 2
