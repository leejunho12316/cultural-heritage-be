from fastapi import FastAPI

from app.routers import assessments
from app.schemas import HealthResponse, SystemInfoModelResponse, SystemInfoResponse
from app.services.system_info import get_system_info


app = FastAPI(title="VCA AI Adapter", version="0.1.0")
app.include_router(assessments.router)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="vca-ai", mode="deterministic")


@app.get("/system-info", response_model=SystemInfoResponse)
def system_info() -> SystemInfoResponse:
    info = get_system_info()
    return SystemInfoResponse(
        os=info.os_label,
        pythonVersion=info.python_version,
        device=info.device,
        libraries=info.libraries,
        models=tuple(
            SystemInfoModelResponse(key=model.key, repoId=model.repo_id, revision=model.revision)
            for model in info.models
        ),
    )
