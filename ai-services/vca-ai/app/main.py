from fastapi import FastAPI, HTTPException, Request, Response

from app.routers import assessments
from app.schemas import HealthResponse, SystemInfoModelResponse, SystemInfoResponse
from app.services.ollama_proxy import OllamaProxyError, forward_chat
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


# BE의 VcaOverallConditionGenerator가 부르는 Ollama 리버스 프록시 - 같은 팟의
# localhost:11434로 그대로 전달한다(services/ollama_proxy.py 참고). vca-ai의
# 다른 엔드포인트와 마찬가지로 X-VCA-Access-Token 검증 없이 열어둔다.
@app.post("/ollama/api/chat")
async def ollama_chat_proxy(request: Request) -> Response:
    body = await request.body()
    try:
        upstream_response = forward_chat(body)
    except OllamaProxyError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return Response(content=upstream_response, media_type="application/json")
