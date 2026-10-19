# ADR-015 – S3 API for object storage; SeaweedFS in the shipped Compose

- Status: Accepted (PO, 2026-10-19)
- Date: 2026-10-16

## Context

The spec names MinIO (alternative: Ceph RGW). MinIO Inc. has wound down the open-source community edition:

| When | Change |
| --- | --- |
| May 2025 | Admin console removed from the community edition (basic object browser only) |
| Sep–Oct 2025 | Pre-built binaries and container images no longer published (Docker Hub, quay.io); "source-only distribution" |
| Dec 2025 | Repository in maintenance mode: no features, no PRs, security fixes case by case |
| 2026 | `github.com/minio/minio` archived; README: "THIS REPOSITORY IS NO LONGER MAINTAINED", pointing to the proprietary AIStor (Free / Enterprise) |

Verified on 2026-10-16: `minio/minio:latest` and `quay.io/minio/minio:latest` manifests not found; GitHub API reports the repository `archived: true`. Dates before 2026 come from secondary sources and may be off by a few weeks.

Options considered: build MinIO from the archived source (AGPL-3.0, unmaintained, no security fixes — not acceptable for an enterprise product); AIStor Free (proprietary licence, conflicts with "everything is open source"); another S3-compatible open-source server.

## Decision

- The backend depends on the **S3 API only** (endpoint, access key, secret key, bucket from deployment wiring, ADR-007). Any S3-compatible store works: MinIO (self-built or commercial), Ceph RGW, SeaweedFS, cloud S3.
- The shipped Compose uses **SeaweedFS 4.47** (Apache-2.0), single-node `weed server -s3`. S3 identities are generated inside the container from `S3_ACCESS_KEY` / `S3_SECRET_KEY`, so no secret file is mounted.
- Helm (Phase 3) supports both deployments, chosen per installation: an **external** S3 endpoint provided by the customer, or **SeaweedFS shipped in the chart** (sub-chart). Neither is privileged; the chart values select one (PO, 2026-10-19).
- Integration tests for E1.14 run against SeaweedFS and must avoid MinIO-only API extensions.

## PO confirmation (2026-10-19)

- SeaweedFS replaces MinIO in the standard installation (deviation from the spec accepted).
- Production object storage: both an external S3 service and SeaweedFS in the Helm chart are supported.

## Consequences

- No lock-in to one object store; air-gapped installs can reuse an existing S3 service.
- SeaweedFS S3 compatibility must be checked for the features we use (multipart upload, presigned URLs, bucket lifecycle for purge).
