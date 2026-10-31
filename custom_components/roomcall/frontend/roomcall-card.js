/* RoomCall push-to-talk card for Home Assistant dashboards. */
const CARD_VERSION = "0.22.1";
// Peak level (0–1) below which a recording is treated as silence.
const SILENCE_PEAK = 0.04;
const LAYOUTS = ["simple", "default", "compact", "kiosk"];
const APPEARANCES = ["glass", "material", "home-assistant"];
const THEMES = ["auto", "light", "dark"];
// Cards added from the card picker start simple; a saved card without `layout` keeps "default".
const NEW_CARD = { title: "Intercom", layout: "simple" };
// A tapped microphone has no finger holding it open, so it always closes itself.
const TAP_LIMIT_MS = 120000;
// English fallbacks; the translated catalog replaces them once it loads.
const TAP_TEXT = {
  tap_to_talk: "Tap to talk",
  tap_to_send: "Tap to send",
  tap_to_finish: "Tap to finish",
  tap_hint: "Pick a room, tap the button and speak.",
  tap_too_short: "Too short — tap, talk, then tap again.",
  tap_live_unsupported: "Live calls need RoomCall 0.5 or newer in Home Assistant — tap again to send a recording.",
};
const cardConfig = (config) => ({
  title: "Intercom",
  layout: "default",
  history: 3,
  people: true,
  live: true,
  appearance: "glass",
  theme: "auto",
  ...config,
});
const LIVE_RATE = 16000;
// Turns the microphone into 16 kHz 16-bit PCM frames of 40 ms for live calls.
const PCM_WORKLET = `
class RoomCallPcm extends AudioWorkletProcessor {
  constructor() { super(); this.step = sampleRate / ${16000}; this.t = 0; this.out = new Int16Array(640); this.n = 0; }
  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    let t = this.t;
    while (t < input.length) {
      const start = Math.floor(t);
      const end = Math.max(start + 1, Math.min(input.length, Math.floor(t + this.step)));
      let sum = 0;
      for (let k = start; k < end; k++) sum += input[k];
      const s = Math.max(-1, Math.min(1, sum / (end - start)));
      this.out[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.out.length) {
        this.port.postMessage(this.out.buffer, [this.out.buffer]);
        this.out = new Int16Array(640);
        this.n = 0;
      }
      t += this.step;
    }
    this.t = t - input.length;
    return true;
  }
}
registerProcessor("roomcall-pcm16", RoomCallPcm);`;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

function timeAgo(iso) {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

/** "#0a84ff" or [10, 132, 255] (the card editor's color picker) → [hue, saturation %]. */
function seedHsl(color) {
  const rgb = Array.isArray(color) ? color
    : [1, 3, 5].map((i) => parseInt(String(color || "#0a84ff").replace("#", "").padEnd(6, "0").slice(i - 1, i + 1), 16));
  const [r, g, b] = rgb.map((v) => (Number(v) || 0) / 255);
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  let h = 0, s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    h = (max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4) * 60;
  }
  return [h, s * 100];
}
/** Dark text on light seed colours (yellow, mint) so labels on them stay readable. */
function onSeed(color) {
  const hex = seedHex(color).replace("#", "").padEnd(6, "0");
  const [r, g, b] = [0, 2, 4].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.4 ? "#101317" : "#ffffff";
}
const hsl = (h, s, l, a = 1) => `hsl(${Math.round(h)} ${Math.round(s)}% ${l}% / ${a})`;
const seedHex = (color) => (Array.isArray(color)
  ? `#${color.map((v) => Number(v).toString(16).padStart(2, "0")).join("")}` : color || "#0a84ff");

/**
 * Design tokens for the card. "glass" is frosted, Apple-like surfaces around one accent; "material"
 * is a Material 3 tonal palette generated from the same seed colour. Home Assistant's own colour
 * variables are redefined inside the card too, so every control and the Settings panel follow.
 */
function themeTokens(appearance, dark, color) {
  if (appearance === "home-assistant") return {};
  const [h, s] = seedHsl(color);
  const seed = seedHex(color);
  if (appearance === "material") {
    const th = (h + 60) % 360, sh = (h + 20) % 360;
    const p = dark ? {
      primary: hsl(h, Math.min(s, 85), 70), onPrimary: hsl(h, 60, 14), tertiary: hsl(th, 55, 68),
      secondaryContainer: hsl(sh, 24, 26), onSecondaryContainer: hsl(sh, 40, 90),
      surface: hsl(h, 14, 8), surfaceHigh: hsl(h, 12, 14), surfaceHighest: hsl(h, 12, 19),
      onSurface: hsl(h, 12, 92), onSurfaceVariant: hsl(h, 10, 76), outline: hsl(h, 8, 42),
      success: hsl(140, 45, 70), onSuccess: hsl(140, 60, 12), warning: hsl(36, 90, 66), error: hsl(6, 90, 72),
    } : {
      primary: hsl(h, Math.min(s, 80), 42), onPrimary: "#ffffff", tertiary: hsl(th, 60, 50),
      secondaryContainer: hsl(sh, 55, 88), onSecondaryContainer: hsl(sh, 55, 16),
      surface: hsl(h, 30, 98), surfaceHigh: hsl(h, 22, 93), surfaceHighest: hsl(h, 20, 89),
      onSurface: hsl(h, 14, 11), onSurfaceVariant: hsl(h, 10, 32), outline: hsl(h, 8, 62),
      success: hsl(140, 60, 28), onSuccess: "#ffffff", warning: hsl(32, 90, 38), error: hsl(4, 75, 44),
    };
    return {
      "--rc-bg": p.surface, "--rc-surface": p.surfaceHigh, "--rc-surface-strong": p.surfaceHighest,
      "--rc-stroke": "transparent", "--rc-outline": p.outline, "--rc-blur": "none", "--rc-shadow": "none",
      "--rc-selected": p.secondaryContainer, "--rc-on-selected": p.onSecondaryContainer,
      "--rc-seg-on": p.secondaryContainer, "--rc-on-seg": p.onSecondaryContainer,
      "--rc-orb": `radial-gradient(circle at 34% 28%, rgba(255,255,255,0.35), rgba(255,255,255,0) 48%), linear-gradient(135deg, ${p.primary}, ${p.tertiary})`,
      "--rc-orb-glow": hsl(h, 70, 50, dark ? 0.35 : 0.28), "--rc-glow-a": p.primary, "--rc-glow-b": p.tertiary,
      "--rc-glow-opacity": dark ? "16%" : "12%", "--rc-radius": "28px", "--rc-chip-radius": "12px",
      "--rc-success": p.success, "--rc-on-success": p.onSuccess,
      "--rc-font": "'Google Sans', 'Roboto Flex', Roboto, system-ui, sans-serif",
      "--primary-color": p.primary, "--text-primary-color": p.onPrimary, "--primary-text-color": p.onSurface,
      "--secondary-text-color": p.onSurfaceVariant, "--divider-color": p.outline, "--card-background-color": p.surfaceHigh,
      "--success-color": p.success, "--warning-color": p.warning, "--error-color": p.error,
    };
  }
  const t = dark ? {
    bg: "rgba(10,12,18,0.74)", surface: "rgba(255,255,255,0.08)", strong: "rgba(255,255,255,0.14)", stroke: "rgba(255,255,255,0.14)",
    text: "#f5f7fa", sub: "#aab3c2", dialog: "rgba(22,25,33,0.92)", segOn: "rgba(255,255,255,0.20)", onSeg: "#ffffff",
    shadow: "inset 0 1px 0 rgba(255,255,255,0.10)", success: "#1f7a3a", warning: "#ff9f0a", error: "#ff453a",
  } : {
    bg: "rgba(255,255,255,0.66)", surface: "rgba(255,255,255,0.62)", strong: "rgba(255,255,255,0.9)", stroke: "rgba(255,255,255,0.95)",
    text: "#101317", sub: "#4f5866", dialog: "rgba(248,249,252,0.94)", segOn: "#ffffff", onSeg: "#101317",
    shadow: "0 12px 34px rgba(15,23,42,0.08), inset 0 1px 0 rgba(255,255,255,0.9)", success: "#1f7a3a", warning: "#b45309", error: "#d92d20",
  };
  return {
    "--rc-bg": t.bg, "--rc-surface": t.surface, "--rc-surface-strong": t.strong, "--rc-stroke": t.stroke,
    "--rc-outline": t.stroke, "--rc-blur": "blur(30px) saturate(170%)", "--rc-shadow": t.shadow,
    "--rc-selected": seed, "--rc-on-selected": onSeed(color), "--rc-seg-on": t.segOn, "--rc-on-seg": t.onSeg,
    "--rc-orb": `radial-gradient(circle at 34% 28%, rgba(255,255,255,0.38), rgba(255,255,255,0) 46%), ${seed}`,
    "--rc-orb-glow": hsl(h, Math.max(s, 60), 50, dark ? 0.5 : 0.32), "--rc-glow-a": seed, "--rc-glow-b": "#5e5ce6",
    "--rc-glow-opacity": dark ? "24%" : "16%", "--rc-radius": "28px", "--rc-chip-radius": "18px",
    "--rc-success": t.success, "--rc-on-success": "#ffffff",
    "--rc-font": "-apple-system, BlinkMacSystemFont, 'SF Pro Text', Inter, system-ui, sans-serif",
    "--primary-color": seed, "--text-primary-color": onSeed(color), "--primary-text-color": t.text,
    "--secondary-text-color": t.sub, "--divider-color": t.stroke, "--card-background-color": t.dialog,
    "--success-color": t.success, "--warning-color": t.warning, "--error-color": t.error,
  };
}

