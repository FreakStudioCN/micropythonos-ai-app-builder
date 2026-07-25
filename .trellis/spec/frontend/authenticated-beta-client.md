# Authenticated beta client

## Scenario: Login-gated builder UI

### 1. Scope / Trigger

- Trigger: auth form, account state, session restore, API requests, SSE, credit display or logout.
- The browser is an untrusted consumer; it displays server identity and credits but never creates
  or selects them.

### 2. Signatures

```typescript
apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response>
type AuthStatus = "loading" | "signed_out" | "signed_in"
interface BillingAccount {
  user_id: string;
  username: string;
  credits: number;
  generations_remaining: number;
  generation_limit: number;
  generation_cost: number;
  initial_credits: number;
}
```

### 3. Contracts

- `apiFetch` always sets `credentials: "include"`.
- SSE uses `new EventSource(url, {withCredentials: true})`.
- Initial load calls `/api/user`; 200 enters the builder and 401 shows login/register.
- Register/login body contains only `username` and `password`.
- Logout calls the backend, clears account/session UI state and closes live work state.
- Credit display comes from the server account payload: 50 initial, 10 per revision, five total.
- No payment, top-up, plan purchase, automatic subscription or client-side credit grant UI.

### 4. Validation & Error Matrix

| Condition | UI behavior |
| --- | --- |
| `/api/user` 401 | show signed-out auth gate |
| Register 409 | show backend username conflict message |
| Login 401 | show backend credential error |
| Generation 402 | refresh billing and show beta-credit depletion message |
| Session 404 after account switch | remove stale local session ID |
| Network failure | keep signed-out/safe state; never invent an authenticated user |

### 5. Good/Base/Bad Cases

- Good: user logs in on a new browser and restores server-owned sessions and remaining credits.
- Base: new account sees 50 credits and an empty history.
- Bad: persisting a billing `user_id` in localStorage or sending `X-MPOS-User-ID`.
- Bad: calling protected endpoints with raw `fetch` and losing cookies.

### 6. Tests Required

- TypeScript/Vite production build passes.
- Backend integration test covers register/login/Cookie/401/404 contracts.
- HTTP smoke covers register, create session, logout, login and history restore.
- Browser E2E, when added, must verify auth gate, refresh recovery and account switching.

### 7. Wrong vs Correct

#### Wrong

```typescript
fetch(apiUrl + "/api/sessions", {
  headers: {"X-MPOS-User-ID": localStorage.getItem("user") || ""},
});
```

#### Correct

```typescript
apiFetch(apiUrl + "/api/sessions");
new EventSource(eventsUrl, {withCredentials: true});
```
