FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

COPY --from=frontend /app/frontend/dist /app/frontend/dist

RUN pip install uv
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --no-dev

COPY api.py ./

ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').environ.get('PORT', '8000') + '/api/health/live', timeout=3)" || exit 1
CMD uv run python api.py --port $PORT
