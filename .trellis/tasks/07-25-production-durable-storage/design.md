# Design: Production Durable Storage

## Connection

- Region: `ap-southeast-1`
- Endpoint: `https://jzbbjpiqnbhdqykzjdxa.storage.supabase.co/storage/v1/s3`
- Bucket: `mpos-sessions`
- Addressing: S3-compatible path-style client behavior already used by the application

## Secret Handling

The access key ID and secret access key must be generated in Supabase Dashboard under Storage S3 configuration. The secret is displayed once and must be transferred directly to Render environment variables.

The saved Supabase personal access token is valid for the Management API, but the official Management API OpenAPI schema contains no S3 credential endpoint. Supabase Studio uses an internal endpoint that requires a Dashboard user JWT and rejects a PAT.

## Safe Rollout

Apply all storage variables together. Do not trigger a Render redeploy with only the non-secret variables present because durable storage is already mandatory.