/** Outline icons for glass (SF Symbols-like), filled ones for Material. */
const ICONS = {
  glass: { talk: "mdi:microphone-outline", urgent: "mdi:alert-outline", dnd: "mdi:moon-waning-crescent",
    task: "mdi:clipboard-text-clock-outline", done: "mdi:check", later: "mdi:alarm-snooze", door: "mdi:doorbell-video" },
  material: { talk: "mdi:microphone", urgent: "mdi:alert", dnd: "mdi:minus-circle", task: "mdi:clipboard-text-clock",
    done: "mdi:check", later: "mdi:snooze", door: "mdi:doorbell-video" },
  "home-assistant": { talk: "mdi:microphone", urgent: "mdi:alert", dnd: "mdi:bell-sleep", task: "mdi:clipboard-alert-outline",
    done: "mdi:check", later: "mdi:alarm-snooze", door: "mdi:doorbell-video" },
};

class RoomCallCard extends HTMLElement {
  static getStubConfig() {
    return { ...NEW_CARD };
  }

  static getConfigElement() {
    return document.createElement("roomcall-card-editor");
  }

  setConfig(config) {
    this._communication?.close();
    this._communication = null;
    if (config.layout && !LAYOUTS.includes(config.layout)) {
      throw new Error(`layout must be one of: ${LAYOUTS.join(", ")}`);
    }
    if (config.appearance && !APPEARANCES.includes(config.appearance)) {
      throw new Error(`appearance must be one of: ${APPEARANCES.join(", ")}`);
    }
    if (config.theme && !THEMES.includes(config.theme)) {
      throw new Error(`theme must be one of: ${THEMES.join(", ")}`);
    }
    this._config = cardConfig(config);
    this._expanded = false;
    const fromUrl = new URLSearchParams(window.location.search).get("roomcall");
    this._replyTo = fromUrl || null;
    this._target = fromUrl || this._config.default_room || "all";
    this._person = null;
    this._urgent = false;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._themeKey = null;
    this._render();
    this._applyTheme();
    if (this._hass) this._load();
  }

  /** Applies the appearance's tokens; "auto" follows Home Assistant's dark mode. */
  _applyTheme() {
    const { appearance, theme, color } = this._config;
    const dark = theme === "dark" || (theme === "auto" && Boolean(this._hass?.themes?.darkMode));
    const key = JSON.stringify([appearance, dark, color]);
    if (key === this._themeKey) return;
    this._themeKey = key;
    for (const name of this._themeVars || []) this.style.removeProperty(name);
    const tokens = themeTokens(appearance, dark, color);
    for (const [name, value] of Object.entries(tokens)) this.style.setProperty(name, value);
    this._themeVars = Object.keys(tokens);
    this.setAttribute("appearance", appearance);
    this.toggleAttribute("dark", dark);
    this._icons = ICONS[appearance] || ICONS.glass;
    this.shadowRoot?.querySelector(".talk ha-icon")?.setAttribute("icon", this._icons.talk);
    if (this._hass && this._rooms) this._renderToggles();
    if (this._tasks) this._renderTasks();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    this._applyTheme();
    if (first) this._load();
    else {
      this._renderToggles();
      this._checkDoorbell();
      // Task sensors change when a task opens, is added, or someone confirms it elsewhere.
      const signature = this._taskStates(hass);
      if (this._taskSignature !== undefined && signature !== this._taskSignature) this._loadTasks();
    }
  }

  getCardSize() {
    const height = this.shadowRoot?.querySelector("ha-card")?.getBoundingClientRect().height;
    return height ? Math.ceil(height / 50) : { compact: 3, simple: 5 }[this._config.layout] || 7;
  }

  getGridOptions() {
    // Room chips, forms and setup all change height. Fixed rows let the next card
    // start underneath our contents, especially in narrow sections.
    return { columns: this._config.layout === "kiosk" ? "full" : 12, rows: "auto" };
  }

  async _load() {
    try {
      const res = await this._hass.callApi("GET", "roomcall/rooms");
      const only = this._config.rooms;
      this._rooms = only ? res.rooms.filter((r) => only.includes(r.area_id) || only.includes(r.name)) : res.rooms;
      // A floor would also reach any room this card hides, so it needs all of its rooms visible.
      this._floors = (res.floors || []).filter((f) => f.rooms.every((id) => this._rooms.some((r) => r.area_id === id)));
      this._count = res.speakers;
      this._configured = res.configured !== false;
      this.shadowRoot.querySelector(".talk").disabled = !this._configured;
      this._dndEntity = res.dnd_entity;
      this._doorbellFetched = Date.now();
      this._setDoorbell(res.doorbell);
      const people = this._config.people;
      this._people = people === false ? [] : Array.isArray(people)
        ? res.people.filter((p) => people.includes(p.entity_id)) : res.people;
    } catch (err) {
      this._rooms = [];
      this._people = [];
      this._setStatus(`RoomCall unavailable: ${err.message || err}`, "err");
    }
    this._renderRooms();
    this._renderToggles();
    this._loadHistory();
    try {
      const strings = await this._hass.callApi("GET", `roomcall/locale?language=${encodeURIComponent(this._hass.language || "en")}`);
      this._strings = strings;
      this._relabelTap();
      this.shadowRoot.querySelector(".recipients-title").textContent = strings.recipients;
      this._renderMore();
      this._renderRooms();
      this._loadTasks();
      if (!this._configured) this._setStatus(strings.not_ready, "muted");
      const { RoomCallControls } = await import(`./roomcall-controls.js?v=${CARD_VERSION}`);
      if (this.isConnected && !this._communication) this._communication = new RoomCallControls(this, strings);
    } catch (error) { console.warn("RoomCall communication controls", error); }
  }

  disconnectedCallback() {
    clearTimeout(this._doorbellRetry);
    this._doorbellRetry = null;
    window.removeEventListener("blur", this._windowBlur);
    clearTimeout(this._tapTimer);
    this._communication?.close();
    this._communication = null;
    this._stream?.getTracks().forEach((track) => track.stop());
    this._ctx?.close();
    if (this._live) {
      this._live.done = true;
      if (this._live.handlerId !== null) this._endLiveAudio(this._live);
      this._live.unsub?.();
      this._live = null;
    }
  }

  connectedCallback() {
    this._windowBlur ||= () => this._stop({ preventDefault() {} });
    window.addEventListener("blur", this._windowBlur);
    if (this._hass && !this._communication) this._load();
  }

  _t(key, values = {}) {
    return Object.entries(values).reduce((copy, [name, value]) => copy.replaceAll(`{${name}}`, value), this._strings?.[key] || key);
  }

