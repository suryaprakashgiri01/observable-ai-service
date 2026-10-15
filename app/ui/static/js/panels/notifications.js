import { grafanaPanel, request, state } from "../api.js";
import { badge, busy, clear, empty, h, link, time } from "../dom.js";

export function mount(root) {
  const list = h("div", { class: "panel" });
  const statusFilter = h("select", {}, h("option", { value: "", text: "All statuses" }), h("option", { value: "firing", text: "Firing" }), h("option", { value: "resolved", text: "Resolved" }));
  const unreadOnly = h("input", { type: "checkbox" });
  const auto = h("input", { type: "checkbox" });
  const fetchBtn = h("button", { class: "btn", type: "button", text: "Refresh", hidden: true });
  let timer = null;

  async function load(button) {
    const run = async () => {
      const r = await request("GET", "/notifications", {
        query: { status: statusFilter.value, unread: unreadOnly.checked ? "true" : "", limit: 50 },
        label: "notifications",
      });
      fetchBtn.hidden = false;
      if (!r.ok) return clear(list, h("div", { class: "card" }, badge(`${r.status} ${r.data?.error?.code || ""}`, "err"), " ", r.data?.error?.message || ""));
      const counter = document.getElementById("unread-count");
      counter.hidden = !r.data.unread;
      counter.textContent = String(r.data.unread);
      if (!r.data.items.length) return clear(list, h("div", { class: "empty", text: "No notifications match. Cause a 502 (Ask → tool_error) or a 401, wait ~30 s, fetch again." }));
      clear(list, r.data.items.map((n) => item(n, () => load(fetchBtn))));
    };
    return button ? busy(button, run) : run();
  }

  auto.addEventListener("change", () => {
    clearInterval(timer);
    if (auto.checked) timer = setInterval(() => load(), 10000);
  });
  for (const el of [statusFilter, unreadOnly]) el.addEventListener("change", () => !fetchBtn.hidden && load(fetchBtn));
  fetchBtn.addEventListener("click", (e) => load(e.currentTarget));
  list.append(empty("Alerts from Prometheus arrive here through Alertmanager. Nothing is fetched until you ask.", "Fetch notifications", (e) => load(e.currentTarget)));

  const links = state.config?.links || {};
  clear(root,
    h("div", { class: "panel-head" },
      h("div", {}, h("h2", { text: "Notifications" }),
        h("p", { text: "5xx and 401/403 alert; other 4xx are only logged. Alerts arrive 15–30 s after the cause and resolve about a minute after it stops." })),
      h("span", { class: "spacer" }), fetchBtn),
    h("div", { class: "card row" },
      h("label", { class: "field" }, h("span", { text: "Status" }), statusFilter),
      h("label", { class: "check" }, unreadOnly, h("span", { text: "Unread only" })),
      h("label", { class: "check" }, auto, h("span", { text: "Auto-refresh every 10 s (off by default)" })),
      h("span", { class: "spacer" }),
      h("div", { class: "links" },
        link("Prometheus alerts ↗", links.prometheus && `${links.prometheus}/alerts`),
        link("Alertmanager ↗", links.alertmanager),
        link("Grafana: firing alerts ↗", grafanaPanel(60)))),
    list);
}

function item(n, reload) {
  const tone = n.status === "resolved" ? "ok" : n.severity === "critical" ? "err" : "warn";
  return h("div", { class: "card" },
    h("div", { class: "card-title" },
      badge(n.status, tone), badge(n.severity, n.severity === "critical" ? "err" : "warn"),
      h("strong", { text: n.alertname }),
      n.read_at ? badge("read") : badge("unread", "info"),
      h("span", { class: "spacer" }),
      n.read_at ? null : h("button", {
        class: "btn btn-sm", type: "button", text: "Mark read",
        on: { click: async (e) => busy(e.currentTarget, async () => { await request("POST", `/notifications/${n.id}/read`, { label: "notifications" }); await reload(); }) },
      })),
    h("div", { text: n.summary }),
    n.description ? h("div", { class: "muted", text: n.description }) : null,
    h("div", { class: "muted", text: `started ${time(n.starts_at)}${n.ends_at ? ` · ended ${time(n.ends_at)}` : ""} · received ${time(n.received_at)}` }));
}
