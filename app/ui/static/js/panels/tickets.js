import { jaegerUrl, request } from "../api.js";
import { badge, busy, clear, empty, h, json, kv, link, statusBadge, time } from "../dom.js";

const PRIORITY = { critical: "err", high: "warn", medium: "info", low: "" };

export function mount(root) {
  const list = h("div", {});
  const detail = h("div", {});
  const idInput = h("input", { placeholder: "ticket UUID", spellcheck: "false", size: 38 });
  const limit = h("select", {}, [10, 20, 50, 100].map((n) => h("option", { value: n, text: `${n} newest`, selected: n === 20 })));
  const reload = h("button", { class: "btn", type: "button", text: "Refresh", hidden: true });

  async function load(button) {
    await busy(button, async () => {
      const r = await request("GET", "/tickets", { query: { limit: limit.value }, label: "tickets" });
      reload.hidden = false;
      if (!r.ok) return clear(list, errorCard(r));
      if (!r.data.items.length) return clear(list, h("div", { class: "empty", text: "No tickets yet. Create one from the Ask tab." }));
      clear(list, h("div", { class: "card table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Created", "Title", "Priority", "Status", "Request id", "Trace"].map((t) => h("th", { text: t })))),
        h("tbody", {}, r.data.items.map((t) => h("tr", { class: "clickable", on: { click: () => open(t.id) } },
          h("td", { text: time(t.created_at) }),
          h("td", { text: t.title }),
          h("td", {}, badge(t.priority, PRIORITY[t.priority])),
          h("td", { text: t.status }),
          h("td", { class: "mono", text: t.request_id || "—" }),
          h("td", {}, link("trace ↗", jaegerUrl(t.trace_id)) || "—"))))),
        h("p", { class: "muted", text: `${r.data.total} ticket(s). Click a row to fetch it with GET /tickets/{id}.` })));
    });
  }

  async function open(id) {
    const r = await request("GET", `/tickets/${encodeURIComponent(id)}`, { label: "ticket" });
    clear(detail, h("div", { class: "card" },
      h("div", { class: "card-title" }, h("h3", { text: `GET /tickets/${id}` }), h("span", { class: "spacer" }), statusBadge(r.status)),
      r.ok ? kv([
        ["id", r.data.id], ["title", r.data.title], ["priority", badge(r.data.priority, PRIORITY[r.data.priority])],
        ["status", r.data.status], ["created", time(r.data.created_at)], ["request id", r.data.request_id],
        ["trace", link(r.data.trace_id || "—", jaegerUrl(r.data.trace_id)) || "—"],
      ]) : json(r.data)));
  }

  reload.addEventListener("click", (e) => load(e.currentTarget));
  list.append(empty("Your tickets are fetched only when you ask.", "Load my tickets", (e) => load(e.currentTarget)));

  clear(root,
    h("div", { class: "panel-head" },
      h("div", {}, h("h2", { text: "Tickets" }), h("p", { text: "Tickets created by the agent for the current key. Ids are UUIDs." })),
      h("span", { class: "spacer" }), limit, reload),
    h("div", { class: "card row" },
      h("label", { class: "field" }, h("span", { text: "Look up by id" }), idInput),
      h("button", { class: "btn", type: "button", text: "Get ticket", on: { click: () => idInput.value.trim() && open(idInput.value.trim()) } }),
      h("span", { class: "muted", text: "Try a random UUID for 404, or “abc” for 422." })),
    detail,
    list);
}

function errorCard(r) {
  return h("div", { class: "card" }, h("div", { class: "card-title" }, statusBadge(r.status), h("strong", { text: r.data?.error?.code || "Error" })), json(r.data));
}
