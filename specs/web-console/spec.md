# Spec: web-console

Status: approved · Owner: Surya Prakash Giri · Ticket: none (personal repo; see `tasks.md`)

## Problem
Exercising the API means hand-writing curls or clicking through Swagger for every case. There is
no single place to authorize once, see whether the key works, try every `/ask` variant (sync,
async, model, faults, headers) and watch the responses, and no route returns 403 at all.

## Goals
- A lightweight console at **`/ui`**, served by the app: plain HTML/CSS/JS, no build step, no CDN.
- **Authorize** (paste a key, optionally remembered) and an always-visible auth state:
  authorized as *email (role)* / not authorized (401 code) / no key.
- **Nothing loads by itself**: every panel has a CTA button that fetches its data.
- An **Ask request builder** covering every case: sync/async toggle, question and presets, model
  via header / body / both, request id, session id, traceparent, fault + count, per-call auth
  override (my key / no key / bad key), request preview and "Copy as curl".
- Panels for status, tickets, models (table, filters, default/fallback, BYOM), notifications, and
  route/auth checks (401, 403, webhook 401), plus a request log of every call.
- A real **403**: admin-only `GET /admin/users`.

## Non-goals
User management, editing users, SSO, a JS framework, a separate frontend service.

## API additions

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/me` | key | Who the key belongs to: `{id, email, name, role, key_prefix, key_last_used_at}` |
| GET | `/admin/users` | key, **admin** | Users with role, active, key count, last used. 403 `FORBIDDEN` for non-admins |
| GET | `/tickets?limit=` | key | The caller's newest tickets (1–100, default 20) |
| GET | `/ui/config` | none | Public console config: version, fault injection on/off, Kafka on/off, observability URLs. No secrets |
| GET | `/ui/` | none | The console (static files) |
| GET | `/` | none | Redirect to `/ui/` |

A 403 counts in `auth_failures_total{reason="forbidden"}` and fires `Http4xxAuthErrors`.

## Security
- The key stays in the browser (`sessionStorage`; `localStorage` only if "remember" is ticked),
  sent only as `X-API-Key` to the same origin.
- `/ui` responses carry a strict CSP (`default-src 'self'`) and `X-Content-Type-Options: nosniff`.
- `/ui/config` exposes only public URLs and flags.

## Acceptance criteria
1. `GET /me` → 200 with the caller; 401 without or with a bad key. *(test)*
2. `GET /admin/users` → 403 `FORBIDDEN` for a user, 200 for an admin, 401 without a key. *(test)*
3. `GET /tickets` lists only the caller's tickets, newest first. *(test)*
4. `/ui/` and `/ui/config` are served without a key; config contains no secret; CSP set. *(test)*
5. The console loads no data until a CTA is pressed; the auth pill reflects 200 / 401 from `/me`;
   every Ask option maps to the request sent (verified in the browser and with Copy as curl). *(manual)*
6. Static assets do not appear in traces or in the INFO access log. *(test + manual)*
