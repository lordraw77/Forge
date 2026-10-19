# syntax=docker/dockerfile:1.7
FROM node:24.22.0-trixie-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.31-alpine AS runtime
ARG VERSION=0.0.0-dev
LABEL org.opencontainers.image.title="forge-web" \
      org.opencontainers.image.version="${VERSION}"
COPY nginx.conf.template /etc/nginx/forge/default.conf.template
# The rendered configuration lives in /tmp (the root filesystem is read-only at run time).
RUN printf 'include /tmp/forge-nginx/default.conf;\n' > /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.d/25-forge-nginx.sh /docker-entrypoint.d/25-forge-nginx.sh
COPY --from=build /app/dist/forge-web/browser /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/nginx-health || exit 1
