FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY worker/ ./worker/

ENV PYTHONPATH=/app

CMD ["python", "-m", "worker.main"]
