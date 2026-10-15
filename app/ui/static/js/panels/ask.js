// The /ask request builder: every option of the API, a live preview, and the result.

import { curl, grafanaPanel, jaegerUrl, langfuseSessionUrl, request, state } from "../api.js";
import { badge, busy, clear, h, json, kv, link, randomHex, statusBadge, toast } from "../dom.js";

const PRESETS = [
  ["Ticket · high", "Create a high priority ticket: checkout page is slow"],
  ["Ticket · critical", "Open a critical incident: payments API returns 500s"],
  ["Ticket · low", "Create a low priority ticket: typo on the pricing page"],
  ["Revenue · 7 days", "Give me a revenue report for the last 7 days"],
  ["Orders · 30 days", "How many orders in the last 30 days?"],
  ["Avg order value · quarter", "What is the average order value this quarter?"],
  ["General", "What can you do?"],
  ["Empty → 422", ""],
  ["Too long → 422", "a".repeat(2001)],
];
const FAULTS = [["", "No fault"], ["llm_timeout", "llm_timeout: timeouts → retries → fallback"], ["llm_error", "llm_error: permanent error → fallback, no retry"], ["tool_error", "tool_error: business action fails → 502"]];
const TERMINAL = new Set(["completed", "failed"]);

export function mount(root) {
  let mode = "sync";
  let polling = null;

  // --- inputs -------------------------------------------------------------------------
  const modeBtns = ["sync", "async"].map((m) => h("button", {
    type: "button", text: m === "sync" ? "Sync · 200" : "Async · 202 + job",
    "aria-pressed": String(m === mode), on: { click: () => { mode = m; modeBtns.forEach((b, i) => b.setAttribute("aria-pressed", String(["sync", "async"][i] === m))); update(); } },
  }));
  const question = h("textarea", { rows: 3, placeholder: "Ask something… (empty sends an empty question → 422)" });
  question.value = PRESETS[0][1];
  const presets = h("div", { class: "chips" }, PRESETS.map(([label, text]) => h("button", {
    class: "chip", type: "button", text: label, on: { click: () => { question.value = text; update(); } },
  })));

  const placement = radios("placement", [["none", "No model (use my default)"], ["header", "X-Model-Id header"], ["body", "body model_id"], ["both", "Both (header wins)"]], "none");
  const modelSelect = h("select", {}, h("option", { value: "", text: "— load models first —" }));
  const modelText = h("input", { placeholder: "or type any id (unknown → 400)", spellcheck: "false" });
  const bodyModelText = h("input", { placeholder: "body model_id when “Both” (optional)", spellcheck: "false" });
  const loadModels = h("button", { class: "btn btn-sm", type: "button", text: "Load models" });

  const requestId = h("input", { placeholder: "blank = server generates", spellcheck: "false" });
  const sessionId = h("input", { spellcheck: "false" });
  sessionId.value = state.session;
  const traceMode = h("select", {}, h("option", { value: "", text: "No traceparent (new trace)" }), h("option", { value: "gen", text: "Generate one" }), h("option", { value: "custom", text: "Custom…" }));
  const traceparent = h("input", { placeholder: "00-<32 hex>-<16 hex>-01", spellcheck: "false", hidden: true });

  const fault = h("select", {}, FAULTS.map(([v, t]) => h("option", { value: v, text: t })));
  const faultCount = h("input", { type: "number", min: 1, max: 5, value: 1, class: "count-input" });
  const authMode = radios("authmode", [["mine", "My key"], ["none", "No key → 401"], ["bad", "Bad key → 401"]], "mine");

  const preview = h("pre", { class: "preview" });
  const copyBtn = h("button", { class: "btn btn-sm", type: "button", text: "Copy as curl" });
  const sendBtn = h("button", { class: "btn btn-primary cta", type: "button", text: "Send" });
  const result = h("div", { class: "panel" }, h("div", { class: "empty", text: "Build a request on the left and press Send. Nothing is sent until then." }));

  // --- request assembly ------------------------------------------------------------------
  function chosenModel() { return modelText.value.trim() || modelSelect.value; }

  function build() {
    const headers = {};
    const body = { question: question.value };
    const where = placement.value();
    const model = chosenModel();
    if (model && (where === "header" || where === "both")) headers["X-Model-Id"] = model;
    if (where === "body" && model) body.model_id = model;
    if (where === "both") body.model_id = bodyModelText.value.trim() || model;
    if (requestId.value.trim()) headers["X-Request-ID"] = requestId.value.trim();
    headers["X-Session-ID"] = sessionId.value.trim() || state.session;
    if (traceMode.value && traceparent.value.trim()) headers.traceparent = traceparent.value.trim();
    if (fault.value) {
      headers["X-Debug-Fault"] = fault.value;
      if (Number(faultCount.value) > 1) headers["X-Debug-Fault-Count"] = String(faultCount.value);
    }
    if (mode === "async") headers.Prefer = "respond-async";
    return { headers, body, auth: authMode.value() };
  }

  function update() {
    traceparent.hidden = !traceMode.value;
    faultCount.disabled = !fault.value;
    bodyModelText.hidden = placement.value() !== "both";
    const { headers, body, auth } = build();
    preview.textContent = curl("POST", "/ask", { headers, body: { ...body, question: body.question.length > 120 ? `${body.question.slice(0, 120)}… (${body.question.length} chars)` : body.question }, auth });
  }

  for (const el of [question, modelSelect, modelText, bodyModelText, requestId, sessionId, traceparent, fault, faultCount]) el.addEventListener("input", update);
  traceMode.addEventListener("change", () => {
    traceparent.value = traceMode.value === "gen" ? `00-${randomHex(16)}-${randomHex(8)}-01` : "";
    update();
  });
  placement.onChange(update);
  authMode.onChange(update);

  loadModels.addEventListener("click", (e) => busy(e.currentTarget, async () => {
    const r = await request("GET", "/models", { label: "ask: models" });
    if (!r.ok) return toast(`Could not load models: ${r.status} ${r.data?.error?.code || ""}`);
    const byTier = { economy: [], standard: [], premium: [] };
    for (const m of r.data.items) (byTier[m.tier] ||= []).push(m);
    clear(modelSelect, h("option", { value: "", text: "— choose a model —" }),
      Object.entries(byTier).map(([tier, items]) => h("optgroup", { label: tier },
        items.map((m) => h("option", { value: m.id, text: `${m.id}${m.available ? "" : " (no key → 400)"}${m.is_default ? " · default" : ""}` })))));
    toast(`${r.data.total} models loaded`);
  }));

  copyBtn.addEventListener("click", async () => {
    const { headers, body, auth } = build();
    try { await navigator.clipboard.writeText(curl("POST", "/ask", { headers, body, auth })); toast("curl copied ($API_KEY placeholder)"); }
    catch { toast("Clipboard blocked: select the preview and copy"); }
  });

  sendBtn.addEventListener("click", (e) => busy(e.currentTarget, async () => {
    stopPolling();
    const { headers, body, auth } = build();
    const r = await request("POST", "/ask", { headers, body, auth, label: mode === "async" ? "ask async" : "ask" });
    if (r.status === 202) return showAccepted(r);
    showResult(r);
  }));

  // --- results ---------------------------------------------------------------------------
  function showResult(r) {
    if (!r.ok) {
      const err = r.data?.error || {};
      return clear(result, h("div", { class: "card" },
        h("div", { class: "card-title" }, statusBadge(r.status), h("strong", { text: err.code || "Error" }), h("span", { class: "spacer" }), h("span", { class: "muted", text: `${r.ms} ms` })),
        h("p", { text: err.message || "" }),
        kv([["request id", err.request_id || r.requestId]]),
        err.details ? [h("h3", { text: "Validation details" }), json(err.details)] : null,
        hint(r.status, err.code)));
    }
    clear(result, answerCard(r.data, r));
  }

  function answerCard(d, r) {
    const m = d.model;
    return h("div", { class: "card" },
      h("div", { class: "card-title" },
        statusBadge(r ? r.status : 200), badge(d.intent, "info"),
        m.fallback_used ? badge("fallback used", "warn") : badge("no fallback", "ok"),
        h("span", { class: "spacer" }), h("span", { class: "muted", text: `${d.latency_ms} ms` })),
      h("p", { text: d.answer }),
      d.action ? actionView(d.action) : null,
      h("h3", { text: "Model" }),
      kv([
        ["requested → used", `${m.requested} → ${m.used}`],
        ["source", badge(m.source, "info")],
        ["provider / tier", `${m.provider} / ${m.tier}`],
        ["attempts", String(m.attempts)],
        ["chain", h("div", { class: "chain" }, m.chain.map((id, i) => [i ? h("span", { class: "arrow", text: "→" }) : null, badge(id, i === m.chain.length - 1 ? "ok" : "err")]))],
      ]),
      h("h3", { text: "Trace it" }),
      kv([["request id", d.request_id], ["session id", d.session_id], ["trace id", d.trace_id]]),
      h("div", { class: "links" },
        link("Jaeger trace ↗", jaegerUrl(d.trace_id)),
        link("Langfuse session ↗", langfuseSessionUrl(d.session_id)),
        m.fallback_used ? link("Grafana: retries & fallbacks ↗", grafanaPanel(30)) : null));
  }

  function actionView(action) {
    if (action.type === "ticket_created") {
      const t = action.ticket;
      return h("div", { class: "card" }, h("h3", { text: "Ticket created" }),
        kv([["id", t.id], ["title", t.title], ["priority", badge(t.priority, t.priority === "critical" ? "err" : t.priority === "high" ? "warn" : "")], ["status", t.status]]));
    }
    if (action.type === "report") {
      const rep = action.report;
      return h("div", { class: "card" }, h("h3", { text: `Report: ${rep.metric.replaceAll("_", " ")} · last ${rep.period_days} days` }),
        kv([["value", String(rep.value)], ["orders counted", String(rep.order_count)], ["refunds excluded", String(rep.excluded_refunded)]]),
        h("div", { class: "split" },
          h("table", {}, h("thead", {}, h("tr", {}, h("th", { text: "Region" }), h("th", { text: "Value" }))),
            h("tbody", {}, Object.entries(rep.by_region).map(([k, v]) => h("tr", {}, h("td", { text: k }), h("td", { text: String(v) }))))),
          h("table", {}, h("thead", {}, h("tr", {}, h("th", { text: "Top product" }), h("th", { text: "Revenue" }))),
            h("tbody", {}, rep.top_products.map((p) => h("tr", {}, h("td", { text: p.product }), h("td", { text: String(p.revenue) })))))));
    }
    return json(action);
  }

  function showAccepted(r) {
    const job = r.data;
    const timeline = h("ol", { class: "timeline" });
    const statusLine = h("div", { class: "card-title" }, statusBadge(202), badge("queued", "info"), h("strong", { text: `job ${job.job_id}` }));
    const final = h("div", {});
    const stop = h("button", { class: "btn btn-sm", type: "button", text: "Stop polling", on: { click: stopPolling } });
    clear(result, h("div", { class: "card" },
      statusLine,
      h("p", { class: "muted", text: `Accepted at once. Polling ${job.status_url} every 0.7 s to watch the job in flight.` }),
      h("div", { class: "links" }, link("Jaeger trace ↗", jaegerUrl(job.trace_id)), link("Grafana: job transitions ↗", grafanaPanel(40)),
        state.config?.kafka_enabled ? link("Kafka UI: ask-status ↗", `${state.config.links.kafka_ui}/ui/clusters/local/all-topics/ai-service.ask-status/messages`) : null, stop),
      h("h3", { text: "Status timeline" }), timeline), final);

    let seen = 0;
    const tick = async () => {
      const p = await request("GET", job.status_url, { label: "job poll" });
      if (!p.ok) { stopPolling(); return clear(final, h("div", { class: "card" }, statusBadge(p.status), json(p.data))); }
      const events = p.data.events || [];
      for (const ev of events.slice(seen)) {
        const tone = ev.status === "completed" ? "ok" : ev.status === "failed" ? "err" : ev.status === "retrying" || ev.status === "fallback" ? "warn" : "info";
        const extra = [ev.model_id && `model ${ev.model_id}`, ev.attempt && `attempt ${ev.attempt}`, ev.from_model && `from ${ev.from_model}`, ev.reason && `reason ${ev.reason}`].filter(Boolean).join(" · ");
        timeline.append(h("li", {}, h("time", { text: new Date(ev.at).toLocaleTimeString() }), badge(ev.status, tone), h("span", { text: extra })));
      }
      seen = events.length;
      clear(statusLine, statusBadge(202), badge(p.data.status, TERMINAL.has(p.data.status) ? (p.data.status === "completed" ? "ok" : "err") : "info"),
        h("strong", { text: `job ${job.job_id}` }), h("span", { class: "spacer" }), h("span", { class: "muted", text: `attempts ${p.data.attempts} · ${p.data.current_model || "—"}` }));
      if (TERMINAL.has(p.data.status)) {
        stopPolling();
        if (p.data.result) clear(final, answerCard(p.data.result, { status: 200 }));
        else clear(final, h("div", { class: "card" }, h("div", { class: "card-title" }, badge("failed", "err"), h("strong", { text: p.data.error?.code || "" })), h("p", { text: p.data.error?.message || "" })));
      }
    };
    polling = setInterval(tick, 700);
    tick();
  }

  function stopPolling() { if (polling) { clearInterval(polling); polling = null; } }

  // --- layout ------------------------------------------------------------------------------
  const faultBox = state.config?.fault_injection
    ? h("div", { class: "row" }, h("label", { class: "field" }, h("span", { text: "X-Debug-Fault" }), fault), h("label", { class: "field" }, h("span", { text: "X-Debug-Fault-Count (models that fail)" }), faultCount))
    : h("p", { class: "muted", text: "Fault injection is off (ENABLE_FAULT_INJECTION=false), so X-Debug-Fault would be ignored." });

  clear(root,
    h("div", { class: "panel-head" },
      h("div", {}, h("h2", { text: "Ask" }), h("p", { text: "Build any /ask request: sync or async, model by header or body, ids, trace context, faults, and with or without a key." }))),
    h("div", { class: "split" },
      h("div", { class: "panel" },
        h("div", { class: "card" }, h("h3", { text: "Mode" }), h("div", { class: "segmented" }, modeBtns),
          h("p", { class: "muted", text: "Async sends Prefer: respond-async: you get 202 and a job id, then the console polls GET /ask/jobs/{id}." })),
        h("div", { class: "card" }, h("h3", { text: "Question" }), question, h("div", { class: "muted", text: "Presets fill the field:" }), presets),
        h("div", { class: "card" }, h("div", { class: "card-title" }, h("h3", { text: "Model" }), h("span", { class: "spacer" }), loadModels),
          placement.el, h("div", { class: "row" }, h("label", { class: "field" }, h("span", { text: "Model" }), modelSelect), h("label", { class: "field" }, h("span", { text: "Override" }), modelText)), bodyModelText,
          h("p", { class: "muted", text: "Order: X-Model-Id → body model_id → your default → global default. The chain then falls back: your fallback → LLM_FALLBACK_CHAIN → mock-echo." })),
        h("div", { class: "card" }, h("h3", { text: "Correlation headers" }),
          h("div", { class: "row" },
            h("label", { class: "field" }, h("span", { text: "X-Request-ID" }), requestId),
            h("button", { class: "btn btn-sm", type: "button", text: "Generate", on: { click: () => { requestId.value = `ui-${randomHex(4)}`; update(); } } })),
          h("div", { class: "row" }, h("label", { class: "field" }, h("span", { text: "X-Session-ID" }), sessionId)),
          h("div", { class: "row" }, h("label", { class: "field" }, h("span", { text: "traceparent" }), traceMode), traceparent)),
        h("div", { class: "card" }, h("h3", { text: "Fault injection" }), faultBox),
        h("div", { class: "card" }, h("h3", { text: "Auth for this call" }), authMode.el,
          h("p", { class: "muted", text: "Try 401s without logging out. No key → AUTH_REQUIRED, bad key → AUTH_INVALID (both alert as Http4xxAuthErrors)." })),
        h("div", { class: "card" }, h("div", { class: "card-title" }, h("h3", { text: "Request preview" }), h("span", { class: "spacer" }), copyBtn), preview,
          h("div", { class: "row" }, h("span", { class: "spacer" }), sendBtn))),
      h("div", { class: "panel" }, h("h3", { text: "Result" }), result)));
  update();
}

