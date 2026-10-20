/* RoomCall push-to-talk card for Home Assistant dashboards. */
const CARD_VERSION = "0.1.1";
// Peak level (0–1) below which a recording is treated as silence.
const SILENCE_PEAK = 0.04;

class RoomCallCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Intercom" };
  }

  static getConfigElement() {
    return undefined;
  }

  setConfig(config) {
    this._config = { title: "Intercom", ...config };
    this._target = this._config.default_room || "all";
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._loadRooms();
  }

  getCardSize() {
    return 6;
  }

  getGridOptions() {
    return { columns: 12, rows: 6, min_rows: 5 };
  }

  async _loadRooms() {
    try {
      const res = await this._hass.callApi("GET", "roomcall/rooms");
      const only = this._config.rooms;
      this._rooms = only ? res.rooms.filter((r) => only.includes(r.area_id) || only.includes(r.name)) : res.rooms;
      this._count = res.speakers;
    } catch (err) {
      this._rooms = [];
      this._setStatus(`RoomCall unavailable: ${err.message || err}`, "err");
    }
    this._renderRooms();
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 16px; display: flex; flex-direction: column; gap: 12px; height: 100%; box-sizing: border-box; }
        .head { display: flex; justify-content: space-between; align-items: baseline; }
        .title { font-size: 18px; font-weight: 500; }
        .muted { color: var(--secondary-text-color); font-size: 13px; }
        .rooms { display: flex; flex-wrap: wrap; gap: 8px; }
        .room {
          border: 1px solid var(--divider-color); background: var(--card-background-color);
          color: var(--primary-text-color); border-radius: 999px; padding: 8px 14px;
          font: inherit; font-size: 14px; cursor: pointer; display: inline-flex; gap: 6px; align-items: center;
        }
        .room[aria-pressed="true"] { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
        .room ha-icon { --mdc-icon-size: 18px; }
        .wrap { flex: 1; display: grid; place-items: center; min-height: 160px; }
        button.talk {
          width: min(52vw, 170px); aspect-ratio: 1; border-radius: 50%; border: 0; cursor: pointer;
          background: var(--primary-color); color: var(--text-primary-color, #fff);
          font: inherit; font-size: 17px; font-weight: 600; touch-action: none; user-select: none;
          -webkit-user-select: none; display: grid; place-items: center; align-content: center; gap: 6px; transition: transform 0.08s;
          box-shadow: 0 10px 30px color-mix(in srgb, var(--primary-color) 35%, transparent);
        }
        button.talk ha-icon { --mdc-icon-size: 40px; }
        button.talk.live {
          background: var(--error-color, #db4437); transform: scale(0.97);
          box-shadow: 0 0 0 calc(var(--level, 0) * 30px) color-mix(in srgb, var(--error-color, #db4437) 25%, transparent);
        }
        button.talk:disabled { opacity: 0.55; cursor: progress; }
        .status { text-align: center; min-height: 1.3em; font-size: 14px; }
        .ok { color: var(--success-color, #43a047); } .err { color: var(--error-color, #db4437); }
      </style>
      <ha-card>
        <div class="head">
          <span class="title"></span>
          <span class="muted count"></span>
        </div>
        <div class="rooms" role="group" aria-label="Broadcast to"></div>
        <div class="wrap">
          <button class="talk" type="button" aria-label="Hold to talk">
            <ha-icon icon="mdi:microphone"></ha-icon><span class="label">Hold to talk</span>
          </button>
        </div>
        <div class="status muted">Pick a room, hold the button and speak.</div>
      </ha-card>`;
    this.shadowRoot.querySelector(".title").textContent = this._config.title;
    const talk = this.shadowRoot.querySelector(".talk");
    talk.addEventListener("pointerdown", (e) => this._start(e));
    for (const ev of ["pointerup", "pointerleave", "pointercancel"]) talk.addEventListener(ev, (e) => this._stop(e));
    talk.addEventListener("contextmenu", (e) => e.preventDefault());
    this._renderRooms();
  }

  _renderRooms() {
    const box = this.shadowRoot && this.shadowRoot.querySelector(".rooms");
    if (!box || !this._rooms) return;
    const total = this._count || 0;
    this.shadowRoot.querySelector(".count").textContent = `${total} speaker${total === 1 ? "" : "s"}`;
    box.replaceChildren();
    const entries = [{ area_id: "all", name: "Everywhere", icon: "mdi:home-sound-out" }, ...this._rooms];
    for (const room of entries) {
      const b = document.createElement("button");
      b.className = "room";
      b.type = "button";
      if (room.icon) {
        const icon = document.createElement("ha-icon");
        icon.setAttribute("icon", room.icon);
        b.append(icon);
      }
      b.append(document.createTextNode(room.name));
      b.title = room.speakers ? room.speakers.join(", ") : "All RoomCall speakers";
      b.setAttribute("aria-pressed", String(room.area_id === this._target));
      b.addEventListener("click", () => {
        this._target = room.area_id;
        box.querySelectorAll(".room").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      });
      box.append(b);
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
      this._setStatus("Microphone blocked — allow mic access for Home Assistant.", "err");
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
    const name = this._target === "all" ? "everywhere" : (this._rooms.find((r) => r.area_id === this._target) || {}).name;
    this._setStatus(`Talking to ${name}…`);
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
    try {
      const res = await this._hass.fetchWithAuth(
        `/api/roomcall/talk?target=${encodeURIComponent(this._target)}`,
        { method: "POST", headers: { "Content-Type": type }, body: blob },
      );
      const body = await res.json().catch(() => ({}));
      const results = body.results || [];
      const failed = results.filter((r) => !r.ok);
      const secs = body.seconds ? ` ${body.seconds} s` : "";
      if (res.ok) this._setStatus(`Delivered${secs} to ${results.map((r) => r.speaker).join(", ")}.`, "ok");
      else if (failed.length) this._setStatus(`Not delivered to: ${failed.map((r) => r.speaker).join(", ")}`, "err");
      else this._setStatus(body.message || `Failed (${res.status})`, "err");
    } catch (err) {
      this._setStatus(`Send failed: ${err.message || err}`, "err");
    } finally {
      talk.disabled = false;
    }
  }
}

if (!customElements.get("roomcall-card")) customElements.define("roomcall-card", RoomCallCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "roomcall-card",
  name: "RoomCall",
  description: "Hold-to-talk intercom: broadcast your voice to a room or the whole house.",
  preview: false,
});
console.info(`%c ROOMCALL-CARD %c ${CARD_VERSION} `, "background:#4f8cff;color:#fff", "");
