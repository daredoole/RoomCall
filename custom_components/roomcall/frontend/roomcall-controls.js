/* Communication controls share the integration's translated, validated command API. */
export class RoomCallControls {
  constructor(card, strings) {
    this.card = card;
    this.strings = strings;
    this.timers = new Set();
    this.root = document.createElement("section");
    this.root.className = "communication";
    const style = document.createElement("style");
    style.textContent = `
      .communication { display:grid; gap:10px; min-width:0 }
      .communication nav { display:flex; gap:6px; flex-wrap:wrap }
      .communication textarea,.communication select { font:inherit; color:var(--primary-text-color);
        background:var(--card-background-color); border:1px solid var(--divider-color);
        border-radius:8px; padding:10px; width:100%; box-sizing:border-box; min-width:0 }
      .communication textarea { min-height:80px; resize:vertical }
      .communication form { display:grid; gap:10px }
      .communication [hidden] { display:none!important }
      .communication details { font-size:13px; line-height:1.5 }
      .communication summary { cursor:pointer; padding:6px 0 }
      .communication .setup-content { display:grid; gap:8px; padding-top:8px }
      .communication .test-row { display:flex; align-items:center; flex-wrap:wrap; gap:6px }
      .communication .result { overflow-wrap:anywhere; white-space:pre-wrap }
      .communication button { min-height:36px; min-width:44px }
      .setup-wizard { display:grid; gap:10px; margin:12px 0; min-width:0 }
      .setup-wizard fieldset { min-inline-size:0; display:grid; gap:8px;
        border:1px solid var(--divider-color); border-radius:8px; padding:10px }
      .setup-wizard label { overflow-wrap:anywhere }
      .setup-wizard input[type=number] { width:100%; box-sizing:border-box; font:inherit;
        color:var(--primary-text-color); background:var(--card-background-color);
        border:1px solid var(--divider-color); border-radius:8px; padding:10px }
      .setup-wizard .wizard-status { overflow-wrap:anywhere; white-space:pre-wrap }
      .communication button:focus-visible,.communication summary:focus-visible {
        outline:2px solid var(--primary-color); outline-offset:3px }
      :host([layout="compact"]) .communication { display:none }
      @media(prefers-reduced-motion:reduce) { button.talk { transition:none } }
    `;
    this.root.append(style);
    this.nav = document.createElement("nav");
    this.nav.setAttribute("aria-label", "RoomCall");
    for (const mode of ["talk", "type", "ask"]) {
      const button = this.button(mode, () => this.mode(mode));
      button.setAttribute("aria-pressed", String(mode === "talk"));
      button.dataset.mode = mode;
      this.nav.append(button);
    }
    this.form = document.createElement("form");
    this.form.hidden = true;
    this.input = document.createElement("textarea");
    this.input.maxLength = 4000;
    this.input.required = true;
    this.input.setAttribute("aria-label", this.t("message"));
    this.satellites = this.select("satellite");
    const ackLabel = document.createElement("label");
    this.ack = document.createElement("input");
    this.ack.type = "checkbox";
    ackLabel.append(this.ack, document.createTextNode(` ${this.t("require_ack")}`));
    this.ackLabel = ackLabel;
    const actions = document.createElement("nav");
    actions.append(this.button("preview", () => this.preview()), this.button("send", () => {
      if (this.form.reportValidity()) this.send();
    }));
    this.form.append(this.input, this.satellites, ackLabel, actions);
    this.form.addEventListener("submit", (event) => { event.preventDefault(); this.send(); });
    this.result = document.createElement("div");
    this.result.className = "result muted";
    this.result.setAttribute("role", "status");
    this.jobActions = document.createElement("nav");
    this.details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = this.t("setup");
    this.setup = document.createElement("div");
    this.setup.className = "setup-content";
    this.details.append(summary, this.setup);
    this.wizard = new SetupWizard(this);
    this.setup.before(this.wizard.root);
    this.details.open = card._configured === false;
    this.details.addEventListener("toggle", () => {
      if (this.details.open) this.refresh().catch((e) => this.error(e));
    });
    this.root.append(this.nav, this.form, this.result, this.jobActions);
    card.shadowRoot.querySelector(".wrap").before(this.root);
    this.settingsRoot = document.createElement("section");
    this.settingsRoot.className = "communication";
    this.settingsRoot.append(this.details);
    card.shadowRoot.querySelector(".history").before(this.settingsRoot);
    this.mode("talk");
    this.refresh().catch((error) => this.error(error));
  }

