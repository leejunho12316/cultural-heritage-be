FROM python:3.13-slim

WORKDIR /code

COPY app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir uv && pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY engine/ /vca_v2/
COPY vca-entrypoint.sh ./vca-entrypoint.sh
RUN sed -i 's/\r$//' ./vca-entrypoint.sh && chmod +x ./vca-entrypoint.sh

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["./vca-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
