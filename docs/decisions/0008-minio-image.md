# ADR-0008: MinIO image for local dev

`minio/minio` is no longer published on Docker Hub (pull access denied) and quay.io requires auth.
Local compose uses `bitnamilegacy/minio:2025.4.22`, the same MinIO release from Broadcom's frozen
Bitnami archive, which also creates the bucket from `MINIO_DEFAULT_BUCKETS`. Local and test only;
production uses Vercel Blob (ADR-0015).
