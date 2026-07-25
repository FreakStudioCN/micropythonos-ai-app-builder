# Frontend Code-Spec

## Required contracts

- [Authenticated API client and beta UI](authenticated-beta-client.md)

## Quality Check

- Run `npm run build` in `frontend/`.
- Search for direct `fetch` calls; authenticated API calls must use the credentialed wrapper.
- Confirm there is no client-generated billing user ID and no payment/purchase entry.
