// Every call the console makes goes through here: auth and session headers, timing,
// and an entry in the request log.

import { randomHex } from "./dom.js";

const listeners = new Set();
const KEY = "oas.apiKey";
const SESSION = "oas.sessionId";

export const state = {
  key: sessionStorage.getItem(KEY) || localStorage.getItem(KEY) || "",
  remembered: Boolean(localStorage.getItem(KEY)),
  session: sessionStorage.getItem(SESSION) || `ui-${randomHex(3)}`,
  config: null,
};
sessionStorage.setItem(SESSION, state.session);

export function setKey(key, remember) {
  state.key = key;
  state.remembered = remember;
  sessionStorage.removeItem(KEY);
  localStorage.removeItem(KEY);
  if (key) (remember ? localStorage : sessionStorage).setItem(KEY, key);
}

export function setSession(session) {
  state.session = session;
  sessionStorage.setItem(SESSION, session);
}

export function onCall(fn) { listeners.add(fn); }

/**
 * request("GET", "/models", { auth: "mine" | "none" | "bad" | "admin-key-string",
 *   headers, body, query, label })
 * Resolves with { status, ok, data, headers, ms, requestId, traceId, sent } and never throws
 * for HTTP errors (status 0 means the network call itself failed).
 */
export async function request(method, path, opts = {}) {
  const { auth = "mine", headers = {}, body, query, label } = opts;
  const url = new URL(path, window.location.origin);
  for (const [k, v] of Object.entries(query || {})) if (v !== "" && v !== undefined && v !== null) url.searchParams.set(k, v);

  const sent = { "X-Session-ID": state.session, ...headers };
  if (auth === "mine" && state.key) sent["X-API-Key"] = state.key;
  else if (auth === "bad") sent["X-API-Key"] = "oas_not_a_real_key";
  else if (auth && !["mine", "none", "bad"].includes(auth)) sent["X-API-Key"] = auth;
  for (const [k, v] of Object.entries(sent)) if (v === "" || v === undefined) delete sent[k];
  if (body !== undefined) sent["Content-Type"] = "application/json";

  const started = performance.now();
  let status = 0;
  let data = null;
  let responseHeaders = new Headers();
  try {
    const res = await fetch(url, {
      method,
      headers: sent,
      body: body === undefined ? undefined : typeof body === "string" ? body : JSON.stringify(body),
    });
    status = res.status;
    responseHeaders = res.headers;
    const text = await res.text();
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  } catch (err) {
    data = { error: { code: "NETWORK_ERROR", message: String(err) } };
  }
  const result = {
    method,
    path: url.pathname + url.search,
    status,
    ok: status >= 200 && status < 300,
    data,
    headers: responseHeaders,
    ms: Math.round(performance.now() - started),
    requestId: responseHeaders.get("x-request-id") || data?.request_id || data?.error?.request_id || null,
    traceId: data?.trace_id || null,
    sent: maskKey(sent),
    body,
    label,
    at: new Date(),
  };
  for (const fn of listeners) fn(result);
  return result;
}

function maskKey(headers) {
  const copy = { ...headers };
  if (copy["X-API-Key"]) copy["X-API-Key"] = `${copy["X-API-Key"].slice(0, 10)}…`;
  return copy;
}

/** A curl command for the same call; the key is written as $API_KEY, never the real value. */
export function curl(method, path, { headers = {}, body, auth = "mine", query } = {}) {
  const url = new URL(path, window.location.origin);
  for (const [k, v] of Object.entries(query || {})) if (v !== "" && v != null) url.searchParams.set(k, v);
  const lines = [`curl --location --request ${method} '${url.href}'`];
  if (auth === "mine") lines.push(`  --header "X-API-Key: $API_KEY"`);
  if (auth === "bad") lines.push(`  --header 'X-API-Key: oas_not_a_real_key'`);
  const all = { "X-Session-ID": state.session, ...headers };
  if (body !== undefined) all["Content-Type"] = "application/json";
  for (const [k, v] of Object.entries(all)) if (v !== "" && v != null) lines.push(`  --header '${k}: ${v}'`);
  if (body !== undefined) {
    const raw = typeof body === "string" ? body : JSON.stringify(body);
    lines.push(`  --data '${raw.replaceAll("'", "'\\''")}'`);
  }
  return lines.join(" \\\n");
}

export function jaegerUrl(traceId) {
  return traceId && state.config ? `${state.config.links.jaeger}/trace/${traceId}` : null;
}

export function langfuseSessionUrl(session) {
  return state.config?.links.langfuse && session ? `${state.config.links.langfuse}/sessions/${encodeURIComponent(session)}` : null;
}

export function grafanaPanel(id) {
  return state.config ? `${state.config.links.grafana}/d/observable-ai-service/observable-ai-service?viewPanel=${id}&from=now-15m&to=now` : null;
}
