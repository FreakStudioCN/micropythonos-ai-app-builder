# PRD: Production Durable Storage

## Background

Render uses an ephemeral filesystem. Production session artifacts must be stored in Supabase Storage before the service can safely redeploy with durable storage required.

The production Supabase project already has S3 protocol support enabled and contains the `mpos-sessions` bucket.

## Requirements

- Use the existing `mpos-sessions` bucket.
- Use the direct Supabase Storage S3 endpoint.
- Store all credentials only in Render environment variables.
- Keep `MPOS_REQUIRE_DURABLE_STORAGE=true`.
- Confirm the deployed service can start with durable storage enabled.

## Acceptance Criteria

- All five `MPOS_STORAGE_*` variables are configured in Render.
- No credential is committed to the repository or written to Trellis files.
- Render deployment reaches a healthy state.
- A production artifact can be written and retrieved after a redeploy.

## Out of Scope

- Replacing Supabase Storage.
- Storing credentials in source control.
- Disabling durable storage as a workaround.