  async _loadHistory() {
    if (!this._config.history || !["default", "simple"].includes(this._config.layout)) return;
    try {
      const res = await this._hass.callApi("GET", "roomcall/history");
      this._history = res.calls.slice(0, this._config.history);
    } catch {
      this._history = [];
    }
    this._renderHistory();
  }

  _setDoorbell(bell) {
    if (JSON.stringify(bell || null) === JSON.stringify(this._doorbell || null)) return;
    this._doorbell = bell || null;
    // Only presses after the card learned about this doorbell ring the banner.
    const state = this._doorbell && this._hass.states[this._doorbell.entity];
    this._doorbellSeen = state?.last_changed;
    this._doorbellState = state?.state;
  }

  async _refreshDoorbell() {
    // Doorbell options can change while a wall tablet stays open; re-read them at most once a
    // minute, and when it's too soon, try again once the minute is up.
    const wait = 60000 - (Date.now() - (this._doorbellFetched || 0));
    if (wait > 0) {
      this._doorbellRetry ||= setTimeout(() => {
        this._doorbellRetry = null;
        this._refreshDoorbell();
      }, wait);
      return;
    }
    this._doorbellFetched = Date.now();
    try {
      this._setDoorbell((await this._hass.callApi("GET", "roomcall/rooms")).doorbell);
    } catch { /* keep the current doorbell */ }
  }

