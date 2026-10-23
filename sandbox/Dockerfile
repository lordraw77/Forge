# syntax=docker/dockerfile:1.7
# The sandbox runner service: the only Forge component that holds the container engine socket.
# Standalone on purpose: server.py imports nothing from the rest of Forge.
FROM python:3.12-slim-trixie
RUN groupadd --system --gid 10001 forge \
 && useradd --system --uid 10001 --gid forge --home-dir /app --shell /usr/sbin/nologin forge
WORKDIR /app
RUN pip install --no-cache-dir "fastapi==0.143.2" "uvicorn==0.54.0" "httpx==0.28.1"
COPY server.py /app/server.py
# The container engine socket must be readable by this user: run with --group-add <docker gid>
# (Compose: SANDBOX_DOCKER_GID).
USER forge
EXPOSE 8090
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2).status == 200 else 1)"]
CMD ["python", "-m", "uvicorn", "--factory", "server:create_app", "--host", "0.0.0.0", "--port", "8090"]
