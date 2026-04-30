FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY worker/ ./worker/
COPY slack_bot/ ./slack_bot/

ENV PYTHONPATH=/app

CMD ["python", "-m", "slack_bot.app"]