  _checkDoorbell() {
    this._refreshDoorbell();
    const bell = this._doorbell;
    const state = bell && this._hass.states[bell.entity];
    if (!state || state.last_changed === this._doorbellSeen) return;
    this._doorbellSeen = state.last_changed;
    // Like the backend: coming back from unavailable is not a press.
    const previous = this._doorbellState;
    this._doorbellState = state.state;
    if (["unavailable", "unknown", "off"].includes(state.state) || previous === "unavailable") return;
    // Like the backend: a binary sensor leaving "unknown" is initializing, not a press.
    if (previous === "unknown" && bell.entity.startsWith("binary_sensor.")) return;
    // A ring from before a suspended tablet woke up is not a visitor at the door now.
    if (Date.now() - Date.parse(state.last_changed) > 120000) return;
    // Same 10-second cooldown as the backend, so a bounce doesn't reopen a dismissed banner.
    if (Date.now() - (this._doorbellShown || 0) < 10000) return;
    this._doorbellShown = Date.now();
    const box = this.shadowRoot.querySelector(".doorbell");
    const camera = bell.camera && this._hass.states[bell.camera];
    box.replaceChildren();
    if (camera?.attributes?.entity_picture) {
      const img = document.createElement("img");
      img.alt = "";
      img.src = camera.attributes.entity_picture;
      box.append(img);
    }
    const text = document.createElement("div");
    text.className = "what";
    text.textContent = this._t("doorbell_message", { name: bell.name });
    box.append(text);
    const actions = document.createElement("div");
    actions.className = "task-actions";
    if (bell.camera) {
      const answer = this._chip("mdi:doorbell-video", this._t("answer_door"), false, () => {
        this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: bell.camera }, bubbles: true, composed: true }));
        box.hidden = true;
      }, "done");
      answer.removeAttribute("aria-pressed");
      answer.setAttribute("aria-label", this._t("answer_door"));
      actions.append(answer);
    }
    const close = document.createElement("button");
    close.type = "button";
    close.className = "later";
    close.setAttribute("aria-label", this._t("close"));
    close.innerHTML = `<ha-icon icon="mdi:close"></ha-icon>`;
    close.addEventListener("click", () => { box.hidden = true; });
    actions.append(close);
    box.append(actions);
    box.hidden = false;
    clearTimeout(this._doorbellTimer);
    this._doorbellTimer = setTimeout(() => { box.hidden = true; }, 120000);
  }

  async _task(request = {}) {
    const res = await this._hass.callApi("POST", "roomcall/command", { operation: "tasks", request });
    if (!res.ok) throw new Error(res.error?.message || res.error?.code);
    this._tasks = res.tasks;
    this._taskSignature = this._taskStates(this._hass);
    this._renderTasks();
  }

  _taskStates(hass) {
    // Every RoomCall task sensor, including ones added after the card loaded.
    return Object.entries(hass.states)
      .filter(([id, s]) => id.startsWith("sensor.") && s?.attributes && "task" in s.attributes && "done_by" in s.attributes)
      .map(([id, s]) => `${id}:${s.last_changed}`).sort().join();
  }

  async _loadTasks() {
    try {
      await this._task();
    } catch (err) {
      console.warn("RoomCall tasks", err);
    }
  }

  _renderTasks() {
    const box = this.shadowRoot.querySelector(".tasks");
    const open = (this._tasks || []).filter((t) => t.state === "open");
    box.hidden = !open.length;
    box.setAttribute("aria-label", this._t("tasks"));
    const title = document.createElement("div");
    title.className = "recipients-title";
    title.textContent = this._t("tasks");
    const rows = open.map((task) => {
      const row = document.createElement("div");
      row.className = "task";
      row.innerHTML = `<ha-icon icon="${this._icons.task}"></ha-icon>
        <div class="what"><div class="name"></div><div class="muted when"></div></div>`;
      row.querySelector(".name").textContent = task.name;
      row.querySelector(".when").textContent = this._t("task_announced", { time: timeAgo(task.opened) });
      const run = (button, request) => button.addEventListener("click", async () => {
        row.querySelectorAll("button").forEach((b) => { b.disabled = true; });
        try {
          await this._task(request);
        } catch (err) {
          row.querySelectorAll("button").forEach((b) => { b.disabled = false; });
          this._setStatus(err.message || String(err), "err");
        }
      });
      // The only way to finish a task here is to say it was done; there is no dismiss.
      const done = this._chip("mdi:check", task.task, false, () => {}, "done");
      done.removeAttribute("aria-pressed");
      done.setAttribute("aria-label", task.task);
      run(done, { action: "complete", id: task.id });
      const later = document.createElement("button");
      later.type = "button";
      later.className = "later";
      later.title = this._t("remind_later");
      later.setAttribute("aria-label", `${this._t("remind_later")}: ${task.name}`);
      later.innerHTML = `<ha-icon icon="${this._icons.later}"></ha-icon>`;
      run(later, { action: "snooze", id: task.id });
      const actions = document.createElement("div");
      actions.className = "task-actions";
      actions.append(done, later);
      row.append(actions);
      return row;
    });
    box.replaceChildren(title, ...rows);
  }

  _roomName(id) {
    if (!id || id === "all") return "Everywhere";
    return (this._rooms || []).find((r) => r.area_id === id)?.name
      || (this._floors || []).find((f) => f.id === id)?.name
      || id.replace(/^(floor|label):/, "");
  }

  _render() {
    const layout = this._config.layout;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; min-width: 0; min-height: 0; height: 100%; container: roomcall / inline-size; }
        ha-card { padding: 16px; display: flex; flex-direction: column; gap: 12px; height: 100%; box-sizing: border-box;
          min-width: 0; min-height: 0; overflow: auto; }
        ha-card > * { flex-shrink: 0; min-width: 0; }
        .workspace { display:grid; gap:20px; min-width:0; }
        .recipients,.composer { min-width:0; display:grid; gap:12px; align-content:start; }
        .recipients-title { color:var(--secondary-text-color); font-size:0.8125rem; font-weight:600; }
        .destination { text-align:center; font-size:0.9375rem; font-weight:600; overflow-wrap:anywhere; }
        .head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px; }
        .title { font-size: 1.125rem; font-weight: 500; overflow-wrap: anywhere; }
        .muted { color: var(--secondary-text-color); font-size: 0.8125rem; }
        .toggles { display: flex; gap: 6px; }
        .reply { display: none; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 12px;
          background: color-mix(in srgb, var(--primary-color) 15%, transparent); font-size: 0.875rem; }
        .reply.on { display: flex; }
        .reply button { margin-left: auto; }
        .chips { display: flex; flex-wrap: wrap; gap: 8px; }
        .chip {
          border: 1px solid var(--divider-color); background: var(--card-background-color);
          color: var(--primary-text-color); border-radius: 999px; padding: 8px 14px;
          font-family: inherit; font-size: 0.875rem; cursor: pointer; display: inline-flex; gap: 6px; align-items: center;
          max-width: 100%; overflow-wrap: anywhere;
          min-height:44px; min-width:44px; box-sizing:border-box;
        }
        .chip.small { padding: 5px 10px; font-size: 0.8125rem; }
        .chip[aria-pressed="true"] { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
        .chip.urgent[aria-pressed="true"] { background: var(--error-color, #db4437); border-color: var(--error-color, #db4437); }
        .chip.dnd[aria-pressed="true"] { background: var(--warning-color, #ffa600); border-color: var(--warning-color, #ffa600); color: #000; }
        .chip ha-icon { --mdc-icon-size: 18px; }
        .chip.small ha-icon { --mdc-icon-size: 16px; }
        .people:empty { display: none; }
        .wrap { flex: 1 0 auto; display: grid; place-items: center; min-height: 150px; }
        .wrap[hidden] { display:none }
        button.talk {
          width: min(100%, 170px); aspect-ratio: 1; border-radius: 50%; border: 0; cursor: pointer;
          background: var(--primary-color); color: var(--text-primary-color, #fff);
          font-family: inherit; font-size: 1.0625rem; font-weight: 600; touch-action: none; user-select: none;
          -webkit-user-select: none; display: grid; place-items: center; align-content: center; gap: 6px; transition: transform 0.08s;
          box-shadow: 0 10px 30px color-mix(in srgb, var(--primary-color) 35%, transparent);
        }
        button.talk ha-icon { --mdc-icon-size: 40px; }
        button.talk.urgent { background: var(--error-color, #db4437); }
        button.talk.live {
          background: var(--error-color, #db4437); transform: scale(0.97);
          box-shadow: 0 0 0 calc(var(--level, 0) * 30px) color-mix(in srgb, var(--error-color, #db4437) 25%, transparent);
        }
        button.talk:disabled { opacity: 0.55; cursor: progress; }
        .status { text-align: center; min-height: 1.3em; font-size: 0.875rem; overflow-wrap: anywhere; }
        .ok { color: var(--success-color, #43a047); } .err { color: var(--error-color, #db4437); }
        .history { display: grid; gap: 6px; }
        .history:empty { display: none; }
        .call { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; align-items: center;
          padding: 8px 10px; border-radius: 10px; background: color-mix(in srgb, var(--primary-text-color) 5%, transparent); }
        .call .what { font-size: 0.8125rem; line-height: 1.3; overflow: hidden; }
        .call .text { color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .call button { border: 0; background: none; color: var(--primary-color); cursor: pointer; padding: 4px; min-width:44px; min-height:44px; }
        .call ha-icon { --mdc-icon-size: 20px; }
        .doorbell { display:flex; flex-wrap:wrap; gap:10px 12px; align-items:center; padding:10px 12px; border-radius:12px;
          background: color-mix(in srgb, var(--primary-color) 16%, transparent); }
        .doorbell[hidden] { display:none; }
        .doorbell img { width:96px; aspect-ratio:16/9; object-fit:cover; border-radius:8px; }
        .doorbell .what { flex:1 1 140px; min-width:0; font-weight:600; overflow-wrap:anywhere; }
        .doorbell button.later { border:0; background:none; color:var(--secondary-text-color); cursor:pointer;
          min-width:44px; min-height:44px; border-radius:50%; }
        .tasks { display:grid; gap:8px; }
        .tasks[hidden] { display:none; }
        .task { display:flex; flex-wrap:wrap; gap:8px 12px; align-items:center; padding:10px 12px; border-radius:12px;
          background: color-mix(in srgb, var(--warning-color, #ffa600) 14%, transparent); }
        .task > ha-icon { color: var(--warning-color, #ffa600); }
        .task .what { flex:1 1 140px; min-width:0; }
        .task .name { font-weight:600; overflow-wrap:anywhere; }
        .task-actions { display:flex; gap:6px; align-items:center; margin-inline-start:auto; max-width:100%; }
        .chip.done { background: var(--success-color, #43a047); border-color: var(--success-color, #43a047);
          color: var(--text-primary-color, #fff); font-weight:600; }
        .task button.later { border:0; background:none; color:var(--secondary-text-color); cursor:pointer;
          min-width:44px; min-height:44px; border-radius:50%; }
        .task button:disabled { opacity:0.55; cursor:progress; }

        /* Simple: rooms, the talk button and the latest call; More reveals everything else. */
        .head .more[hidden] { display:none; }
        :host([layout="simple"]) .head .chip.more { min-height:44px; }
        /* More/Less stays beside the title; Urgent and Do Not Disturb get their own row. */
        :host([layout="simple"]) .head .more { margin-inline-start:auto; }
        :host([layout="simple"]) .head .toggles { order:1; flex-basis:100%; flex-wrap:wrap; justify-content:flex-end; }
        :host([layout="simple"]:not([expanded])) .toggles,
        :host([layout="simple"]:not([expanded])) .people,
        :host([layout="simple"]:not([expanded])) .communication nav[data-toolbar] { display:none; }

        :host([layout="compact"]) ha-card { padding:12px; gap:10px; }
        :host([layout="compact"]) .title, :host([layout="compact"]) .recipients-title, :host([layout="compact"]) .history { display: none; }
        :host([layout="compact"]) .workspace { gap:12px; }
        :host([layout="compact"]) .chips.rooms { flex: 1; }
        :host([layout="compact"]) .wrap { flex: 0 0 auto; min-height: 0; }
        :host([layout="compact"]) button.talk { width: 64px; font-size: 0; gap: 0; }
        :host([layout="compact"]) button.talk ha-icon { --mdc-icon-size: 28px; }
        :host([layout="compact"]) .status { flex-basis: 100%; }

        :host([layout="kiosk"]) ha-card { padding: 24px; gap: 18px; }
        :host([layout="kiosk"]) .title { font-size: 1.625rem; }
        :host([layout="kiosk"]) .chip { padding: 14px 22px; font-size: 1.125rem; }
        :host([layout="kiosk"]) .chip ha-icon { --mdc-icon-size: 24px; }
        :host([layout="kiosk"]) button.talk { width: min(100%, 340px); font-size: 1.625rem; }
        :host([layout="kiosk"]) button.talk ha-icon { --mdc-icon-size: 72px; }
        :host([layout="kiosk"]) .status { font-size: 1.125rem; }
        :host([layout="kiosk"]) .history { display: none; }
        @container roomcall (min-width: 640px) {
          .workspace { grid-template-columns:minmax(0,1fr) minmax(0,1fr); align-items:start; }
          .composer { border-inline-start:1px solid var(--divider-color); padding-inline-start:20px; }
          .chips.rooms { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); }
          .chips.rooms .chip { justify-content:flex-start; }
        }
        @media (prefers-reduced-motion:reduce) { button.talk { transition:none; } }
        button:focus-visible { outline:2px solid var(--primary-color); outline-offset:3px; }
        /* ---- Glass and Material appearances (design A; tokens from themeTokens) ---- */
        :host([appearance="glass"]), :host([appearance="material"]) { color: var(--primary-text-color); }
        :host([appearance="glass"]) ha-card, :host([appearance="material"]) ha-card {
          background:
            radial-gradient(60% 45% at 0% 100%, color-mix(in srgb, var(--rc-glow-a) var(--rc-glow-opacity), transparent), transparent),
            radial-gradient(50% 40% at 100% 0%, color-mix(in srgb, var(--rc-glow-b) var(--rc-glow-opacity), transparent), transparent),
            var(--rc-bg);
          color: var(--primary-text-color);
          font-family: var(--rc-font); border-radius: var(--rc-radius); border: 1px solid var(--rc-stroke);
          box-shadow: var(--rc-shadow); backdrop-filter: var(--rc-blur); -webkit-backdrop-filter: var(--rc-blur);
          padding: 20px; gap: 16px; }
        :host([appearance="glass"]) .title, :host([appearance="material"]) .title {
          font-size: 1.75rem; line-height: 1.2; font-weight: 700; letter-spacing: -0.02em; }
        :host([appearance="material"]) .title { font-weight: 500; letter-spacing: 0; }
        :host([appearance="glass"]) .recipients-title, :host([appearance="material"]) .recipients-title {
          font-size: 0.75rem; letter-spacing: 0.04em; text-transform: uppercase; }
        :host([appearance="material"]) .recipients-title { text-transform: none; letter-spacing: 0; font-size: 0.875rem; font-weight: 500; }

        /* Rooms: tiles on glass, filter chips on Material. */
        :host([appearance="glass"]:not([layout="compact"])) .chips.rooms { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 8px; }
        :host([appearance="glass"]:not([layout="compact"])) .chips.rooms .chip {
          flex-direction: column; align-items: flex-start; justify-content: space-between; gap: 8px; min-height: 68px;
          padding: 10px 12px; border-radius: var(--rc-chip-radius); text-align: start; font-weight: 600; }
        :host([appearance="glass"]) .chip, :host([appearance="material"]) .chip {
          background: var(--rc-surface); color: var(--primary-text-color); border: 1px solid var(--rc-stroke);
          backdrop-filter: var(--rc-blur); -webkit-backdrop-filter: var(--rc-blur); font-weight: 600; }
        :host([appearance="glass"]) .chip[aria-pressed="true"], :host([appearance="material"]) .chip[aria-pressed="true"] {
          background: var(--rc-selected); color: var(--rc-on-selected); border-color: transparent; }
        :host([appearance="material"]) .chip { border-radius: var(--rc-chip-radius); border-color: var(--rc-outline);
          background: transparent; min-height: 40px; padding: 6px 14px; }
        :host([appearance="material"]) .chip[aria-pressed="true"]::before { content: "✓"; font-weight: 700; }
        :host([appearance="glass"]) .toggles .chip { border-radius: 999px; }
        :host([appearance="glass"]) .chip.urgent:not([aria-pressed="true"]) { color: var(--warning-color); }
        :host([appearance="glass"]) .chip.urgent[aria-pressed="true"], :host([appearance="material"]) .chip.urgent[aria-pressed="true"] {
          background: var(--error-color); color: #fff; }
        :host([appearance="glass"]) .chip.dnd[aria-pressed="true"], :host([appearance="material"]) .chip.dnd[aria-pressed="true"] {
          background: #5e5ce6; color: #fff; }

        /* The talk orb: depth and a specular highlight; Material adds a primary→tertiary gradient. */
        :host([appearance="glass"]) button.talk, :host([appearance="material"]) button.talk {
          width: min(100%, 184px); background: var(--rc-orb); color: var(--text-primary-color); border: 1px solid rgba(255,255,255,0.3);
          box-shadow: 0 18px 50px var(--rc-orb-glow), inset 0 2px 0 rgba(255,255,255,0.45), inset 0 -12px 30px rgba(0,0,0,0.18);
          transition: transform 0.18s cubic-bezier(.2,.8,.2,1), box-shadow 0.2s; }
        :host([appearance="glass"]) button.talk.urgent, :host([appearance="material"]) button.talk.urgent { background: var(--error-color); color: #fff; }
        :host([appearance="glass"]) button.talk.live, :host([appearance="material"]) button.talk.live {
          background: radial-gradient(circle at 34% 28%, rgba(255,255,255,0.32), rgba(255,255,255,0) 46%), #d92d20; color: #fff; transform: scale(0.96);
          box-shadow: 0 0 0 calc(var(--level, 0) * 34px + 12px) rgba(217,45,32,0.18), 0 18px 50px rgba(217,45,32,0.45); }
        :host([appearance="glass"]) .destination, :host([appearance="material"]) .destination { font-size: 1.0625rem; }

        /* Panels: needs attention, tasks, doorbell, history. */
        :host([appearance="glass"]) .doorbell, :host([appearance="glass"]) .task, :host([appearance="glass"]) .history,
        :host([appearance="material"]) .doorbell, :host([appearance="material"]) .task, :host([appearance="material"]) .history {
          background: var(--rc-surface); border: 1px solid var(--rc-stroke); border-radius: 22px; padding: 12px 14px;
          backdrop-filter: var(--rc-blur); -webkit-backdrop-filter: var(--rc-blur); box-shadow: var(--rc-shadow); }
        :host([appearance="glass"]) .task > ha-icon, :host([appearance="material"]) .task > ha-icon { color: var(--warning-color); }
        :host([appearance="glass"]) .chip.done, :host([appearance="material"]) .chip.done {
          background: var(--rc-success); color: var(--rc-on-success); border-color: transparent; border-radius: 999px; }
        :host([appearance="glass"]) .history .call, :host([appearance="material"]) .history .call {
          background: transparent; border-radius: 0; padding: 8px 0; }
        :host([appearance="glass"]) .history .call + .call, :host([appearance="material"]) .history .call + .call {
          border-top: 1px solid var(--rc-stroke); }
        :host([appearance="material"]) .history .call + .call { border-top-color: var(--rc-surface-strong); }

        /* Talk / Type / Ask: an iOS segmented control on glass, a Material segmented button. */
        :host([appearance="glass"]) .communication nav[data-toolbar], :host([appearance="material"]) .communication nav[data-toolbar] {
          background: var(--rc-surface); border: 1px solid var(--rc-stroke); border-radius: 14px; padding: 3px; }
        :host([appearance="glass"]) .communication nav[data-toolbar] button[aria-pressed=true],
        :host([appearance="material"]) .communication nav[data-toolbar] button[aria-pressed=true] {
          background: var(--rc-seg-on); color: var(--rc-on-seg); box-shadow: 0 1px 3px rgba(0,0,0,0.12); }
        :host([appearance="glass"]) .communication nav[data-toolbar] button:not([aria-pressed=true]),
        :host([appearance="material"]) .communication nav[data-toolbar] button:not([aria-pressed=true]) {
          background: transparent; border-color: transparent; backdrop-filter: none; -webkit-backdrop-filter: none; }
        :host([appearance="glass"]) .communication nav[data-toolbar] button,
        :host([appearance="material"]) .communication nav[data-toolbar] button { border-color: transparent; }
        :host([appearance="material"]) .communication nav[data-toolbar] { border: 1px solid var(--rc-outline); border-radius: 22px; background: transparent; }
        /* Material: the check mark takes the selected chip's icon slot. */
        :host([appearance="material"]) .chip[aria-pressed="true"] ha-icon { display: none; }
        :host([appearance="material"]) .communication nav[data-toolbar] button { border-radius: 18px; }
        :host([appearance="glass"]) .communication dialog, :host([appearance="material"]) .communication dialog {
          border-radius: 28px; border: 1px solid var(--rc-stroke); backdrop-filter: var(--rc-blur); -webkit-backdrop-filter: var(--rc-blur);
          font-family: var(--rc-font); }
        :host([appearance="glass"]) .communication .chip, :host([appearance="material"]) .communication .chip { border-radius: 14px; }
        :host([appearance][layout="compact"]) ha-card { padding: 12px; gap: 10px; }
        :host([appearance="glass"]) .chips.rooms .chip, :host([appearance]) .communication nav[data-toolbar] button {
          overflow-wrap: normal; word-break: normal; hyphens: auto; }
        :host([appearance]) .communication nav[data-toolbar] button { padding: 6px; }
        :host([appearance="glass"][layout="kiosk"]) .chips.rooms { grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); }
        :host([appearance][layout="kiosk"]) ha-card { padding: 24px; gap: 18px; }
        :host([appearance][layout="compact"]) button.talk { width: 64px; }
        :host([appearance][layout="kiosk"]) button.talk { width: min(100%, 340px); }
        @media (prefers-reduced-transparency: reduce) {
          :host([appearance="glass"]) ha-card { background: var(--card-background-color); backdrop-filter: none; }
        }
      </style>
      <ha-card>
        <div class="head">
          <span class="title"></span>
          <span class="toggles"></span>
          <button class="chip small more" type="button" aria-expanded="false" hidden>
            <ha-icon icon="mdi:dots-horizontal"></ha-icon><span class="more-label">More</span>
          </button>
        </div>
        <div class="reply" role="status">
          <ha-icon icon="mdi:reply"></ha-icon><span class="reply-text"></span>
          <button class="chip small" type="button">Cancel</button>
        </div>
        <div class="doorbell" role="alert" hidden></div>
        <div class="tasks" role="region" aria-live="polite" hidden></div>
        <div class="workspace">
        <div class="recipients">
          <span class="recipients-title"></span>
          <div class="chips rooms" role="group" aria-label="Broadcast to"></div>
          <div class="chips people" role="group" aria-label="Call a person"></div>
        </div>
        <div class="composer">
        <div class="destination" aria-live="polite"></div>
        <div class="wrap">
          <button class="talk" type="button" aria-label="${this._idleLabel()}">
            <ha-icon icon="mdi:microphone"></ha-icon><span class="label">${this._idleLabel()}</span>
          </button>
        </div>
        <div class="status muted">${this._config.tap_to_talk ? this._tapText("tap_hint") : "Pick a room, hold the button and speak."}</div>
        </div></div>
        <div class="history" aria-label="Recent calls"></div>
      </ha-card>`;
    this.setAttribute("layout", layout);
    this.removeAttribute("expanded");
    this.shadowRoot.querySelector(".title").textContent = this._config.title;
    const more = this.shadowRoot.querySelector(".more");
    more.hidden = layout !== "simple";
    more.addEventListener("click", () => {
      this._expanded = !this._expanded;
      // Talk is the only mode the collapsed card can show.
      if (!this._expanded) this._communication?.mode("talk");
      this._renderMore();
      this._renderHistory();
    });
    this._renderMore();
    const talk = this.shadowRoot.querySelector(".talk");
    talk.addEventListener("pointerdown", (e) => { if (!this._config.tap_to_talk) this._start(e); });
    for (const ev of ["pointerup", "pointerleave", "pointercancel"]) {
      talk.addEventListener(ev, (e) => { if (!this._config.tap_to_talk) this._stop(e); });
    }
    talk.addEventListener("click", (e) => { if (this._config.tap_to_talk) this._toggle(e); });
    talk.addEventListener("contextmenu", (e) => e.preventDefault());
    talk.addEventListener("blur", (e) => this._stop(e));
    talk.addEventListener("keydown", (e) => {
      if (!this._config.tap_to_talk && [" ", "Enter"].includes(e.key) && !e.repeat) this._start(e);
    });
    talk.addEventListener("keyup", (e) => {
      if (!this._config.tap_to_talk && [" ", "Enter"].includes(e.key)) this._stop(e);
    });
    this.shadowRoot.querySelector(".reply button").addEventListener("click", () => {
      this._replyTo = null;
      this._target = this._config.default_room || "all";
      this._renderRooms();
    });
    this._renderRooms();
  }

  _renderMore() {
    const more = this.shadowRoot.querySelector(".more");
    const key = this._expanded ? "fewer_options" : "more_options";
    this.toggleAttribute("expanded", this._expanded);
    more.setAttribute("aria-expanded", String(this._expanded));
    const label = this._strings?.[key] || (this._expanded ? "Less" : "More");
    more.querySelector(".more-label").textContent = label;
    more.setAttribute("aria-label", label);
  }

  _chip(icon, label, pressed, onClick, cls = "") {
    const b = document.createElement("button");
    b.className = `chip ${cls}`.trim();
    b.type = "button";
    if (icon) {
      const i = document.createElement("ha-icon");
      i.setAttribute("icon", icon);
      b.append(i);
    }
    if (label) b.append(document.createTextNode(label));
    b.setAttribute("aria-pressed", String(pressed));
    b.addEventListener("click", onClick);
    return b;
  }

  _renderRooms() {
    const root = this.shadowRoot;
    const box = root && root.querySelector(".rooms");
    if (!box || !this._rooms) return;
    const reply = root.querySelector(".reply");
    reply.classList.toggle("on", Boolean(this._replyTo));
    root.querySelector(".reply-text").textContent = this._replyTo ? `Replying to ${this._roomName(this._replyTo)}` : "";
    box.replaceChildren();
    const entries = [
      { area_id: "all", name: "Everywhere", icon: "mdi:home-sound-out" },
      ...(this._floors || []).map((f) => ({ ...f, area_id: f.id, icon: f.icon || "mdi:floor-plan" })),
      ...this._rooms,
    ];
    for (const room of entries) {
      const chip = this._chip(room.icon, room.name, !this._person && room.area_id === this._target, () => {
        if (this._communication) this._communication.profileTargetOverride = true;
        this._target = room.area_id;
        this._person = null;
        this._renderRooms();
      });
      chip.title = room.speakers ? room.speakers.join(", ") : "All RoomCall speakers";
      box.append(chip);
    }
    const people = root.querySelector(".people");
    people.replaceChildren(
      ...(this._people || []).map((p) =>
        this._chip("mdi:account", p.name, this._person === p.entity_id, () => {
          if (this._communication) this._communication.profileTargetOverride = true;
          this._person = this._person === p.entity_id ? null : p.entity_id;
          this._renderRooms();
        }, "small"),
      ),
    );
    const profile = this._communication?.currentMode === "type"
      ? this._communication.capabilities?.profiles?.find(p => p.id === this._communication.profiles.value) : null;
    root.querySelector(".destination").textContent = profile && !this._communication.profileTargetOverride
      ? this._t("profile_destination", { name: profile.name }) : this._strings
      ? this._t("destination", { name: this._person
        ? this._people.find(p => p.entity_id === this._person)?.name || this._person
        : this._roomName(this._target) }) : "";
  }

  _renderToggles() {
    const box = this.shadowRoot && this.shadowRoot.querySelector(".toggles");
    if (!box || !this._hass) return;
    const dndState = this._dndEntity && this._hass.states[this._dndEntity];
    const dndOn = dndState?.state === "on";
    const chips = [
      this._chip(this._icons.urgent, "Urgent", this._urgent, () => {
        this._urgent = !this._urgent;
        this._renderToggles();
      }, "small urgent"),
    ];
    if (dndState) {
      const dnd = this._chip(this._icons.dnd, dndOn ? "DND on" : "", dndOn, () =>
        this._hass.callService("switch", "toggle", { entity_id: this._dndEntity }), "small dnd");
      dnd.title = "Do Not Disturb: only urgent calls play";
      chips.push(dnd);
    }
    box.replaceChildren(...chips);
    this.shadowRoot.querySelector(".talk").classList.toggle("urgent", this._urgent);
  }

  _renderHistory() {
    const box = this.shadowRoot.querySelector(".history");
    box.replaceChildren();
    // The collapsed simple card keeps only the latest call (and its reply button) in view.
    // Calls fetched under an earlier config never exceed the current `history` setting.
    const limit = Number(this._config.history) || 0;
    const collapsed = this._config.layout === "simple" && !this._expanded;
    for (const call of (this._history || []).slice(0, collapsed ? Math.min(1, limit) : limit)) {
      const row = document.createElement("div");
      row.className = "call";
      const from = call.from ? this._roomName(call.from) : call.kind === "talk" ? "Someone" : "Automation";
      const icon = call.ok ? (call.priority === "urgent" ? "mdi:alert" : "mdi:account-voice") : "mdi:alert-circle-outline";
      const to = call.target.split(",").map((t) => this._roomName(t.trim())).join(", ");
      row.innerHTML = `<ha-icon icon="${icon}"></ha-icon>
        <div class="what"><div>${esc(from)} → ${esc(to)} <span class="muted">· ${esc(timeAgo(call.at))}</span></div>
          ${call.text ? `<div class="text">“${esc(call.text)}”</div>` : ""}</div>`;
      const actions = document.createElement("div");
      if (call.has_audio) {
        const play = document.createElement("button");
        play.title = "Play";
        play.setAttribute("aria-label", this._t("replay"));
        play.innerHTML = `<ha-icon icon="mdi:play-circle-outline"></ha-icon>`;
        play.addEventListener("click", () => this._replay(call.id));
        actions.append(play);
      }
      if (call.requires_ack && this._communication) {
        actions.append(this._communication.button("acknowledge", async () => {
          await this._communication.command("acknowledge", { id: call.id });
          this._loadHistory();
        }));
      }
      if (call.from && (this._rooms || []).some((r) => r.area_id === call.from)) {
        const reply = document.createElement("button");
        reply.title = `Reply to ${from}`;
        reply.setAttribute("aria-label", this._t("reply_to", { name: from }));
        reply.innerHTML = `<ha-icon icon="mdi:reply"></ha-icon>`;
        reply.addEventListener("click", () => {
          this._replyTo = call.from;
          this._target = call.from;
          this._person = null;
          this._renderRooms();
        });
        actions.append(reply);
      }
      row.append(actions);
      box.append(row);
    }
  }

  async _replay(id) {
    try {
      const res = await this._hass.fetchWithAuth(`/api/roomcall/history/${id}.mp3`);
      if (!res.ok) throw new Error(res.status);
      const url = URL.createObjectURL(await res.blob());
      const audio = new Audio(url);
      audio.addEventListener("ended", () => URL.revokeObjectURL(url));
      await audio.play();
    } catch (err) {
      this._setStatus(`Could not play that call (${err.message || err})`, "err");
    }
  }

  _setStatus(text, cls = "muted") {
    const el = this.shadowRoot.querySelector(".status");
    el.textContent = text;
    el.className = `status ${cls}`;
  }

  _tapText(key) {
    return this._strings?.[key] || TAP_TEXT[key];
  }

  _idleLabel() {
    return this._config.tap_to_talk ? this._tapText("tap_to_talk") : "Hold to talk";
  }

  /** The first render can beat the translated catalog, so relabel an idle button once it loads. */
  _relabelTap() {
    const root = this.shadowRoot;
    const talk = root?.querySelector(".talk");
    if (!talk || !this._config.tap_to_talk || this._recorder || this._live || this._starting) return;
    talk.setAttribute("aria-label", this._idleLabel());
    root.querySelector(".label").textContent = this._idleLabel();
    const status = root.querySelector(".status");
    if (status.textContent === TAP_TEXT.tap_hint) status.textContent = this._tapText("tap_hint");
  }

  _toggle(e) {
    if (this._live?.done) return;
    if (this._recorder || this._live || this._starting) this._stop(e);
    else this._start(e);
  }

  _talkOpened(talk) {
    if (!this._config.tap_to_talk) return;
    talk.setAttribute("aria-pressed", "true");
    this._tapTimer = setTimeout(() => this._stop({ preventDefault() {} }), TAP_LIMIT_MS);
  }

  _talkClosed(talk) {
    clearTimeout(this._tapTimer);
    talk.removeAttribute("aria-pressed");
    talk.classList.remove("live");
    this.shadowRoot.querySelector(".label").textContent = this._idleLabel();
  }

  async _start(e) {
    e.preventDefault();
    if (this._recorder || this._live || this._starting) return;
    this._pressed = true;
    this._starting = true;
    try {
      this._stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      this._starting = false;
      this._setStatus(
        window.isSecureContext
          ? "Microphone blocked — allow mic access for Home Assistant."
          : "The microphone needs HTTPS — open Home Assistant over https:// or in the app.",
        "err",
      );
      return;
    }
    this._starting = false;
    if (!this._pressed || !this.isConnected) {
      this._stream.getTracks().forEach((track) => track.stop());
      return;
    }
    const talk = this.shadowRoot.querySelector(".talk");
    this._ctx = new AudioContext();
    const analyser = this._ctx.createAnalyser();
    analyser.fftSize = 512;
    this._ctx.createMediaStreamSource(this._stream).connect(analyser);
    const buf = new Uint8Array(analyser.fftSize);
    this._peak = 0;
    const tick = () => {
      analyser.getByteTimeDomainData(buf);
      let peak = 0;
      for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
      this._peak = Math.max(this._peak, peak / 128);
      talk.style.setProperty("--level", (peak / 128).toFixed(2));
      this._raf = requestAnimationFrame(tick);
    };
    tick();
    if (this._config.live && !this._liveUnsupported) {
      this._startLive(talk);
      return;
    }
    this._chunks = [];
    this._recorder = new MediaRecorder(this._stream);
    this._recorder.ondataavailable = (ev) => ev.data.size && this._chunks.push(ev.data);
    this._recorder.onstop = () => this._send();
    this._recorder.start();
    this._startedAt = performance.now();
    talk.classList.add("live");
    this.shadowRoot.querySelector(".label").textContent = this._config.tap_to_talk ? this._tapText("tap_to_send") : "Release to send";
    this._talkOpened(talk);
    const who = this._person
      ? (this._people.find((p) => p.entity_id === this._person) || {}).name
      : this._target === "all" ? "everywhere" : this._roomName(this._target);
    this._setStatus(`${this._urgent ? "Urgent: talking" : "Talking"} to ${who}…`);
  }

  _stop(e) {
    e.preventDefault();
    this._pressed = false;
    if (this._live) {
      this._stopLive();
      return;
    }
    if (!this._recorder || this._recorder.state !== "recording") return;
    this._recorder.stop();
    cancelAnimationFrame(this._raf);
    this._stream.getTracks().forEach((t) => t.stop());
    this._talkClosed(this.shadowRoot.querySelector(".talk"));
  }

  _talkingTo() {
    return this._person
      ? (this._people.find((p) => p.entity_id === this._person) || {}).name
      : this._target === "all" ? "everywhere" : this._roomName(this._target);
  }

  // Live call: speakers start while you are still talking.
  async _startLive(talk) {
    const live = { handlerId: null, queue: [], done: false, unsub: null };
    this._live = live;
    talk.classList.add("live");
    this.shadowRoot.querySelector(".label").textContent = this._config.tap_to_talk ? this._tapText("tap_to_finish") : "Release to finish";
    this._talkOpened(talk);
    this._setStatus(`${this._urgent ? "Urgent: live" : "Live"} to ${this._talkingTo()}…`);
    const send = (pcm) => {
      const frame = new Uint8Array(1 + pcm.byteLength);
      frame[0] = live.handlerId;
      frame.set(new Uint8Array(pcm), 1);
      this._hass.connection.socket.send(frame);
    };
    try {
      if (!RoomCallCard._workletUrl) {
        RoomCallCard._workletUrl = URL.createObjectURL(
          new Blob([PCM_WORKLET], { type: "text/javascript" }),
        );
      }
      await this._ctx.audioWorklet.addModule(RoomCallCard._workletUrl);
      if (live.done) return;
      const node = new AudioWorkletNode(this._ctx, "roomcall-pcm16");
      const mute = this._ctx.createGain();
      mute.gain.value = 0;
      this._ctx.createMediaStreamSource(this._stream).connect(node);
      node.connect(mute).connect(this._ctx.destination); // keeps the worklet running
      node.port.onmessage = (ev) => {
        if (live.done) return;
        if (live.handlerId === null) live.queue.push(ev.data);
        else send(ev.data);
      };
      const msg = { type: "roomcall/live", target: this._target };
      if (this._person) msg.person = this._person;
      if (this._urgent) msg.priority = "urgent";
      if (this._config.room) msg.from = this._config.room;
      live.unsub = await this._hass.connection.subscribeMessage((ev) => this._onLive(live, ev, send), msg);
      if (!this.isConnected) { live.unsub(); return; }
      if (live.done && live.handlerId !== null) this._endLiveAudio(live);
    } catch (err) {
      const unknown = err && (err.code === "unknown_command" || String(err.message).includes("Unknown command"));
      if (unknown) this._liveUnsupported = true;
      this._finishLive(live);
      this._setStatus(
        unknown
          ? (this._config.tap_to_talk
            ? this._tapText("tap_live_unsupported")
            : "Live calls need RoomCall 0.5 or newer in Home Assistant — hold again to send a recording.")
          : (err && err.message) || "Could not start the call",
        "err",
      );
    }
  }

  _onLive(live, ev, send) {
    if (ev.type === "ready") {
      live.handlerId = ev.handler_id;
      for (const pcm of live.queue.splice(0)) send(pcm);
      if (live.done) this._endLiveAudio(live);
    } else if (ev.type === "delivered") {
      const names = (ev.results || []).filter((r) => r.ok).map((r) => r.speaker);
      if (!live.done) this._setStatus(this._t("accepted", { count: names.length }), names.length ? "ok" : "err");
      const failed = (ev.results || []).filter((r) => !r.ok).map((r) => r.speaker);
      if (failed.length) this._setStatus(`Not delivered to: ${failed.join(", ")}`, "err");
      live.delivered = names;
      live.failed = failed;
    } else if (ev.type === "done") {
      live.unsub && live.unsub();
      const where = (live.delivered || []).join(", ");
      const failed = live.failed || [];
      this._setStatus(failed.length ? this._t("failed", { speakers: failed.join(", ") })
        : this._t("accepted", { count: live.delivered?.length || 0 }), failed.length ? "err" : "ok");
      if (this._replyTo) {
        this._replyTo = null;
        this._renderRooms();
      }
      this._urgent = false;
      this._renderToggles();
      this._loadHistory();
    } else if (ev.type === "error") {
      live.unsub && live.unsub();
      this._setStatus(ev.message || "The call failed", "err");
    }
  }

  _endLiveAudio(live) {
    if (live.ended) return;
    live.ended = true;
    this._hass.connection.socket.send(new Uint8Array([live.handlerId])); // empty frame = end
  }

  _stopLive() {
    const live = this._live;
    live.done = true;
    if (live.handlerId !== null) this._endLiveAudio(live);
    this._finishLive(live);
    if (this._peak < SILENCE_PEAK) {
      this._setStatus("No sound from your microphone — check which mic the browser is using.", "err");
    } else {
      this._setStatus("Finishing…");
    }
  }

  _finishLive(live) {
    live.done = true;
    this._live = null;
    cancelAnimationFrame(this._raf);
    this._stream && this._stream.getTracks().forEach((t) => t.stop());
    this._ctx && this._ctx.close();
    this._talkClosed(this.shadowRoot.querySelector(".talk"));
  }

  async _send() {
    this._ctx && this._ctx.close();
    const type = this._recorder.mimeType || "audio/webm";
    const blob = new Blob(this._chunks, { type });
    this._recorder = null;
    if (performance.now() - this._startedAt < 300) {
      this._setStatus(this._config.tap_to_talk ? this._tapText("tap_too_short") : "Too short — hold the button while you talk.");
      return;
    }
    if (this._peak < SILENCE_PEAK) {
      this._setStatus("No sound from your microphone — check which mic the browser is using.", "err");
      return;
    }
    const talk = this.shadowRoot.querySelector(".talk");
    talk.disabled = true;
    this._setStatus("Sending…");
    const params = new URLSearchParams({ target: this._target });
    if (this._person) params.set("person", this._person);
    if (this._urgent) params.set("priority", "urgent");
    if (this._config.room) params.set("from", this._config.room);
    try {
      const res = await this._hass.fetchWithAuth(`/api/roomcall/talk?${params}`, {
        method: "POST",
        headers: { "Content-Type": type },
        body: blob,
      });
      const body = await res.json().catch(() => ({}));
      const results = body.results || [];
      const failed = results.filter((r) => !r.ok);
      const secs = body.seconds ? ` ${body.seconds} s` : "";
      const skipped = Object.keys(body.skipped || {});
      if (res.ok && !failed.length && results.length) {
        let text = this._t("accepted", { count: results.length });
        if (skipped.length) text += ` Skipped (quiet hours): ${skipped.join(", ")}.`;
        this._setStatus(text, "ok");
        if (this._replyTo) {
          this._replyTo = null;
          this._renderRooms();
        }
      } else if (failed.length) {
        this._setStatus(`Not delivered to: ${failed.map((r) => r.speaker).join(", ")}`, "err");
      } else {
        this._setStatus(body.message || `Failed (${res.status})`, "err");
      }
    } catch (err) {
      this._setStatus(`Send failed: ${err.message || err}`, "err");
    } finally {
      talk.disabled = false;
      this._urgent = false;
      this._renderToggles();
      this._loadHistory();
    }
  }
}

// English fallbacks; the translated catalog (editor_*) replaces them once it loads.
const EDITOR_TEXT = {
  title: "Title",
  room: "This card's room",
  default_room: "Room selected at start",
  rooms: "Only show these rooms",
  layout: "Layout",
  live: "Live calls (play while you talk)",
  tap_to_talk: "Tap to start and stop",
  people: "Show people buttons",
  history: "Recent calls to show",
  appearance: "Appearance",
  theme: "Light or dark",
  color: "Button colour",
  layout_simple: "Simple (rooms and the talk button; More shows the rest)",
  layout_default: "Standard",
  layout_compact: "Compact (one row)",
  layout_kiosk: "Kiosk (big button for a wall tablet)",
  appearance_glass: "Glass (Apple-like, frosted)",
  appearance_material: "Material (tonal colours from your colour)",
  appearance_home_assistant: "Home Assistant theme",
  theme_auto: "Follow Home Assistant",
  theme_light: "Light",
  theme_dark: "Dark",
  help_appearance: "Glass and Material use their own colours; Home Assistant theme keeps your dashboard's theme.",
  help_color: "The talk button, selected rooms and (with Material) the whole palette come from this colour.",
  help_room: "Where this dashboard is (for example a wall tablet). Replies to its calls come back here.",
  help_rooms: "Leave empty for every room with a RoomCall speaker.",
  help_live: "Turn off to record the whole message first, then send it.",
  help_tap_to_talk: "For anyone who finds holding a button hard. The microphone stays on until you tap again, for up to 2 minutes.",
  help_layout: "Simple puts typing, asking, people, Urgent, Do Not Disturb, settings and older calls behind More.",
};
const select = (t, name, values) => ({
  name,
  selector: {
    select: {
      mode: "dropdown",
      options: values.map((value) => ({ value, label: t(`${name}_${value.replace("-", "_")}`) })),
    },
  },
});
const editorSchema = (t) => [
  { name: "title", selector: { text: {} } },
  {
    type: "grid",
    name: "",
    schema: [
      { name: "room", selector: { area: {} } },
      { name: "default_room", selector: { area: {} } },
    ],
  },
  { name: "rooms", selector: { area: { multiple: true } } },
  select(t, "layout", LAYOUTS),
  {
    type: "grid",
    name: "",
    schema: [
      { name: "live", selector: { boolean: {} } },
      { name: "tap_to_talk", selector: { boolean: {} } },
      { name: "people", selector: { boolean: {} } },
    ],
  },
  { name: "history", selector: { number: { min: 0, max: 10, mode: "box" } } },
  select(t, "appearance", APPEARANCES),
  {
    type: "grid",
    name: "",
    schema: [select(t, "theme", THEMES), { name: "color", selector: { color_rgb: {} } }],
  },
];

class RoomCallCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { live: true, people: true, history: 3, layout: "default", appearance: "glass", theme: "auto", ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
    if (!this._strings && !this._loadingStrings) this._loadStrings();
  }

  async _loadStrings() {
    this._loadingStrings = true;
    try {
      this._strings = await this._hass.callApi("GET", `roomcall/locale?language=${encodeURIComponent(this._hass.language || "en")}`);
      if (this._form) this._render();
    } catch (error) {
      console.warn("RoomCall editor strings", error);
    }
  }

  _text(key) {
    return this._strings?.[`editor_${key}`] || EDITOR_TEXT[key];
  }

  _render() {
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (field) => this._text(field.name) || field.name;
      this._form.computeHelper = (field) => this._text(`help_${field.name}`);
      this._form.addEventListener("value-changed", (ev) => {
        const config = { ...ev.detail.value };
        for (const key of ["room", "default_room"]) if (!config[key]) delete config[key];
        if (Array.isArray(config.rooms) && !config.rooms.length) delete config.rooms;
        this._config = config;
        this.dispatchEvent(
          new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }),
        );
      });
      this.append(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = editorSchema((key) => this._text(key));
    this._form.data = this._config;
  }
}

if (!customElements.get("roomcall-card")) customElements.define("roomcall-card", RoomCallCard);
if (!customElements.get("roomcall-card-editor")) {
  customElements.define("roomcall-card-editor", RoomCallCardEditor);
}
window.customCards = window.customCards || [];
window.customCards.push({
  type: "roomcall-card",
  name: "RoomCall",
  description: "Hold-to-talk intercom: talk to a room, a person or the whole house.",
  preview: true,
  documentationURL: "https://daredoole.github.io/RoomCall/home-assistant/#3-add-the-push-to-talk-card",
});
console.info(`%c ROOMCALL-CARD %c ${CARD_VERSION} `, "background:#4f8cff;color:#fff", "");
