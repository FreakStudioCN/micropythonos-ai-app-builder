# Closed-beta auth, credits and durable deployment

## Scenario: Username/password beta accounts on Render + Supabase

### 1. Scope / Trigger

- Trigger: changes to auth APIs, user ownership, beta credits, database schema, session/artifact
  persistence, cookies, CORS, Docker or Render environment wiring.
- This contract prevents client-selected identities and prevents Render Free restarts from
  silently deleting users or generated artifacts.

### 2. Signatures

APIs:

```text
POST /api/auth/register  AuthCredentials -> BillingAccount (201)
POST /api/auth/login     AuthCredentials -> BillingAccount (200)
POST /api/auth/logout    cookie -> {status: "logged_out"}
GET  /api/user           cookie -> BillingAccount
GET  /api/billing/account cookie -> BillingAccount
```

Database tables:

```text
app_users(id, username, username_normalized UNIQUE, password_hash, created_at)
app_login_sessions(token_hash PK, user_id, created_at, last_seen_at, expires_at)
app_billing_accounts(user_id PK, credits, created_at, updated_at)
app_billing_ledger(id PK, user_id, idempotency_key, entry_type, amount, created_at,
                   UNIQUE(user_id, idempotency_key))
```

### 3. Contracts

- `AuthCredentials.username`: 3-32 characters; letters/numbers/`_`/`-`; uniqueness uses
  `casefold()`.
- `AuthCredentials.password`: 8-128 characters; store only salted scrypt output.
- Cookie name is `mpos_session`; it is HttpOnly, 30 days, `SameSite=lax`, and Secure on HTTPS.
- Store only SHA-256 of the random login token in `app_login_sessions`.
- New account: 50 credits. New revision: 10 credits. Idempotency key is
  `generation:{session_id}:{revision_id}`. There is no purchase, recharge or subscription API.
- Every session state has `owner_user_id`; session/artifact/permission/billing identity comes
  only from authenticated request state.
- Local: SQLite + filesystem. Cloud: Supabase PostgreSQL + private S3-compatible Storage.
- Required cloud environment:

```text
DATABASE_URL
MPOS_STORAGE_ENDPOINT
MPOS_STORAGE_REGION
MPOS_STORAGE_ACCESS_KEY_ID
MPOS_STORAGE_SECRET_ACCESS_KEY
MPOS_STORAGE_BUCKET
MPOS_REQUIRE_DURABLE_STORAGE=true
MPOS_COOKIE_SECURE=true
```

### 4. Validation & Error Matrix

| Condition | Result |
| --- | --- |
| Missing/expired login cookie | 401 `请先登录` |
| Duplicate normalized username | 409 `用户名已存在` |
| Wrong username/password | 401 `用户名或密码错误` |
| Invalid username/password shape | 400 |
| Cross-user session/artifact/permission | 404 `资源不存在` |
| Credits below 10 | 402 `INSUFFICIENT_CREDITS` |
| Partial `MPOS_STORAGE_*` configuration | startup `RuntimeError` |
| Durable mode with SQLite or disabled object storage | startup `RuntimeError` |

> CORS must be the outer middleware around auth so middleware-generated 401/404 responses keep
> `Access-Control-Allow-Origin` during local frontend development.

### 5. Good/Base/Bad Cases

- Good: Render uses Supabase Session pooler URI and private S3 bucket; `/api/health` reports
  `postgresql`, object storage enabled, and durable storage required.
- Base: local development has no cloud variables and uses ignored SQLite/session files.
- Bad: trusting `X-MPOS-User-ID`, query `user_id`, or request payload user ID for ownership or
  charging.
- Bad: enabling durable production mode while relying on `/tmp`, `/data`, or container SQLite.

### 6. Tests Required

- Registration asserts raw password and raw token are absent from database rows.
- Login asserts correct/wrong password, case-insensitive duplicate name and logout invalidation.
- Two-user test asserts isolated lists plus 404 for session/artifact/permission.
- Billing asserts 50 initial, 10 charge, five generations, sixth rejected, same revision charged
  once, and header/query spoofing ignored.
- Storage asserts round trip, unchanged-file cache, traversal rejection and partial-env failure.
- CORS test asserts unauthenticated `/api/user` still returns local origin and credentials headers.
- Deployment validation includes full unit suite, frontend build, Docker build and HTTP smoke.

### 7. Wrong vs Correct

#### Wrong

```python
user_id = request.headers.get("X-MPOS-User-ID")
billing_service.consume_generation(user_id, key)
```

#### Correct

```python
user_id = request.state.user_id
session_service.require_owner(session_id, user_id)
billing_service.consume_generation(user_id, f"generation:{session_id}:{revision_id}")
```
