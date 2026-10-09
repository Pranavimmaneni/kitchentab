# Image for the in-repo fake services (mock OIDC issuer, fake Web Push service).
# Build context: apps/api. Local dev and CI only.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /usr/local/bin/uv
WORKDIR /srv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY fakeservices ./fakeservices
RUN useradd --system fake && chown -R fake /srv
USER fake
