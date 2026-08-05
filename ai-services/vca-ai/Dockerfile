FROM python:3.13-slim

WORKDIR /code

COPY app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir uv && pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
