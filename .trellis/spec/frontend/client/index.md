# Frontend Authenticated Client

Read the executable contract before changing auth state, API requests, SSE or credits UI:

- [Authenticated beta client](../authenticated-beta-client.md)

## Quality Check

- Run `npm run build` in `frontend/`.
- Protected fetch and EventSource calls must include credentials.
- The UI must not generate user IDs, grant credits or expose payment entry points.
