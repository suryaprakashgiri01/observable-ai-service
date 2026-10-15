import { request, state } from "../api.js";
import { badge, busy, clear, empty, h, kv, link } from "../dom.js";

export function mount(root) {
  const results = h("div", { class: "grid" });
  const refresh = h("button", { class: "btn", type: "button", text: "Refresh", hidden: true });

  async function check(button) {
    await busy(button, async () => {
      const [live, ready] = await Promise.all([
        request("GET", "/health", { auth: "none", label: "status" }),
        request("GET", "/health/ready", { auth: "none", label: "status" }),
      ]);
      const checks = ready.data?.checks || {};
      const providers = ready.data?.providers || {};
      clear(results,
        card("Liveness · GET /health", live.ok ? badge("ok", "ok") : badge(String(live.status), "err"),
          kv([["status", live.data?.status], ["version", live.data?.version], ["latency", `${live.ms} ms`]])),
        card("Readiness · GET /health/ready",
          ready.ok ? badge("ready", "ok") : badge(`${ready.status} not ready`, "err"),
          kv([
            ...Object.entries(checks).map(([k, v]) => [k, badge(v, v === "ok" ? "ok" : v === "disabled" ? "" : "err")]),
            ["latency", `${ready.ms} ms`],
          ])),
        card("LLM providers with a key", null,
          kv(Object.entries(providers).map(([k, v]) => [k, badge(v ? "configured" : "no key", v ? "ok" : "")]))));
      refresh.hidden = false;
    });
  }

  const first = empty("Health is not checked until you ask. No calls are made when the console opens.", "Check health", (e) => check(e.currentTarget));
  refresh.addEventListener("click", (e) => check(e.currentTarget));
  results.append(first);

  const links = state.config?.links || {};
  clear(root,
    h("div", { class: "panel-head" },
      h("div", {}, h("h2", { text: "Service status" }),
        h("p", { text: "Liveness, readiness (database, Kafka) and where to look for logs, metrics and traces." })),
      h("span", { class: "spacer" }), refresh),
    results,
    h("div", { class: "card" },
      h("h3", { text: "Observability" }),
      h("div", { class: "links" },
        link("Swagger / OpenAPI ↗", "/docs"),
        link("Grafana dashboard ↗", links.grafana && `${links.grafana}/d/observable-ai-service/observable-ai-service`),
        link("Jaeger traces ↗", links.jaeger && `${links.jaeger}/search?service=observable-ai-service`),
        link("Prometheus alerts ↗", links.prometheus && `${links.prometheus}/alerts`),
        link("Alertmanager ↗", links.alertmanager),
        link(`Langfuse session ${state.session} ↗`, links.langfuse && `${links.langfuse}/sessions/${encodeURIComponent(state.session)}`),
        state.config?.kafka_enabled ? link("Kafka UI ↗", links.kafka_ui) : null,
        link("Raw /metrics ↗", "/metrics"))));
}

function card(title, status, body) {
  return h("div", { class: "card" }, h("div", { class: "card-title" }, h("h3", { text: title }), h("span", { class: "spacer" }), status), body);
}
