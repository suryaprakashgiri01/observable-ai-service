// Tiny DOM helpers. Text always goes through textContent: no HTML strings, no XSS.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "on") for (const [ev, fn] of Object.entries(value)) el.addEventListener(ev, fn);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key in el && typeof value !== "string") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

export function statusBadge(status) {
  const cls = status === 0 ? "s0" : `s${String(status)[0]}`;
  return h("span", { class: `status ${cls}`, text: status === 0 ? "ERR" : String(status) });
}

export function badge(text, tone = "") {
  return h("span", { class: `badge ${tone ? `b-${tone}` : ""}`, text });
}

export function json(value) {
  return h("pre", { class: "json", text: JSON.stringify(value, null, 2) });
}

export function kv(pairs) {
  const dl = h("dl", { class: "kv" });
  for (const [k, v] of pairs) {
    if (v === undefined) continue;
    dl.append(h("dt", { text: k }), h("dd", {}, v instanceof Node ? v : String(v ?? "—")));
  }
  return dl;
}

export function link(text, href) {
  return href ? h("a", { href, target: "_blank", rel: "noopener noreferrer", text }) : null;
}

export function empty(message, ctaLabel, onClick) {
  return h("div", { class: "empty" },
    h("div", { text: message }),
    ctaLabel ? h("button", { class: "btn btn-primary cta", type: "button", text: ctaLabel, on: { click: onClick } }) : null);
}

export async function busy(button, fn) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Working…";
  try { return await fn(); } finally { button.disabled = false; button.textContent = label; }
}

let toastTimer;
export function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

export function randomHex(bytes) {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function time(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}
