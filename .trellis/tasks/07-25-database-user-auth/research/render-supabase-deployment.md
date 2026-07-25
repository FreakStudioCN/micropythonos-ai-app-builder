# Render and Supabase deployment research

Research date: 2026-07-25.

## Render findings

- Render Free web services have an ephemeral filesystem. Local SQLite files,
  uploaded screenshots, generated MPK files, billing JSON, and session JSON are
  lost on restart, redeploy, or idle spin-down.
- Free web services spin down after 15 minutes without inbound traffic and can
  take about one minute to start again.
- Persistent disks are only available on paid Render services.
- `render.yaml` Blueprints support Docker services, health checks, Singapore
  region, `sync: false` secrets, and generated secret values.

Sources:

- https://render.com/docs/free
- https://render.com/docs/disks
- https://render.com/docs/blueprint-spec
- https://render.com/docs/docker

## Supabase findings

- Every project provides PostgreSQL. For a long-lived Render container, use the
  Session pooler connection string from the project Connect panel and store it
  as `DATABASE_URL`.
- Supabase Free currently provides Nano compute and a 500 MB database-size
  quota before read-only mode.
- Supabase Storage is S3 compatible. Server-side S3 access keys bypass RLS and
  must remain secret. The endpoint, region, access key ID, and secret are shown
  in Storage > Configuration > S3.
- Supabase Free Storage quota is currently 1 GB.

Sources:

- https://supabase.com/docs/guides/database/overview
- https://supabase.com/docs/guides/database/postgres-js
- https://supabase.com/docs/guides/platform/compute-and-disk
- https://supabase.com/docs/guides/platform/database-size
- https://supabase.com/docs/guides/storage/s3/compatibility
- https://supabase.com/docs/guides/storage/s3/authentication
- https://supabase.com/docs/guides/platform/manage-your-usage/storage-size

## Decision

- Render serves the built Vite frontend and FastAPI API from one Docker service.
- Supabase PostgreSQL stores registered users, login sessions, and billing.
- Supabase Storage stores session directories and generated artifacts through
  its S3-compatible server-side endpoint.
- Local development keeps SQLite and filesystem fallbacks.
- A deployment is not accepted if auth is in PostgreSQL but session/billing/
  artifact state still depends on Render's ephemeral filesystem.