function radios(name, options, initial) {
  const listeners = [];
  const el = h("div", { class: "radio-row" }, options.map(([value, label]) => h("label", {},
    h("input", { type: "radio", name, value, checked: value === initial, on: { change: () => listeners.forEach((fn) => fn()) } }),
    h("span", { text: label }))));
  return {
    el,
    value: () => el.querySelector("input:checked")?.value,
    onChange: (fn) => listeners.push(fn),
  };
}

function hint(status, code) {
  const tips = {
    AUTH_REQUIRED: "No X-API-Key was sent. Counted in auth_failures_total{reason=\"missing\"}; fires Http4xxAuthErrors.",
    AUTH_INVALID: "The key is unknown, revoked or its user inactive. Fires Http4xxAuthErrors.",
    VALIDATION_ERROR: "Rejected before any LLM call: costs nothing, logged and counted, no alert.",
    MODEL_NOT_FOUND: "An explicitly requested model must exist (GET /models).",
    MODEL_UNAVAILABLE: "The model exists but its provider has no key in .env.",
    TOOL_FAILED: "The business action failed after the LLM answered. Fires Http5xxErrors.",
  };
  return tips[code] ? h("p", { class: "muted", text: tips[code] }) : (status >= 500 ? h("p", { class: "muted", text: "5xx: fires Http5xxErrors; see the request id in the logs." }) : null);
}