  t(key, values = {}) {
    return Object.entries(values).reduce((value, [name, replacement]) =>
      value.replaceAll(`{${name}}`, String(replacement)), this.strings[key] || key);
  }
  button(key, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip small";
    button.textContent = this.t(key);
    button.addEventListener("click", () => Promise.resolve().then(action).catch((e) => this.error(e)));
    return button;
  }
  select(key) {
    const select = document.createElement("select");
    select.setAttribute("aria-label", this.t(key));
    return select;
  }
  async command(operation, request = {}) {
    const result = await this.card._hass.callApi("POST", "roomcall/command", { operation, request });
    if (!result.ok) throw Object.assign(new Error(result.error?.message || this.t("provider_failed")),
      { problem: result.error });
    return result;
  }
  error(error) { this.result.textContent = error.message || String(error); }
  targets() {
    return this.card._person ? { people: [this.card._person] } : { areas: [this.card._target] };
  }
  mode(mode) {
    if (mode !== "talk") this.card._stop({ preventDefault() {} });
    this.currentMode = mode;
    this.form.hidden = mode === "talk";
    this.satellites.hidden = mode !== "ask";
    this.ackLabel.hidden = mode === "ask";
    this.card.shadowRoot.querySelector(".wrap").hidden = mode !== "talk";
    this.card.shadowRoot.querySelector(".status").hidden = mode !== "talk";
    for (const button of this.nav.children) button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
  }
  async preview() {
    if (this.currentMode === "ask") {
      if (!this.satellites.value) throw new Error(this.t("satellite_unavailable"));
      this.result.textContent = this.satellites.selectedOptions[0].textContent;
      return;
    }
    const result = await this.command("preview", this.targets());
    const names = result.endpoints.map((id) => this.capabilities.endpoints.find((e) => e.entity_id === id)?.name || id);
    this.result.textContent = `${names.join(", ")}\n${this.t("request_hint")}`;
  }
  async send() {
    if (!this.input.value.trim()) return;
    const request = { message: this.input.value.trim(), idempotency_key: crypto.randomUUID() };
    if (this.currentMode === "ask") {
      if (!this.satellites.value) throw new Error(this.t("satellite_unavailable"));
      request.satellites = [this.satellites.value];
    } else Object.assign(request, this.targets(), { require_ack: this.ack.checked,
      priority: this.card._urgent ? "urgent" : "normal" });
    const job = await this.command(this.currentMode === "ask" ? "ask" : "send", request);
    this.watch(job.id);
  }
  async watch(id, onDone) {
    if (this.closed) return;
    try {
      const job = await this.command("status", { id });
      this.result.textContent = this.t(job.state, { accepted: job.result?.delivered?.length || 0,
        speakers: Object.keys(job.result?.failed || {}).join(", ") });
      this.jobActions.replaceChildren();
      if (job.state === "queued") this.jobActions.append(this.button("cancel", async () => {
        await this.command("cancel", { id });
      }));
      if (job.responses?.length) this.result.textContent += `\n${job.responses.map((r) => r.sentence || r.text || "").join("\n")}`;
      if (job.error) this.result.textContent += `\n${job.error.message}`;
      if (["queued", "running", "waiting_ack"].includes(job.state)) {
        const timer = setTimeout(() => { this.timers.delete(timer); this.watch(id, onDone); }, 700);
        this.timers.add(timer);
      } else { onDone?.(job); this.card._loadHistory(); }
    } catch (error) { this.error(error); }
  }
  async refresh() {
    if (this.card._configured === false) return;
    this.capabilities = await this.command("discover");
    if (this.closed) return;
    this.satellites.replaceChildren(...this.capabilities.satellites.filter((s) => s.can_ask && s.available).map((s) => new Option(s.name, s.entity_id)));
    this.setup.replaceChildren();
    this.setup.append(this.button("refresh", () => this.refresh()));
    for (const endpoint of this.capabilities.endpoints.filter((e) => e.selected)) {
      const row = document.createElement("div");
      row.className = "test-row";
      const name = document.createElement("span");
      name.textContent = endpoint.name;
      row.append(name, this.button("test", async () => {
        const job = await this.command("sound_test", { speaker: endpoint.entity_id });
        this.watch(job.id, () => {
          for (const heard of [true, false]) row.append(this.button(heard ? "heard" : "silent", async () => {
            await this.command("confirm_test", { id: job.id, heard });
            await this.refresh();
          }));
        });
      }));
      if (endpoint.sound_test) row.append(document.createTextNode(this.t(endpoint.sound_test.heard ? "heard" : "silent")));
      this.setup.append(row);
    }
    const room = this.select("station");
    room.append(...this.capabilities.areas.map((a) => new Option(a.name, a.id)));
    if (this.card._config.room) room.value = this.card._config.room;
    const peers = this.select("call_station");
    peers.append(...this.capabilities.stations.filter((s) => s.id !== this.station?.id).map((s) => new Option(s.name, s.id)));
    this.setup.append(room, this.button("connect_station", async () => {
      if (this.station) await this.station.close();
      this.station = new StationClient(this);
      await this.station.register(room.value);
      await this.refresh();
    }), peers, this.button("call_station", async () => {
      if (!this.station) throw new Error(this.t("station_unavailable"));
      await this.station.call(peers.value);
    }));
    if (this.capabilities.features.voicemail) {
      const mail = await this.command("voicemail");
      const label = document.createElement("div");
      label.textContent = this.t("voicemail");
      this.setup.append(label);
      for (const item of mail.messages) {
        const row = document.createElement("div");
        row.className = "test-row";
        const message = document.createElement("span");
        message.textContent = item.message;
        row.append(message, this.button("replay", async () => {
          const job = await this.command("voicemail", { action: "replay", id: item.id });
          this.watch(job.id);
        }), this.button("delete", async () => {
          await this.command("voicemail", { action: "delete", id: item.id });
          await this.refresh();
        }));
        this.setup.append(row);
      }
    }
  }
  close() {
    this.closed = true;
    for (const timer of this.timers) clearTimeout(timer);
    this.timers.clear();
    this.station?.close();
    this.wizard.closed = true;
    this.settingsRoot.remove();
    this.root.remove();
  }
}

