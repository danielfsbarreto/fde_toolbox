# The review app for Heroku: the built frontend (ui/web) served by the thin API (ui/server).
# Deploy only this folder: `git subtree push --prefix ui heroku main` (heroku.yml builds this image).
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
# WeasyPrint (the account reports' PDF) lays text out with Pango
RUN apt-get update && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /bin/uv
WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY server/review_api ./review_api
COPY --from=web /web/dist /app/web/dist
ENV PATH="/app/server/.venv/bin:$PATH" WEB_DIST=/app/web/dist
# exec: uvicorn gets Heroku's SIGTERM and shuts down cleanly; sh -c expands $PORT
CMD ["sh", "-c", "exec uvicorn review_api.app:app --host 0.0.0.0 --port ${PORT:-8765} --proxy-headers --forwarded-allow-ips='*'"]
