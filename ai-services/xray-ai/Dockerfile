FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

RUN pip install --no-cache-dir \
        torch==2.5.1 torchvision==0.20.1 \
        --index-url https://download.pytorch.org/whl/cpu

COPY app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY models/ ./models/
COPY xray_assembler/ ./xray_assembler/
COPY configs/ ./configs/
COPY mappings/ ./mappings/
COPY scripts/ ./scripts/

ENV XRAY_MODEL_PATH=/code/models/final_xray_defect_best.pt
ENV XRAY_DEVICE=cpu
ENV XRAY_STITCH_JOBS_ROOT=/shared/jobs
ENV XRAY_STITCH_ENGINE_DIR=/code
ENV XRAY_STITCH_SINGLE_SCRIPT=/code/scripts/assemble_xray.py
ENV XRAY_STITCH_BATCH_SCRIPT=/code/scripts/batch_assemble.py
ENV XRAY_STITCH_CONFIG_DIR=/code/configs
ENV XRAY_STITCH_MAPPING_DIR=/code/mappings
ENV XRAY_STITCH_MAPPING_PATH=/code/mappings/mapping.color_front.json
ENV XRAY_STITCH_CACHE_DIR=/shared/cache
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
