// Authorize dialog and the auth pill. The pill reflects the last GET /me answer.

import { request, setKey, state } from "./api.js";
import { toast } from "./dom.js";

const pill = () => document.getElementById("auth-pill");
const pillText = () => document.getElementById("auth-pill-text");

export const auth = { me: null, lastStatus: null };

function render(kind, text, title) {
  const el = pill();
  el.className = `pill pill-${kind}`;
  pillText().textContent = text;
  el.title = title || "Click to check the key with GET /me";
}

export async function checkMe({ quiet = false } = {}) {
  if (!state.key) {
    auth.me = null;
    auth.lastStatus = null;
    render("none", "No key", "Click Authorize to paste an API key");
    return null;
  }
  const r = await request("GET", "/me", { label: "auth check" });
  auth.lastStatus = r.status;
  if (r.ok) {
    auth.me = r.data;
    render("ok", `Authorized · ${r.data.email} (${r.data.role})`, `key ${r.data.key_prefix}…, user id ${r.data.id}`);
    if (!quiet) toast(`Authorized as ${r.data.email}`);
  } else {
    auth.me = null;
    const code = r.data?.error?.code || "ERROR";
    render(r.status === 401 || r.status === 403 ? "err" : "warn", `Not authorized · ${r.status} ${code}`, r.data?.error?.message);
    if (!quiet) toast(`Key rejected: ${r.status} ${code}`);
  }
  return r;
}

/** Called for every response: a 401 with my key means the key stopped working. */
export function noteResponse(r) {
  if (r.status === 401 && r.sent["X-API-Key"] && state.key && r.label !== "auth check" && r.sent["X-API-Key"].startsWith(state.key.slice(0, 10))) {
    render("err", `Not authorized · 401 ${r.data?.error?.code || ""}`.trim());
  }
}

export function setupAuth() {
  const dialog = document.getElementById("auth-dialog");
  const input = document.getElementById("auth-key");
  const remember = document.getElementById("auth-remember");

  document.getElementById("authorize-btn").addEventListener("click", () => {
    input.value = state.key;
    remember.checked = state.remembered;
    dialog.showModal();
    input.focus();
  });
  pill().addEventListener("click", () => checkMe());

  dialog.addEventListener("close", async () => {
    if (dialog.returnValue === "save") {
      setKey(input.value.trim(), remember.checked);
      await checkMe();
    } else if (dialog.returnValue === "logout") {
      setKey("", false);
      await checkMe({ quiet: true });
      toast("Key removed from this browser");
    }
    input.value = "";
  });

  // A key may already be stored from earlier: show that without calling the API.
  if (state.key) render("warn", "Key set · not checked", "Click to check it with GET /me");
  else render("none", "No key");
}
