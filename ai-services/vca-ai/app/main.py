from fastapi import FastAPI

from app.routers import assessments
from app.schemas import HealthResponse


app = FastAPI(title="VCA AI Adapter", version="0.1.0")
app.include_router(assessments.router)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="vca-ai", mode="deterministic")
