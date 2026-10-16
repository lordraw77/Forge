# Forge – frontend

Web UI of **Forge**, a self-hosted, LLM-agnostic platform to build and run AI agents, RAG pipelines and governed prompts, with multi-tenancy and observability.

> **Status: pre-release (`0.1.0-dev`).** This image contains the application shell (navigation, project selector, home page). The studios are being added phase by phase. Do not use in production yet.

## What is inside

- The Angular 21 single-page application, built with PrimeNG 21 (MIT). It is prepared for English and Italian; the Italian translation is in progress.
- An unprivileged nginx server listening on port **8080**.
- A reverse proxy that forwards `/api/` and `/v1/` to the backend at `http://api:8000`, with buffering disabled so server-sent events stream correctly.

## Tags

| Tag | Content |
| --- | --- |
| `<version>` | Immutable release, e.g. `0.1.0-dev` |

Use it together with [`lordraw/forge-backend`](https://hub.docker.com/r/lordraw/forge-backend) of the **same version**.

## Usage

The container expects the backend to be reachable under the hostname `api` on port `8000`, for example as the `api` service in the same Docker Compose network:

```yaml
services:
  api:
    image: lordraw/forge-backend:0.1.0-dev
    command: ["api"]
    # ... see the backend image for configuration
  web:
    image: lordraw/forge-frontend:0.1.0-dev
    ports: ["8080:8080"]
    depends_on: [api]
```

Then open `http://localhost:8080`.

## Health

`GET /nginx-health` returns `200 ok`. The image also defines a Docker `HEALTHCHECK`.

## Security

- Runs as a non-root user (based on `nginxinc/nginx-unprivileged`) and supports a read-only root filesystem (mount `/tmp` as tmpfs).
- Sends a strict Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff`, Referrer-Policy and Permissions-Policy.
- Does not terminate TLS. Put a TLS 1.3 reverse proxy or ingress in front of it.

## License and source

Forge is in early development, and the source repository and license will be announced with the first public release.
