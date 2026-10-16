# syntax=docker/dockerfile:1.7
# One image, several roles (ADR-001): `forge api`, `forge worker runs|ingest`, `forge migrate`.

FROM ghcr.io/astral-sh/uv:0.11.31 AS uv

FROM python:3.12-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-trixie AS runtime
ARG VERSION=0.0.0-dev
LABEL org.opencontainers.image.title="forge-backend" \
      org.opencontainers.image.version="${VERSION}"
RUN groupadd --system --gid 10001 forge \
 && useradd --system --uid 10001 --gid forge --home-dir /app --shell /usr/sbin/nologin forge
WORKDIR /app
COPY --from=build --chown=forge:forge /app/.venv /app/.venv
COPY --from=build --chown=forge:forge /app/alembic.ini /app/alembic.ini
COPY --from=build --chown=forge:forge /app/migrations /app/migrations
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER forge
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["forge"]
CMD ["api"]
