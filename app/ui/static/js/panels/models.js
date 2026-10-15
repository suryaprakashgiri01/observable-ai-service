// Model inventory: fetched on demand, filtered server-side (tier, provider) and client-side.

import { request } from "../api.js";
import { badge, busy, clear, empty, h, json, statusBadge, toast } from "../dom.js";

const TIERS = ["", "economy", "standard", "premium"];
const PROVIDERS = ["", "openai", "anthropic", "google", "byom", "mock"];
const TIER_ORDER = { economy: 0, standard: 1, premium: 2 };
const COLUMNS = [
  ["id", "Id"], ["name", "Name"], ["provider", "Provider"], ["tier", "Tier"],
  ["available", "Available"], ["is_default", "Default"], ["is_fallback", "Fallback"], ["owned", "Mine"],
];

export function mount(root) {
  let data = null;
  let sort = { key: "tier", dir: 1 };

  const tier = h("select", {}, TIERS.map((t) => h("option", { value: t, text: t || "All tiers" })));
  const provider = h("select", {}, PROVIDERS.map((p) => h("option", { value: p, text: p || "All providers" })));
  const search = h("input", { type: "search", placeholder: "Search id or name" });
  const availableOnly = h("input", { type: "checkbox" });
  const summary = h("div", { class: "row" });
  const table = h("div", {});
  const refresh = h("button", { class: "btn", type: "button", text: "Refresh", hidden: true });
  const actionResult = h("div", {});

  async function load(button) {
    await busy(button, async () => {
      const r = await request("GET", "/models", { query: { tier: tier.value, provider: provider.value }, label: "models" });
      refresh.hidden = false;
      if (!r.ok) { data = null; return clear(table, h("div", { class: "card" }, statusBadge(r.status), " ", r.data?.error?.code || "", json(r.data))); }
      data = r.data;
      render();
    });
  }

  function render() {
    if (!data) return;
    const q = search.value.trim().toLowerCase();
    let rows = data.items.filter((m) => (!availableOnly.checked || m.available) && (!q || m.id.includes(q) || m.name.toLowerCase().includes(q)));
    rows = rows.sort((a, b) => {
      const av = sort.key === "tier" ? TIER_ORDER[a.tier] : a[sort.key];
      const bv = sort.key === "tier" ? TIER_ORDER[b.tier] : b[sort.key];
      return (av > bv ? 1 : av < bv ? -1 : a.id.localeCompare(b.id)) * sort.dir;
    });
    const counts = { economy: 0, standard: 0, premium: 0 };
    for (const m of rows) counts[m.tier] += 1;
    clear(summary,
      badge(`${rows.length} of ${data.total} shown`, "info"),
      Object.entries(counts).map(([t, n]) => badge(`${t}: ${n}`)),
      h("span", { class: "spacer" }),
      h("span", { class: "muted", text: `default ${data.default_model_id} · fallback ${data.fallback_model_id}` }));

    clear(table, h("div", { class: "card table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {},
        COLUMNS.map(([key, label]) => h("th", {
          class: "sortable", text: `${label}${sort.key === key ? (sort.dir > 0 ? " ▲" : " ▼") : ""}`,
          on: { click: () => { sort = { key, dir: sort.key === key ? -sort.dir : 1 }; render(); } },
        })),
        h("th", { text: "Actions" }))),
      h("tbody", {}, rows.map((m) => h("tr", {},
        h("td", { class: "mono", text: m.id }),
        h("td", { text: m.name }),
        h("td", { text: m.provider }),
        h("td", {}, badge(m.tier, m.tier === "premium" ? "warn" : m.tier === "standard" ? "info" : "")),
        h("td", {}, m.available ? badge("yes", "ok") : badge("no key")),
        h("td", {}, m.is_default ? badge("default", "info") : ""),
        h("td", {}, m.is_fallback ? badge("fallback", "info") : ""),
        h("td", {}, m.owned ? badge("BYOM", "ok") : ""),
        h("td", { class: "row" },
          action("Set default", "PUT", "/models/default", { model_id: m.id }),
          action("Set fallback", "PUT", "/models/fallback", { model_id: m.id }),
          m.owned ? action("Delete", "DELETE", `/models/custom/${encodeURIComponent(m.id)}`, undefined, "btn-danger") : null)))))));
  }

  function action(label, method, path, body, extra = "") {
    return h("button", {
      class: `btn btn-sm ${extra}`, type: "button", text: label,
      on: {
        click: (e) => busy(e.currentTarget, async () => {
          const r = await request(method, path, { body, label: "models" });
          showAction(`${method} ${path}`, r);
          if (r.ok) { toast(`${label}: ${r.status}`); await load(refresh); }
        }),
      },
    });
  }

  function showAction(title, r) {
    clear(actionResult, h("div", { class: "card" },
      h("div", { class: "card-title" }, statusBadge(r.status), h("strong", { class: "mono", text: title }), h("span", { class: "spacer" }),
        h("button", { class: "btn btn-sm btn-ghost", type: "button", text: "Dismiss", on: { click: () => clear(actionResult) } })),
      r.status === 204 ? h("p", { class: "muted", text: "204 No Content" }) : json(r.data)));
  }

  // Bring-your-own model form
  const f = {
    id: h("input", { placeholder: "my-llama", spellcheck: "false" }),
    name: h("input", { placeholder: "Llama 3.1 8B (local)" }),
    base_url: h("input", { placeholder: "http://host.docker.internal:11434/v1", spellcheck: "false" }),
    provider_model: h("input", { placeholder: "llama3.1:8b", spellcheck: "false" }),
    tier: h("select", {}, ["economy", "standard", "premium"].map((t) => h("option", { value: t, text: t, selected: t === "standard" }))),
    api_key: h("input", { type: "password", placeholder: "optional; stored encrypted" }),
  };
  const byom = h("details", { class: "card" },
    h("summary", { text: "Register your own model (POST /models/custom)" }),
    h("div", { class: "grid" }, Object.entries(f).map(([k, el]) => h("label", { class: "field" }, h("span", { text: k }), el))),
    h("div", { class: "row" },
      h("button", { class: "btn btn-sm", type: "button", text: "Fill example", on: { click: () => { f.id.value = "my-llama"; f.name.value = "Llama 3.1 8B (local Ollama)"; f.base_url.value = "http://host.docker.internal:11434/v1"; f.provider_model.value = "llama3.1:8b"; } } }),
      h("button", { class: "btn btn-sm", type: "button", text: "Fill invalid (→ 422)", on: { click: () => { f.id.value = "Bad Id!"; f.name.value = ""; f.base_url.value = "not-a-url"; f.provider_model.value = "m"; } } }),
      h("span", { class: "spacer" }),
      h("button", {
        class: "btn btn-primary", type: "button", text: "Register",
        on: {
          click: (e) => busy(e.currentTarget, async () => {
            const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value.trim()]).filter(([k, v]) => v !== "" || k !== "api_key"));
            const r = await request("POST", "/models/custom", { body, label: "models" });
            showAction("POST /models/custom", r);
            if (r.ok && data) await load(refresh);
          }),
        },
      })));

  for (const el of [tier, provider]) el.addEventListener("change", () => data && load(refresh));
  search.addEventListener("input", render);
  availableOnly.addEventListener("change", render);
  refresh.addEventListener("click", (e) => load(e.currentTarget));
  table.append(empty("The model catalog is fetched only when you ask.", "Fetch models", (e) => load(e.currentTarget)));

  clear(root,
    h("div", { class: "panel-head" },
      h("div", {}, h("h2", { text: "Models" }), h("p", { text: "Catalog by tier and provider; set your default and fallback, or bring your own OpenAI-compatible model." })),
      h("span", { class: "spacer" }), refresh),
    h("div", { class: "card row" },
      h("label", { class: "field" }, h("span", { text: "Tier (server filter)" }), tier),
      h("label", { class: "field" }, h("span", { text: "Provider (server filter)" }), provider),
      h("label", { class: "field" }, h("span", { text: "Search" }), search),
      h("label", { class: "check" }, availableOnly, h("span", { text: "Available only" }))),
    summary,
    actionResult,
    table,
    byom);
}
