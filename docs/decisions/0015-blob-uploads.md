# ADR-0015: Receipt storage via Vercel Blob client uploads (BUILD_ALL C2)

Files go browser to storage directly, never through a function body (4.5 MB limit). Limits: 20 MB,
10 PDF pages; JPEG, PNG, HEIC, WEBP, PDF. The API decides who may upload (`policy.py`) and returns
an upload ticket. Production: `@vercel/blob/client` `upload()` with `handleUploadUrl` pointing at a
Next.js route that forwards the user's cookie to `POST /api/v1/uploads/authorize` for the policy
decision and then calls `handleUpload` to mint the client token (the token helper exists only in
the JS SDK). Blobs are private; reads go through `GET /api/v1/receipts/{id}/view`, which checks
membership and returns a short-lived URL. Locally the same ticket flow uses MinIO presigned PUT
(`STORAGE_PROVIDER=s3`) or the fake blob service (`STORAGE_PROVIDER=fakeblob`, `PROFILE=vercel`).
The completed-upload webhook is not relied on: the client calls `POST /bills/{id}/receipts` with
the stored key, as PRD §9 already specifies. Source: vercel.com/docs/vercel-blob/client-upload.
