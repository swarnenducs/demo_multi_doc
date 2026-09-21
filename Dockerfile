# Build from this folder: docker build -t document-processing-mcp .
# Compose: context: ./document-processing-mcp
FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.7.12 /uv /usr/local/bin/uv
COPY . .
RUN uv pip install --system -e .
ENV DOCUMENT_MCP_HOST=0.0.0.0 DOCUMENT_MCP_PORT=8001
ENV DOCUMENT_PROMPTS_DIR=/app/prompts
EXPOSE 8001
CMD ["python", "run.py", "--transport", "http", "--host", "0.0.0.0", "--port", "8001"]