class SetupWizard {
  constructor(controls) {
    this.controls = controls;
    this.t = (key, values) => controls.t(key, values);
    this.root = document.createElement("section");
    this.root.className = "setup-wizard";
    this.root.setAttribute("aria-label", this.t("guided_setup"));
    const title = document.createElement("strong");
    title.textContent = this.t("guided_setup");
    this.status = document.createElement("div");
    this.status.className = "wizard-status muted";
    this.status.setAttribute("role", "status");
    this.form = document.createElement("form");
    this.form.addEventListener("submit", (event) => {
      event.preventDefault();
      this.run(() => this.plan());
    });
    this.speakers = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = this.t("setup_speakers");
    this.speakers.append(legend);
    this.engine = controls.select("setup_tts");
    this.volume = document.createElement("input");
    Object.assign(this.volume, {
      type: "number",
      min: "0",
      max: "1",
      step: "0.05",
    });
    this.volume.setAttribute("aria-label", this.t("setup_volume"));
    this.mediaOnly = document.createElement("input");
    this.mediaOnly.type = "checkbox";
    const mediaLabel = document.createElement("label");
    mediaLabel.append(
      this.mediaOnly,
      document.createTextNode(` ${this.t("media_only")}`),
    );
    const engineLabel = document.createElement("label");
    engineLabel.textContent = this.t("setup_tts");
    engineLabel.append(this.engine);
    const volumeLabel = document.createElement("label");
    volumeLabel.textContent = this.t("setup_volume");
    volumeLabel.append(this.volume);
    this.form.append(
      this.speakers,
      engineLabel,
      volumeLabel,
      mediaLabel,
      this.button("create_plan", () => this.plan()),
    );
    this.form.addEventListener("input", () => {
      this.active = null;
      this.review.replaceChildren();
      this.renderActions();
    });
    this.resume = controls.select("resume_plan");
    this.resume.addEventListener("change", () =>
      this.show(this.plans.find((p) => p.id === this.resume.value)),
    );
    this.review = document.createElement("div");
    this.review.className = "setup-review";
    this.actions = document.createElement("nav");
    this.tests = document.createElement("div");
    this.root.append(
      title,
      this.button("scan_devices", () => this.scan()),
      this.button("diagnose_setup", () => this.diagnose()),
      this.status,
      this.form,
      this.resume,
      this.review,
      this.actions,
      this.tests,
    );
    this.run(() => this.scan());
  }
  button(key, action) {
    return this.controls.button(key, () => this.run(action));
  }
  async request(action, values = {}) {
    return this.controls.command("setup", { action, ...values });
  }
  async run(action) {
    if (this.busy || this.closed) return;
    this.busy = true;
    this.root.setAttribute("aria-busy", "true");
    try {
      await action();
    } catch (error) {
      this.status.textContent = error.message;
    } finally {
      this.busy = false;
      this.root.removeAttribute("aria-busy");
    }
  }
  async scan() {
    const { discovery } = await this.request("discover");
    if (this.closed) return;
    this.discovery = discovery;
    this.speakers.querySelectorAll("label").forEach((label) => label.remove());
    for (const item of discovery.endpoints.filter(
      (e) => e.eligible || e.selected,
    )) {
      const label = document.createElement("label"),
        input = document.createElement("input");
      input.type = "checkbox";
      input.value = item.entity_id;
      input.checked = discovery.recommended.speakers.includes(item.entity_id);
      label.append(input, document.createTextNode(` ${item.name}`));
      this.speakers.append(label);
    }
    this.engine.replaceChildren(
      new Option(this.t("choose_tts"), ""),
      ...discovery.tts_engines.map((e) => new Option(e.name, e.entity_id)),
    );
    this.engine.value =
      discovery.settings.tts_engine || discovery.recommended.tts_engine || "";
    this.volume.value = discovery.settings.volume ?? "";
    this.status.textContent = this.t("setup_scan_count", {
      count: discovery.endpoints.length,
    });
    const { plans } = await this.request("list");
    if (this.closed) return;
    this.plans = plans.sort((a, b) => b.created - a.created);
    this.resume.replaceChildren(
      ...this.plans.map(
        (p) =>
          new Option(
            `${this.t(`setup_${p.state}`)} · ${new Date(p.created * 1000).toLocaleString()}`,
            p.id,
          ),
      ),
    );
    this.resume.hidden = !this.plans.length;
    this.resume.value = this.active?.id || this.plans[0]?.id || "";
    if (this.resume.value)
      this.show(this.plans.find((p) => p.id === this.resume.value));
  }
  async plan() {
    if (!this.form.reportValidity()) return;
    const settings = {
      speakers: [...this.speakers.querySelectorAll("input:checked")].map(
        (i) => i.value,
      ),
      tts_engine: this.engine.value || null,
    };
    if (this.volume.value !== "") settings.volume = Number(this.volume.value);
    const result = await this.request("plan", {
      settings,
      media_only: this.mediaOnly.checked,
    });
    this.show(result.plan);
  }
  show(plan) {
    this.active = plan;
    this.review.replaceChildren();
    this.tests.replaceChildren();
    if (!plan) return;
    for (const input of this.speakers.querySelectorAll("input"))
      input.checked = plan.settings.speakers.includes(input.value);
    this.engine.value = plan.settings.tts_engine || "";
    this.volume.value = plan.settings.volume ?? "";
    this.mediaOnly.checked = plan.media_only;
    const title = document.createElement("strong");
    title.textContent = this.t("setup_review");
    const list = document.createElement("ul");
    const names = plan.settings.speakers.map(
      (id) =>
        this.discovery.endpoints.find((e) => e.entity_id === id)?.name || id,
    );
    for (const value of [
      this.t("plan_state", { state: this.t(`setup_${plan.state}`) }),
      `${this.t("setup_speakers")}: ${names.join(", ")}`,
      `${this.t("setup_tts")}: ${this.discovery.tts_engines.find((e) => e.entity_id === plan.settings.tts_engine)?.name || this.t("media_only")}`,
      `${this.t("setup_volume")}: ${plan.settings.volume ?? this.t("setup_keep_volume")}`,
      ...plan.questions.map((q) => q.message),
    ]) {
      const item = document.createElement("li");
      item.textContent = value;
      list.append(item);
    }
    this.review.append(title, list);
    this.renderActions();
    if (plan.state === "applied")
      for (const entity of plan.settings.speakers) {
        const row = document.createElement("div");
        row.className = "test-row";
        const name = document.createElement("span");
        name.textContent =
          this.discovery.endpoints.find((e) => e.entity_id === entity)?.name ||
          entity;
        row.append(
          name,
          this.button("test", async () => {
            const job = await this.request("test", {
              id: plan.id,
              speaker: entity,
            });
            this.status.textContent = this.t("test_pending");
            this.controls.watch(job.id, () => {
              if (this.closed) return;
              for (const heard of [true, false])
                row.append(
                  this.button(heard ? "heard" : "silent", async () => {
                    await this.request("confirm", {
                      id: plan.id,
                      speaker: entity,
                      heard,
                    });
                    await this.verify();
                  }),
                );
            });
          }),
        );
        this.tests.append(row);
      }
  }
  renderActions() {
    this.actions.replaceChildren();
    const plan = this.active;
    if (!plan) return;
    if (["draft", "applying"].includes(plan.state))
      this.actions.append(
        this.button("apply_plan", async () => {
          const result = await this.request("apply", {
            id: plan.id,
            revision: plan.revision,
          });
          this.show(result.plan);
          this.status.textContent = this.t("setup_started");
          await this.controls.card._load();
        }),
      );
    if (["applied", "undoing"].includes(plan.state)) {
      this.actions.append(this.button("verify_setup", () => this.verify()));
      const confirmLabel = document.createElement("label"),
        confirm = document.createElement("input");
      confirm.type = "checkbox";
      confirmLabel.append(
        confirm,
        document.createTextNode(` ${this.t("confirm_undo")}`),
      );
      this.actions.append(
        confirmLabel,
        this.button("undo_setup", async () => {
          const result = await this.request("undo", {
            id: plan.id,
            revision: plan.revision,
            confirm: confirm.checked,
          });
          this.show(result.plan);
          this.status.textContent = this.t("setup_rolled_back");
          await this.controls.card._load();
        }),
      );
    }
  }
  async verify() {
    const result = await this.request("verify", { id: this.active.id });
    this.status.textContent = this.t(
      result.verification.completed
        ? "setup_complete"
        : result.verification.configuration_ready
          ? "setup_needs_tests"
          : "setup_not_ready",
    );
    await this.controls.card._load();
    await this.controls.refresh();
  }
  async diagnose() {
    const result = await this.controls.command("doctor");
    this.status.textContent =
      result.findings
        .map((f) => `${f.entity_id || ""} ${f.message}`)
        .join("\n") || this.t("doctor_clear");
    this.tests.replaceChildren();
    for (const route of result.alternatives) {
      const button = this.button("plan_route", async () => {
        const proposal = await this.controls.command("doctor", {
          action: "plan_route",
          speaker: route.from,
          alternative: route.to,
        });
        this.show(proposal.plan);
      });
      const name = (id) =>
        this.discovery.endpoints.find((e) => e.entity_id === id)?.name || id;
      button.textContent += `: ${name(route.from)} → ${name(route.to)}`;
      this.tests.append(button);
    }
  }
}

