# Plan: web-console

Implements [spec.md](spec.md).

```
browser ── /ui/ (StaticFiles: index.html, app.css, js/*.js) ── same origin ──▶ existing API
            js/api.js      fetch wrapper: X-API-Key, X-Session-ID, timing, request log
            js/auth.js     Authorize modal, storage, /me → auth pill
            js/panels/*.js status · ask · tickets · models · notifications · routes
```

## Decisions
- **No build, no framework, no CDN.** ES modules served as files; works offline; nothing to
  install. The trade-off is hand-written DOM code, kept small with one `h()` element helper.
- **Same origin.** No CORS configuration and no token handling beyond a header.
- **CTA-driven.** Panels never fetch on load, so opening the console creates no traffic, logs
  or metrics by itself.
- **Backend stays the source of behaviour.** The console only calls documented routes; the
  three new routes (`/me`, `/admin/users`, `GET /tickets`) are useful outside the UI too.
- **Quiet static traffic.** `/ui` is excluded from OTel and logged at DEBUG.
