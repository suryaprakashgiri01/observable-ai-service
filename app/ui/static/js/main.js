import { onCall, request, setSession, state } from "./api.js";
import { noteResponse, setupAuth } from "./auth.js";
import { addToLog, setupLog } from "./log.js";
import * as ask from "./panels/ask.js";
import * as models from "./panels/models.js";
import * as notifications from "./panels/notifications.js";
import * as routes from "./panels/routes.js";
import * as status from "./panels/status.js";
import * as tickets from "./panels/tickets.js";

const panels = { status, ask, tickets, models, notifications, routes };
const mounted = new Set();

function show(name) {
  for (const tab of document.querySelectorAll("[role=tab]")) {
    tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
  }
  // Only the top-level sections: inner layout blocks reuse the .panel class.
  for (const panel of document.querySelectorAll("section[data-panel]")) panel.hidden = panel.dataset.panel !== name;
  // Mount on first view only. Mounting renders controls and CTAs; it never calls the API.
  if (!mounted.has(name)) {
    panels[name].mount(document.getElementById(`panel-${name}`));
    mounted.add(name);
  }
  history.replaceState(null, "", `#${name}`);
}

async function boot() {
  setupLog();
  setupAuth();
  onCall(addToLog);
  onCall(noteResponse);

  const session = document.getElementById("session-id");
  session.value = state.session;
  session.addEventListener("change", () => setSession(session.value.trim() || state.session));

  // Console configuration (links and feature flags), not API data. Kept out of the log.
  try {
    const res = await fetch("/ui/config");
    state.config = await res.json();
    document.getElementById("env-line").textContent =
      `v${state.config.version} · ${state.config.environment} · fault injection ${state.config.fault_injection ? "on" : "off"} · Kafka ${state.config.kafka_enabled ? "on" : "off"}`;
  } catch {
    document.getElementById("env-line").textContent = "console config unavailable";
  }

  for (const tab of document.querySelectorAll("[role=tab]")) {
    tab.addEventListener("click", () => show(tab.dataset.tab));
  }
  const initial = location.hash.slice(1);
  show(panels[initial] ? initial : "status");
}

boot();
export { request };
