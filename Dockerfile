FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        nodejs \
        npm \
    && npm install -g @openai/codex \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY dashboard ./dashboard

RUN pip install --no-cache-dir -e .

EXPOSE 8765

CMD ["python", "-m", "racing_model.cli", "serve", "--host", "0.0.0.0", "--port", "8765", "--model-path", "models/baseline.json", "--odds-interval", "30"]
