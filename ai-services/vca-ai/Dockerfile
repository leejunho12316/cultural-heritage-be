FROM python:3.13-slim

WORKDIR /code

ENV ENGINE_ROOT=/vca_v2 \
    UV_PROJECT_ENVIRONMENT=/opt/vca-uv-env \
    UV_CACHE_DIR=/opt/vca-uv-cache \
    UV_FROZEN=true \
    PYTHONUNBUFFERED=1

# unzip: vca-entrypoint.sh가 RAG 코퍼스 캐시 아카이브(Google Drive에서 받음)를
# 여기서 풀어 모델 캐시 루트에 채운다.
RUN apt-get update && apt-get install -y --no-install-recommends unzip \
    && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir uv && pip install --no-cache-dir -r requirements.txt

COPY engine/ /vca_v2/
RUN uv sync --project "$ENGINE_ROOT" --group vision --no-install-project

COPY app/ ./app/
COPY vca-entrypoint.sh ./vca-entrypoint.sh
RUN sed -i 's/\r$//' ./vca-entrypoint.sh && chmod +x ./vca-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./vca-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
