// The request log drawer: every call, newest first, click to expand.

import { jaegerUrl } from "./api.js";
import { clear, h, json, link, statusBadge } from "./dom.js";

const entries = [];

export function setupLog() {
  const toggle = document.getElementById("log-toggle");
  const body = document.getElementById("log-body");
  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    body.hidden = !open;
  });
  document.getElementById("log-clear").addEventListener("click", () => {
    entries.length = 0;
    render();
  });
}

export function addToLog(r) {
  entries.unshift(r);
  if (entries.length > 200) entries.pop();
  render();
}

function render() {
  document.getElementById("log-count").textContent = String(entries.length);
  const list = document.getElementById("log-list");
  clear(list, entries.map((r) => {
    const detail = h("div", { class: "log-detail", hidden: true },
      h("div", { class: "row" },
        h("span", { class: "muted", text: "Request headers" })),
      json(r.sent),
      r.body !== undefined ? [h("span", { class: "muted", text: "Body" }), json(r.body)] : null,
      h("span", { class: "muted", text: "Response" }),
      json(r.data));
    return h("li", {},
      h("div", { class: "log-line", on: { click: () => { detail.hidden = !detail.hidden; } } },
        statusBadge(r.status),
        h("span", { class: "mono", text: r.method }),
        h("span", { class: "path", text: r.path }),
        r.label ? h("span", { class: "badge", text: r.label }) : null,
        h("span", { class: "ms", text: `${r.ms} ms` }),
        r.requestId ? h("span", { class: "muted mono", text: `req ${r.requestId}` }) : null,
        link("trace ↗", jaegerUrl(r.traceId)),
        h("span", { class: "spacer" }),
        h("span", { class: "muted", text: r.at.toLocaleTimeString() })),
      detail);
  }));
}
