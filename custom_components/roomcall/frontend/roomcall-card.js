/* RoomCall push-to-talk card for Home Assistant dashboards. */
const CARD_VERSION = "0.10.0";
// Peak level (0–1) below which a recording is treated as silence.
const SILENCE_PEAK = 0.04;
const LAYOUTS = ["default", "compact", "kiosk"];
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

class RoomCallCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Intercom" };
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
    this._config = {
      title: "Intercom",
      layout: "default",
      history: 3,
      people: true,
      live: true,
      ...config,
    };
    const fromUrl = new URLSearchParams(window.location.search).get("roomcall");
    this._replyTo = fromUrl || null;
    this._target = fromUrl || this._config.default_room || "all";
    this._person = null;
    this._urgent = false;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
    if (this._hass) this._load();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
    else this._renderToggles();
  }

  getCardSize() {
    const height = this.shadowRoot?.querySelector("ha-card")?.getBoundingClientRect().height;
    return height ? Math.ceil(height / 50) : this._config.layout === "compact" ? 3 : 7;
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
      this._count = res.speakers;
      this._configured = res.configured !== false;
      this.shadowRoot.querySelector(".talk").disabled = !this._configured;
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
    try {
      const strings = await this._hass.callApi("GET", `roomcall/locale?language=${encodeURIComponent(this._hass.language || "en")}`);
      this._strings = strings;
      this.shadowRoot.querySelector(".recipients-title").textContent = strings.recipients;
      this._renderRooms();
      if (!this._configured) this._setStatus(strings.not_ready, "muted");
      const { RoomCallControls } = await import(`./roomcall-controls.js?v=${CARD_VERSION}`);
      if (this.isConnected && !this._communication) this._communication = new RoomCallControls(this, strings);
    } catch (error) { console.warn("RoomCall communication controls", error); }
  }

  disconnectedCallback() {
    window.removeEventListener("blur", this._windowBlur);
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
        :host { display: block; min-width: 0; min-height: 0; height: 100%; container: roomcall / inline-size; }
        ha-card { padding: 16px; display: flex; flex-direction: column; gap: 12px; height: 100%; box-sizing: border-box;
          min-width: 0; min-height: 0; overflow: auto; }
        ha-card > * { flex-shrink: 0; min-width: 0; }
        .workspace { display:grid; gap:20px; min-width:0; }
        .recipients,.composer { min-width:0; display:grid; gap:12px; align-content:start; }
        .recipients-title { color:var(--secondary-text-color); font-size:0.8125rem; font-weight:600; }
        .destination { text-align:center; font-size:0.9375rem; font-weight:600; overflow-wrap:anywhere; }
        .head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px; }
        .title { font-size: 18px; font-weight: 500; overflow-wrap: anywhere; }
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
          max-width: 100%; overflow-wrap: anywhere;
          min-height:44px; box-sizing:border-box;
        }
        .chip.small { padding: 5px 10px; font-size: 12px; }
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
        .status { text-align: center; min-height: 1.3em; font-size: 14px; overflow-wrap: anywhere; }
        .ok { color: var(--success-color, #43a047); } .err { color: var(--error-color, #db4437); }
        .history { display: grid; gap: 6px; }
        .history:empty { display: none; }
        .call { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; align-items: center;
          padding: 8px 10px; border-radius: 10px; background: color-mix(in srgb, var(--primary-text-color) 5%, transparent); }
        .call .what { font-size: 13px; line-height: 1.3; overflow: hidden; }
        .call .text { color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .call button { border: 0; background: none; color: var(--primary-color); cursor: pointer; padding: 4px; min-width:44px; min-height:44px; }
        .call ha-icon { --mdc-icon-size: 20px; }

        :host([layout="compact"]) ha-card { padding:12px; gap:10px; }
        :host([layout="compact"]) .head, :host([layout="compact"]) .recipients-title, :host([layout="compact"]) .history { display: none; }
        :host([layout="compact"]) .workspace { gap:12px; }
        :host([layout="compact"]) .chips.rooms { flex: 1; }
        :host([layout="compact"]) .wrap { flex: 0 0 auto; min-height: 0; }
        :host([layout="compact"]) button.talk { width: 64px; font-size: 0; gap: 0; }
        :host([layout="compact"]) button.talk ha-icon { --mdc-icon-size: 28px; }
        :host([layout="compact"]) .status { flex-basis: 100%; }

        :host([layout="kiosk"]) ha-card { padding: 24px; gap: 18px; }
        :host([layout="kiosk"]) .title { font-size: 26px; }
        :host([layout="kiosk"]) .chip { padding: 14px 22px; font-size: 18px; }
        :host([layout="kiosk"]) .chip ha-icon { --mdc-icon-size: 24px; }
        :host([layout="kiosk"]) button.talk { width: min(100%, 340px); font-size: 26px; }
        :host([layout="kiosk"]) button.talk ha-icon { --mdc-icon-size: 72px; }
        :host([layout="kiosk"]) .status { font-size: 18px; }
        :host([layout="kiosk"]) .history { display: none; }
        @container roomcall (min-width: 640px) {
          .workspace { grid-template-columns:minmax(0,1fr) minmax(0,1fr); align-items:start; }
          .composer { border-inline-start:1px solid var(--divider-color); padding-inline-start:20px; }
          .chips.rooms { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); }
          .chips.rooms .chip { justify-content:flex-start; }
        }
        @media (prefers-reduced-motion:reduce) { button.talk { transition:none; } }
        button:focus-visible { outline:2px solid var(--primary-color); outline-offset:3px; }
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
        <div class="workspace">
        <div class="recipients">
          <span class="recipients-title"></span>
          <div class="chips rooms" role="group" aria-label="Broadcast to"></div>
          <div class="chips people" role="group" aria-label="Call a person"></div>
        </div>
        <div class="composer">
        <div class="destination" aria-live="polite"></div>
        <div class="wrap">
          <button class="talk" type="button" aria-label="Hold to talk">
            <ha-icon icon="mdi:microphone"></ha-icon><span class="label">Hold to talk</span>
          </button>
        </div>
        <div class="status muted">Pick a room, hold the button and speak.</div>
        </div></div>
        <div class="history" aria-label="Recent calls"></div>
      </ha-card>`;
    this.setAttribute("layout", layout);
    this.shadowRoot.querySelector(".title").textContent = this._config.title;
    const talk = this.shadowRoot.querySelector(".talk");
    talk.addEventListener("pointerdown", (e) => this._start(e));
    for (const ev of ["pointerup", "pointerleave", "pointercancel"]) talk.addEventListener(ev, (e) => this._stop(e));
    talk.addEventListener("contextmenu", (e) => e.preventDefault());
    talk.addEventListener("keydown", (e) => {
      if ([" ", "Enter"].includes(e.key) && !e.repeat) this._start(e);
    });
    talk.addEventListener("keyup", (e) => {
      if ([" ", "Enter"].includes(e.key)) this._stop(e);
    });
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
    root.querySelector(".destination").textContent = this._strings
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
      if (call.requires_ack && this._communication) {
        actions.append(this._communication.button("acknowledge", async () => {
          await this._communication.command("acknowledge", { id: call.id });
          this._loadHistory();
        }));
      }
      if (call.from && (this._rooms || []).some((r) => r.area_id === call.from)) {
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
    this.shadowRoot.querySelector(".label").textContent = "Release to send";
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
    const talk = this.shadowRoot.querySelector(".talk");
    talk.classList.remove("live");
    this.shadowRoot.querySelector(".label").textContent = "Hold to talk";
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
    this.shadowRoot.querySelector(".label").textContent = "Release to finish";
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
          ? "Live calls need RoomCall 0.5 or newer in Home Assistant — hold again to send a recording."
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

const EDITOR_SCHEMA = [
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
  {
    name: "layout",
    selector: {
      select: {
        mode: "dropdown",
        options: [
          { value: "default", label: "Standard" },
          { value: "compact", label: "Compact (one row)" },
          { value: "kiosk", label: "Kiosk (big button for a wall tablet)" },
        ],
      },
    },
  },
  {
    type: "grid",
    name: "",
    schema: [
      { name: "live", selector: { boolean: {} } },
      { name: "people", selector: { boolean: {} } },
    ],
  },
  { name: "history", selector: { number: { min: 0, max: 10, mode: "box" } } },
];
const EDITOR_LABELS = {
  title: "Title",
  room: "This card's room",
  default_room: "Room selected at start",
  rooms: "Only show these rooms",
  layout: "Layout",
  live: "Live calls (play while you talk)",
  people: "Show people buttons",
  history: "Recent calls to show",
};
const EDITOR_HELP = {
  room: "Where this dashboard is (for example a wall tablet). Replies to its calls come back here.",
  rooms: "Leave empty for every room with a RoomCall speaker.",
  live: "Turn off to record the whole message first, then send it.",
};

class RoomCallCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { live: true, people: true, history: 3, layout: "default", ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
  }

  _render() {
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (field) => EDITOR_LABELS[field.name] || field.name;
      this._form.computeHelper = (field) => EDITOR_HELP[field.name];
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
    this._form.schema = EDITOR_SCHEMA;
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
