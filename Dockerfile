# 1) Build the admin console.
FROM node:22-alpine AS admin
ARG NPM_REGISTRY=https://registry.npmjs.org/
WORKDIR /admin
COPY admin/package.json admin/package-lock.json ./
RUN npm ci --registry="$NPM_REGISTRY"
COPY admin/ ./
# vite.config.ts writes to ../app/static/admin, i.e. /app/static/admin
RUN npm run build

# 2) The API, serving the built console at /admin.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/srv/.venv/bin:$PATH"
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY app ./app
COPY --from=admin /app/static/admin ./app/static/admin
RUN useradd --system --no-create-home app && chown -R app /srv
USER app
EXPOSE 8000
# Render terminates TLS. The client address is read from X-Forwarded-For by app/core/client_ip.py using
# TRUSTED_PROXY_HOPS (never trust every hop: the leftmost entries are written by the client).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-proxy-headers --workers ${WEB_CONCURRENCY:-2}"]
