# Backend Security and Persistence

Read the executable contract before changing auth, ownership, credits, database or deployment:

- [Closed-beta auth, credits and durable deployment](../auth-and-durable-beta.md)

## Quality Check

- Run the full backend unit suite and `git diff --check`.
- Assert missing login is 401 and cross-user resources are 404.
- Assert Render durable mode requires both PostgreSQL and S3-compatible storage.
