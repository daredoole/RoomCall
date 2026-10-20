/* RoomCall push-to-talk card for Home Assistant dashboards. */
const CARD_VERSION = "0.2.0";
// Peak level (0–1) below which a recording is treated as silence.
const SILENCE_PEAK = 0.04;
const LAYOUTS = ["default", "compact", "kiosk"];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

function timeAgo(iso) {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

class RoomCallCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Intercom" };
  }

  setConfig(config) {
    if (config.layout && !LAYOUTS.includes(config.layout)) {
      throw new Error(`layout must be one of: ${LAYOUTS.join(", ")}`);
    }
    this._config = { title: "Intercom", layout: "default", history: 3, people: true, ...config };
    const fromUrl = new URLSearchParams(window.location.search).get("roomcall");
    this._replyTo = fromUrl || null;
    this._target = fromUrl || this._config.default_room || "all";
    this._person = null;
    this._urgent = false;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
    else this._renderToggles();
  }

  getCardSize() {
    return this._config.layout === "compact" ? 3 : 7;
  }

  getGridOptions() {
    if (this._config.layout === "compact") return { columns: 12, rows: 3, min_rows: 2 };
    if (this._config.layout === "kiosk") return { columns: "full", rows: 8, min_rows: 6 };
    return { columns: 12, rows: 7, min_rows: 5 };
  }

  async _load() {
    try {
      const res = await this._hass.callApi("GET", "roomcall/rooms");
      const only = this._config.rooms;
      this._rooms = only ? res.rooms.filter((r) => only.includes(r.area_id) || only.includes(r.name)) : res.rooms;
      this._count = res.speakers;
      this._dndEntity = res.dnd_entity;
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
  }

  async _loadHistory() {
    if (!this._config.history || this._config.layout !== "default") return;
    try {
      const res = await this._hass.callApi("GET", "roomcall/history");
      this._history = res.calls.slice(0, this._config.history);
    } catch {
      this._history = [];
    }
    this._renderHistory();
  }

  _roomName(id) {
    if (!id || id === "all") return "Everywhere";
    return (this._rooms || []).find((r) => r.area_id === id)?.name || id;
  }

  _render() {
    const layout = this._config.layout;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 16px; display: flex; flex-direction: column; gap: 12px; height: 100%; box-sizing: border-box; }
        .head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
        .title { font-size: 18px; font-weight: 500; }
        .muted { color: var(--secondary-text-color); font-size: 13px; }
        .toggles { display: flex; gap: 6px; }
        .reply { display: none; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 12px;
          background: color-mix(in srgb, var(--primary-color) 15%, transparent); font-size: 14px; }
        .reply.on { display: flex; }
        .reply button { margin-left: auto; }
        .chips { display: flex; flex-wrap: wrap; gap: 8px; }
        .chip {
          border: 1px solid var(--divider-color); background: var(--card-background-color);
          color: var(--primary-text-color); border-radius: 999px; padding: 8px 14px;
          font-family: inherit; font-size: 14px; cursor: pointer; display: inline-flex; gap: 6px; align-items: center;
        }
        .chip.small { padding: 5px 10px; font-size: 12px; }
        .chip[aria-pressed="true"] { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
        .chip.urgent[aria-pressed="true"] { background: var(--error-color, #db4437); border-color: var(--error-color, #db4437); }
        .chip.dnd[aria-pressed="true"] { background: var(--warning-color, #ffa600); border-color: var(--warning-color, #ffa600); color: #000; }
        .chip ha-icon { --mdc-icon-size: 18px; }
        .chip.small ha-icon { --mdc-icon-size: 16px; }
        .people:empty { display: none; }
        .wrap { flex: 1; display: grid; place-items: center; min-height: 150px; }
        button.talk {
          width: min(52vw, 170px); aspect-ratio: 1; border-radius: 50%; border: 0; cursor: pointer;
          background: var(--primary-color); color: var(--text-primary-color, #fff);
          font-family: inherit; font-size: 17px; font-weight: 600; touch-action: none; user-select: none;
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
        .status { text-align: center; min-height: 1.3em; font-size: 14px; }
        .ok { color: var(--success-color, #43a047); } .err { color: var(--error-color, #db4437); }
        .history { display: grid; gap: 6px; }
        .history:empty { display: none; }
        .call { display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: center;
          padding: 8px 10px; border-radius: 10px; background: color-mix(in srgb, var(--primary-text-color) 5%, transparent); }
        .call .what { font-size: 13px; line-height: 1.3; overflow: hidden; }
        .call .text { color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .call button { border: 0; background: none; color: var(--primary-color); cursor: pointer; padding: 4px; }
        .call ha-icon { --mdc-icon-size: 20px; }

        :host([layout="compact"]) ha-card { flex-direction: row; align-items: center; flex-wrap: wrap; }
        :host([layout="compact"]) .head, :host([layout="compact"]) .people, :host([layout="compact"]) .history { display: none; }
        :host([layout="compact"]) .chips.rooms { flex: 1; }
        :host([layout="compact"]) .wrap { flex: 0 0 auto; min-height: 0; }
        :host([layout="compact"]) button.talk { width: 64px; font-size: 0; gap: 0; }
        :host([layout="compact"]) button.talk ha-icon { --mdc-icon-size: 28px; }
        :host([layout="compact"]) .status { flex-basis: 100%; }

        :host([layout="kiosk"]) ha-card { padding: 24px; gap: 18px; }
        :host([layout="kiosk"]) .title { font-size: 26px; }
        :host([layout="kiosk"]) .chip { padding: 14px 22px; font-size: 18px; }
        :host([layout="kiosk"]) .chip ha-icon { --mdc-icon-size: 24px; }
        :host([layout="kiosk"]) button.talk { width: min(70vw, 340px); font-size: 26px; }
        :host([layout="kiosk"]) button.talk ha-icon { --mdc-icon-size: 72px; }
        :host([layout="kiosk"]) .status { font-size: 18px; }
        :host([layout="kiosk"]) .history { display: none; }
      </style>
      <ha-card>
        <div class="head">
          <span class="title"></span>
          <span class="toggles"></span>
        </div>
        <div class="reply" role="status">
          <ha-icon icon="mdi:reply"></ha-icon><span class="reply-text"></span>
          <button class="chip small" type="button">Cancel</button>
        </div>
        <div class="chips rooms" role="group" aria-label="Broadcast to"></div>
        <div class="chips people" role="group" aria-label="Call a person"></div>
        <div class="wrap">
          <button class="talk" type="button" aria-label="Hold to talk">
            <ha-icon icon="mdi:microphone"></ha-icon><span class="label">Hold to talk</span>
          </button>
        </div>
        <div class="status muted">Pick a room, hold the button and speak.</div>
        <div class="history" aria-label="Recent calls"></div>
      </ha-card>`;
    this.setAttribute("layout", layout);
    this.shadowRoot.querySelector(".title").textContent = this._config.title;
    const talk = this.shadowRoot.querySelector(".talk");
    talk.addEventListener("pointerdown", (e) => this._start(e));
    for (const ev of ["pointerup", "pointerleave", "pointercancel"]) talk.addEventListener(ev, (e) => this._stop(e));
    talk.addEventListener("contextmenu", (e) => e.preventDefault());
    this.shadowRoot.querySelector(".reply button").addEventListener("click", () => {
      this._replyTo = null;
      this._target = this._config.default_room || "all";
      this._renderRooms();
    });
    this._renderRooms();
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
    const entries = [{ area_id: "all", name: "Everywhere", icon: "mdi:home-sound-out" }, ...this._rooms];
    for (const room of entries) {
      const chip = this._chip(room.icon, room.name, !this._person && room.area_id === this._target, () => {
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
          this._person = this._person === p.entity_id ? null : p.entity_id;
          this._renderRooms();
        }, "small"),
      ),
    );
  }

  _renderToggles() {
    const box = this.shadowRoot && this.shadowRoot.querySelector(".toggles");
    if (!box || !this._hass) return;
    const dndState = this._dndEntity && this._hass.states[this._dndEntity];
    const dndOn = dndState?.state === "on";
    const chips = [
      this._chip("mdi:alert", "Urgent", this._urgent, () => {
        this._urgent = !this._urgent;
        this._renderToggles();
      }, "small urgent"),
    ];
    if (dndState) {
      const dnd = this._chip("mdi:bell-sleep", dndOn ? "DND on" : "", dndOn, () =>
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
    for (const call of this._history || []) {
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
        play.innerHTML = `<ha-icon icon="mdi:play-circle-outline"></ha-icon>`;
        play.addEventListener("click", () => this._replay(call.id));
        actions.append(play);
      }
      if (call.from) {
        const reply = document.createElement("button");
        reply.title = `Reply to ${from}`;
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

  async _start(e) {
    e.preventDefault();
    if (this._recorder) return;
    try {
      this._stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      this._setStatus(
        window.isSecureContext
          ? "Microphone blocked — allow mic access for Home Assistant."
          : "The microphone needs HTTPS — open Home Assistant over https:// or in the app.",
        "err",
      );
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
    this._chunks = [];
    this._recorder = new MediaRecorder(this._stream);
    this._recorder.ondataavailable = (ev) => ev.data.size && this._chunks.push(ev.data);
    this._recorder.onstop = () => this._send();
    this._recorder.start();
    this._startedAt = performance.now();
    talk.classList.add("live");
    this.shadowRoot.querySelector(".label").textContent = "Release to send";
    const who = this._person
      ? (this._people.find((p) => p.entity_id === this._person) || {}).name
      : this._target === "all" ? "everywhere" : this._roomName(this._target);
    this._setStatus(`${this._urgent ? "Urgent: talking" : "Talking"} to ${who}…`);
  }

  _stop(e) {
    e.preventDefault();
    if (!this._recorder || this._recorder.state !== "recording") return;
    this._recorder.stop();
    cancelAnimationFrame(this._raf);
    this._stream.getTracks().forEach((t) => t.stop());
    const talk = this.shadowRoot.querySelector(".talk");
    talk.classList.remove("live");
    this.shadowRoot.querySelector(".label").textContent = "Hold to talk";
  }

  async _send() {
    this._ctx && this._ctx.close();
    const type = this._recorder.mimeType || "audio/webm";
    const blob = new Blob(this._chunks, { type });
    this._recorder = null;
    if (performance.now() - this._startedAt < 300) {
      this._setStatus("Too short — hold the button while you talk.");
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
      if (res.ok) {
        let text = `Delivered${secs} to ${results.map((r) => r.speaker).join(", ")}.`;
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

if (!customElements.get("roomcall-card")) customElements.define("roomcall-card", RoomCallCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "roomcall-card",
  name: "RoomCall",
  description: "Hold-to-talk intercom: talk to a room, a person or the whole house.",
  preview: false,
});
console.info(`%c ROOMCALL-CARD %c ${CARD_VERSION} `, "background:#4f8cff;color:#fff", "");
