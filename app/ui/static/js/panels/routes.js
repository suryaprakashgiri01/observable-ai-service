// One card per route or auth case: nothing is called until you press Send.

import { curl, request, state } from "../api.js";
import { badge, busy, clear, h, json, randomHex, statusBadge, toast } from "../dom.js";
import { checkMe } from "../auth.js";

const CASES = [
  { group: "Auth", method: "GET", path: "/me", auth: "mine", title: "Who am I (my key)", expect: "200 with your email and role; 401 if the key is wrong" },
  { group: "Auth", method: "GET", path: "/me", auth: "none", title: "No key", expect: "401 AUTH_REQUIRED · fires Http4xxAuthErrors" },
  { group: "Auth", method: "GET", path: "/me", auth: "bad", title: "Unknown key", expect: "401 AUTH_INVALID · fires Http4xxAuthErrors" },
  { group: "Auth", method: "GET", path: "/admin/users", auth: "mine", title: "Admin only, with my key", expect: "403 FORBIDDEN for a user key, 200 for an admin key · 403 fires Http4xxAuthErrors" },
  { group: "Auth", method: "GET", path: "/admin/users", auth: "admin", title: "Admin only, with an admin key", expect: "200: paste SEED_ADMIN_API_KEY below", needsAdminKey: true },
  { group: "Auth", method: "POST", path: "/alerts/webhook", auth: "none", title: "Webhook with a wrong token", expect: "401 WEBHOOK_UNAUTHORIZED (the webhook takes Alertmanager's bearer token, not X-API-Key)", headers: { Authorization: "Bearer wrong" }, body: { status: "firing", alerts: [] } },
  { group: "Health", method: "GET", path: "/health", auth: "none", title: "Liveness", expect: "200, no key needed" },
  { group: "Health", method: "GET", path: "/health/ready", auth: "none", title: "Readiness", expect: "200 ready, or 503 if Postgres is down" },
  { group: "Health", method: "GET", path: "/ui/config", auth: "none", title: "Console config", expect: "200; public flags and links, no secrets" },
  { group: "Errors", method: "GET", path: "/tickets/{random-uuid}", auth: "mine", title: "Unknown ticket", expect: "404 TICKET_NOT_FOUND" },
  { group: "Errors", method: "GET", path: "/tickets/abc", auth: "mine", title: "Ticket id not a UUID", expect: "422 VALIDATION_ERROR (uuid_parsing) · logged, no alert" },
  { group: "Errors", method: "GET", path: "/models", auth: "mine", query: { tier: "gold" }, title: "Bad enum in a query", expect: "422 VALIDATION_ERROR" },
  { group: "Errors", method: "GET", path: "/ask/jobs/{random-uuid}", auth: "mine", title: "Unknown async job", expect: "404 JOB_NOT_FOUND" },
  { group: "Errors", method: "GET", path: "/nope", auth: "none", title: "Unknown route", expect: "404 NOT_FOUND (route label <unmatched>)" },
];

export function mount(root) {
  const adminKey = h("input", { type: "password", placeholder: "SEED_ADMIN_API_KEY (kept in memory only)", spellcheck: "false", size: 36 });
  const groups = {};
  for (const c of CASES) (groups[c.group] ||= []).push(c);

  clear(root,
    h("div", { class: "panel-head" },
      h("div", {}, h("h2", { text: "Routes & auth checks" }),
        h("p", { text: "One button per case. Use these to see 401, 403, 404 and 422 on purpose, with the exact response." }))),
    h("div", { class: "card row" },
      h("label", { class: "field" }, h("span", { text: "Admin key for the admin-only case" }), adminKey),
      h("span", { class: "muted", text: "Your own key is the one set with Authorize. 401 and 403 alert; 404 and 422 do not." })),
    Object.entries(groups).map(([group, cases]) => [h("h3", { text: group }), h("div", { class: "grid" }, cases.map((c) => caseCard(c, adminKey)))]));
}

function caseCard(c, adminKey) {
  const out = h("div", {});
  const send = h("button", { class: "btn btn-primary btn-sm", type: "button", text: "Send" });
  const copy = h("button", { class: "btn btn-sm", type: "button", text: "Copy curl" });

  function resolved() {
    const path = c.path.replaceAll("{random-uuid}", crypto.randomUUID ? crypto.randomUUID() : `${randomHex(4)}-0000-4000-8000-${randomHex(6)}`);
    let auth = c.auth;
    if (c.needsAdminKey) auth = adminKey.value.trim() || "none";
    return { path, auth };
  }

  send.addEventListener("click", (e) => busy(e.currentTarget, async () => {
    if (c.needsAdminKey && !adminKey.value.trim()) return toast("Paste the admin key first");
    if (c.auth === "mine" && !state.key) toast("No key set: this will be a 401 (use Authorize)");
    const { path, auth } = resolved();
    const r = await request(c.method, path, { auth, headers: c.headers, body: c.body, query: c.query, label: "routes" });
    if (c.path === "/me" && c.auth === "mine") await checkMe({ quiet: true });
    clear(out,
      h("div", { class: "card-title" }, statusBadge(r.status), h("span", { class: "mono", text: `${r.method} ${r.path}` }), h("span", { class: "spacer" }), h("span", { class: "muted", text: `${r.ms} ms` })),
      typeof r.data === "string" ? h("pre", { class: "json", text: r.data.slice(0, 1500) }) : json(r.data));
  }));
  copy.addEventListener("click", async () => {
    const { path, auth } = resolved();
    const text = curl(c.method, path, { auth: auth === "mine" || auth === "bad" ? auth : "none", headers: c.headers, body: c.body, query: c.query });
    try { await navigator.clipboard.writeText(text); toast("curl copied"); } catch { toast("Clipboard blocked"); }
  });

  return h("div", { class: "card" },
    h("div", { class: "card-title" }, badge(c.method, "info"), h("span", { class: "mono", text: c.path }), h("span", { class: "spacer" }), copy, send),
    h("strong", { text: c.title }),
    h("p", { class: "muted", text: `Expect: ${c.expect}` }),
    out);
}