class StationClient {
  constructor(controls) {
    this.controls = controls;
    this.id = crypto.randomUUID();
    this.pendingCandidates = [];
    this.panel = document.createElement("div");
    controls.root.append(this.panel);
  }
  async register(room) {
    const name = this.controls.capabilities.areas.find((a) => a.id === room)?.name || room;
    this.unsubscribe = await this.controls.card._hass.connection.subscribeMessage((event) => {
      this.event(event).catch((error) => { this.controls.error(error); this.hangup(); });
    }, { type: "roomcall/station", station_id: this.id, room, name });
  }
  command(action, extra = {}) {
    return this.controls.card._hass.callWS({ type: "roomcall/session", station_id: this.id,
      session_id: this.session, action, ...extra });
  }
  async media() {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: {
      echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    this.peer = new RTCPeerConnection({ iceServers: [] });
    this.stream.getTracks().forEach((track) => this.peer.addTrack(track, this.stream));
    this.peer.onicecandidate = (event) => {
      if (event.candidate) this.command("signal", { signal: { candidate: event.candidate.toJSON() } }).catch((e) => this.controls.error(e));
    };
    this.peer.ontrack = (event) => {
      const audio = document.createElement("audio");
      audio.autoplay = true;
      audio.controls = true;
      audio.srcObject = event.streams[0];
      this.panel.append(audio);
      audio.play().catch(() => { /* Native controls allow explicit playback if autoplay is blocked. */ });
    };
    this.peer.onconnectionstatechange = () => {
      if (this.peer?.connectionState === "connected") this.controls.result.textContent = this.controls.t("connected");
      if (["failed", "disconnected"].includes(this.peer?.connectionState)) this.hangup();
    };
  }
  async call(peer) {
    try {
      await this.media();
      const result = await this.command("call", { peer });
      this.session = result.id;
    } catch (error) { await this.hangup(); throw error; }
  }
  async event(event) {
    if (event.type === "incoming") {
      this.session = event.id;
      this.controls.result.textContent = this.controls.t("incoming", { name: event.from });
      this.panel.replaceChildren(this.controls.button("answer", async () => {
        await this.media();
        await this.command("answer");
        this.panel.replaceChildren(this.controls.button("hangup", () => this.hangup()));
      }), this.controls.button("cancel", () => this.hangup()));
    } else if (event.type === "calling") {
      this.session = event.id;
      this.controls.result.textContent = this.controls.t("calling");
      this.panel.replaceChildren(this.controls.button("hangup", () => this.hangup()));
    } else if (event.type === "accepted") {
      await this.peer.setLocalDescription(await this.peer.createOffer());
      await this.command("signal", { signal: { description: this.peer.localDescription.toJSON() } });
    } else if (event.type === "signal" && event.id === this.session && this.peer) {
      const { description, candidate } = event.signal;
      if (description) {
        await this.peer.setRemoteDescription(description);
        for (const queued of this.pendingCandidates.splice(0)) await this.peer.addIceCandidate(queued);
        if (description.type === "offer") {
          await this.peer.setLocalDescription(await this.peer.createAnswer());
          await this.command("signal", { signal: { description: this.peer.localDescription.toJSON() } });
        }
      } else if (candidate) {
        if (this.peer.remoteDescription) await this.peer.addIceCandidate(candidate);
        else this.pendingCandidates.push(candidate);
      }
    } else if (event.type === "ended" && event.id === this.session) {
      this.cleanup();
      this.controls.result.textContent = this.controls.t("ended");
    }
  }
  cleanup() {
    this.session = null;
    this.peer?.close();
    this.peer = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.pendingCandidates = [];
    this.panel.replaceChildren();
  }
  async hangup() {
    const session = this.session;
    this.cleanup();
    if (session) this.controls.result.textContent = this.controls.t("ended");
    try { if (session) await this.command("hangup", { session_id: session }); }
    catch (error) { this.controls.error(error); }
  }
  async close() {
    const closing = this.hangup();
    this.unsubscribe?.();
    this.panel.remove();
    await closing;
  }
}
