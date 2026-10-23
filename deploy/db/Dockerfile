# syntax=docker/dockerfile:1.7
# PostgreSQL + pgvector (ADR-008) with WAL-G for continuous archiving and point-in-time recovery
# (E3.7, RPO 15 min / RTO 1 h). Same base image as the default database: pg_data is compatible.
FROM pgvector/pgvector:0.8.7-pg17-trixie
ARG WALG_VERSION=v3.0.9
ARG WALG_SHA256=
USER root
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSL -o /tmp/wal-g.tar.gz \
      "https://github.com/wal-g/wal-g/releases/download/${WALG_VERSION}/wal-g-pg-24.04-amd64.tar.gz" \
 && if [ -n "$WALG_SHA256" ]; then echo "${WALG_SHA256}  /tmp/wal-g.tar.gz" | sha256sum -c -; fi \
 && tar -xzf /tmp/wal-g.tar.gz -C /usr/local/bin \
 && mv /usr/local/bin/wal-g-pg-24.04-amd64 /usr/local/bin/wal-g \
 && chmod 0755 /usr/local/bin/wal-g \
 && rm -f /tmp/wal-g.tar.gz \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY pitr-backup-loop.sh pitr-restore.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/pitr-backup-loop.sh /usr/local/bin/pitr-restore.sh
