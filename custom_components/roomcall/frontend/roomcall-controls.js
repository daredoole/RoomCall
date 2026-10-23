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
      .communication textarea,.communication select,.shortcut-builder input[type=password] { font:inherit; color:var(--primary-text-color);
        background:var(--card-background-color); border:1px solid var(--divider-color);
        border-radius:8px; padding:10px; width:100%; box-sizing:border-box; min-width:0 }
      .communication textarea { min-height:80px; resize:vertical }
      .communication form { display:grid; gap:10px }
      .communication [hidden] { display:none!important }
      .communication details { font-size:13px; line-height:1.5 }
      .communication summary { cursor:pointer; padding:6px 0 }
      .communication .setup-content { display:grid; gap:8px; padding-top:8px }
      .communication .test-row { display:flex; align-items:center; flex-wrap:wrap; gap:6px }
      .communication .result,.communication .settings-result { overflow-wrap:anywhere; white-space:pre-wrap }
      .communication ol { min-width:0; margin:0; padding-inline-start:24px; overflow-wrap:anywhere; }
      .communication button { min-height:44px; min-width:44px }
      .communication button:disabled { opacity:.5; cursor:not-allowed }
      .communication nav[data-toolbar] { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)) 44px; gap:4px;
        background:var(--secondary-background-color,var(--card-background-color)); padding:4px; border-radius:14px; }
      .communication nav[data-toolbar] button { justify-content:center; padding:6px; border-color:transparent; border-radius:10px; }
      .communication nav[data-toolbar] button[aria-pressed=true] { background:var(--primary-color); color:var(--text-primary-color,#fff); }
      .communication .primary-action,.communication [role=tab][aria-selected=true] { background:var(--primary-color); color:var(--text-primary-color,#fff); border-color:var(--primary-color); }
      .communication dialog { color:var(--primary-text-color); background:var(--card-background-color);
        border:1px solid var(--divider-color); border-radius:20px; padding:0;
        width:min(840px,calc(100vw - 24px)); max-width:none; max-height:calc(100dvh - 32px);
        box-sizing:border-box; box-shadow:var(--ha-card-box-shadow,0 16px 60px #0005); }
      .communication dialog::backdrop { background:rgb(0 0 0 / .48); }
      .communication dialog[open] { display:grid; grid-template-rows:auto minmax(0,1fr); }
      .settings-heading { display:flex; align-items:center; justify-content:space-between; gap:12px;
        padding:16px 20px; border-bottom:1px solid var(--divider-color); }
      .settings-heading h2 { margin:0; font-size:1.125rem; min-width:0; overflow-wrap:anywhere; }
      .settings-body { padding:16px 20px max(20px,env(safe-area-inset-bottom)); overflow:auto;
        min-height:0; min-width:0; }
      .settings-body nav { margin-bottom:16px; }
      .settings-body [role=tabpanel] { min-width:0; }
      .communication.settings-surface { display:contents; }
      .setup-review { overflow-wrap:anywhere; }
      .shortcut-builder,.shortcut-instructions { display:grid; gap:12px; min-width:0; }
      .panel-section { display:grid; gap:10px; min-width:0; margin-bottom:12px; padding:14px;
        border:1px solid var(--divider-color); border-radius:14px; }
      .panel-section h3 { margin:0; font-size:0.9375rem; font-weight:600; }
      .panel-field { display:grid; gap:6px; min-width:0; color:var(--secondary-text-color); font-size:0.8125rem; }
      .panel-note { margin:0; color:var(--secondary-text-color); font-size:0.875rem; overflow-wrap:anywhere; }
      .message-row { display:grid; gap:8px; padding-top:10px; border-top:1px solid var(--divider-color); }
      .message-row p { margin:0; overflow-wrap:anywhere; }
      .shortcut-builder label { display:grid; gap:6px; min-width:0; }
      .shortcut-builder .remote-consent { display:flex; align-items:flex-start; gap:8px; }
      .shortcut-builder p { margin:0; overflow-wrap:anywhere; }
      .shortcut-builder code { white-space:pre-wrap; overflow-wrap:anywhere; }

      .setup-review ul { padding-inline-start:20px; }
      .setup-wizard input[type=checkbox] { width:20px; height:20px; flex-shrink:0; }
      .setup-wizard label { min-height:44px; display:flex; align-items:center; gap:8px; }
      .communication form label { min-height:44px; }
      .communication input[type=checkbox] { width:20px; height:20px; }
      .setup-wizard label:has(select),.setup-wizard label:has(input[type=number]) { display:grid; }
      @media (max-width:400px) { .settings-heading,.settings-body { padding-inline:14px; } }
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
      @media(prefers-reduced-motion:reduce) { button.talk { transition:none } }
    `;
    this.root.append(style);
    this.nav = document.createElement("nav");
    this.nav.setAttribute("aria-label", "RoomCall");
    this.nav.dataset.toolbar = "";
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
    this.timeline = document.createElement("ol");
    this.timeline.setAttribute("aria-label", this.t("delivery_timeline"));
    this.timeline.hidden = true;
    this.jobActions = document.createElement("nav");
    this.dialog = document.createElement("dialog");
    this.dialog.setAttribute("aria-label", this.t("settings"));
    const heading = document.createElement("header");
    heading.className = "settings-heading";
    const title = document.createElement("h2");
    title.textContent = this.t("settings");
    const close = this.button("close", () => this.dialog.close());
    close.setAttribute("aria-label", this.t("close")); close.textContent = "";
    const closeIcon = document.createElement("ha-icon"); closeIcon.setAttribute("icon", "mdi:close");
    close.append(closeIcon);
    heading.append(title, close);
    const body = document.createElement("div");
    body.className = "settings-body";
    const tabs = document.createElement("nav");
    tabs.setAttribute("aria-label", this.t("settings"));
    tabs.setAttribute("role", "tablist");
    const prefix = `roomcall-${crypto.randomUUID()}`;
    this.panels = {};
    for (const key of ["setup", "shortcuts", "stations", "voicemail"]) {
      const panel = document.createElement("section");
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-label", this.t(key));
      panel.id = `${prefix}-${key}`;
      this.panels[key] = panel;
      const button = this.button(key, () => this.settingsTab(key));
      button.dataset.panel = key;
      button.id = `${prefix}-${key}-tab`;
      button.setAttribute("role", "tab");
      button.setAttribute("aria-controls", panel.id);
      panel.setAttribute("aria-labelledby", button.id);
      tabs.append(button);
    }
    this.settingsTabs = tabs;
    tabs.addEventListener("keydown", event => {
      const items = [...tabs.children].filter(button => !button.hidden);
      const index = items.indexOf(event.target);
      if (index < 0 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + items.length) % items.length;
      this.settingsTab(items[next].dataset.panel); items[next].focus();
    });
    this.settingsStatus = document.createElement("div");
    this.settingsStatus.setAttribute("role", "status");
    this.settingsStatus.className = "settings-result";
    body.append(tabs, this.settingsStatus, ...Object.values(this.panels));
    this.dialog.append(heading, body);
    this.setup = document.createElement("div");
    this.setup.className = "setup-content";
    this.quickTests = document.createElement("div");
    this.quickTests.className = "quick-tests";
    this.panels.setup.append(this.quickTests);
    this.panels.stations.append(this.setup);
    this.shortcutBuilder = new ShortcutBuilder(this);
    this.panels.shortcuts.append(this.shortcutBuilder.root);
    this.wizard = new SetupWizard(this);
    this.quickTests.before(this.wizard.root);
    const settings = this.button("settings", () => this.openSettings());
    settings.setAttribute("aria-haspopup", "dialog");
    settings.setAttribute("aria-label", this.t("settings"));
    settings.textContent = "";
    const icon = document.createElement("ha-icon"); icon.setAttribute("icon", "mdi:tune-variant");
    settings.append(icon);
    this.nav.append(settings);
    this.root.append(this.nav, this.form, this.result, this.timeline, this.jobActions);
    card.shadowRoot.querySelector(".wrap").before(this.root);
    this.settingsRoot = document.createElement("section");
    this.settingsRoot.className = "communication settings-surface";
    this.settingsRoot.append(this.dialog);
    card.shadowRoot.querySelector(".history").before(this.settingsRoot);
    this.mode("talk");
    this.refresh().catch((error) => this.error(error));
    this.settingsTab("setup");
    if (card._configured === false) this.openSettings();
  }

  settingsTab(key) {
    for (const [name, panel] of Object.entries(this.panels)) panel.hidden = name !== key;
    for (const button of this.settingsTabs.children) {
      const selected = button.dataset.panel === key;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    }
  }
  openSettings() {
    this.card._stop({ preventDefault() {} });
    this.settingsStatus.textContent = "";
    if (!this.dialog.open) this.dialog.showModal();
    this.refresh().catch(error => this.error(error));
  }

  t(key, values = {}) {
    return Object.entries(values).reduce((value, [name, replacement]) =>
      value.replaceAll(`{${name}}`, String(replacement)), this.strings[key] || key);
  }
  button(key, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip small";
    if (["send", "apply_plan", "create_plan", "connect_station"].includes(key)) button.classList.add("primary-action");
    button.textContent = this.t(key);
    button.addEventListener("click", () => Promise.resolve().then(action).catch((e) => this.error(e)));
    return button;
  }
  select(key) {
    const select = document.createElement("select");
    select.setAttribute("aria-label", this.t(key));
    return select;
  }
  section(key, ...children) {
    const section = document.createElement("section");
    section.className = "panel-section";
    const title = document.createElement("h3");
    title.textContent = this.t(key);
    section.append(title, ...children);
    return section;
  }
  field(key, control) {
    const label = document.createElement("label");
    label.className = "panel-field";
    label.append(this.t(key), control);
    return label;
  }
  note(text) {
    const note = document.createElement("p");
    note.className = "panel-note";
    note.textContent = text;
    return note;
  }
  async command(operation, request = {}) {
    const result = await this.card._hass.callApi("POST", "roomcall/command", { operation, request });
    if (!result.ok) throw Object.assign(new Error(result.error?.message || this.t("provider_failed")),
      { problem: result.error });
    return result;
  }
  error(error) {
    const message = error.message || String(error);
    this.result.textContent = message;
    if (this.dialog.open) this.settingsStatus.textContent = message;
  }
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
    for (const button of this.nav.querySelectorAll("[data-mode]")) button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
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
      const steps = [...(job.progress || [])];
      this.timeline.hidden = !steps.length;
      this.timeline.replaceChildren(...steps.map(step => {
        const row = document.createElement("li");
        row.textContent = `${this.t(`phase_${step.stage}`)} · ${(step.elapsed_ms / 1000).toFixed(1)}s`;
        return row;
      }));
      for (const [endpoint, evidence] of Object.entries(job.result?.evidence || {})) {
        if (endpoint === "human") continue;
        const name = this.capabilities?.endpoints.find(e => e.entity_id === endpoint)?.name || endpoint;
        for (const key of ["audio_fetched_ms", "playback_reported_ms"]) {
          if (evidence[key] == null) continue;
          const row = document.createElement("li");
          row.textContent = `${name}: ${this.t(`phase_${key}`)} · ${(evidence[key] / 1000).toFixed(1)}s`;
          this.timeline.append(row);
        }
      }
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
    this.shortcutBuilder.rooms(this.capabilities.rooms || this.capabilities.areas.map(a => ({area_id:a.id,name:a.name})));
    this.setup.replaceChildren();
    this.quickTests.replaceChildren();
    this.quickTests.hidden = this.wizard.active?.state === "applied";
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
      this.quickTests.append(row);
    }
    const room = this.select("station_room");
    room.append(...this.capabilities.areas.map((a) => new Option(a.name, a.id)));
    if (this.card._config.room) room.value = this.card._config.room;
    const peers = this.select("call_station");
    const others = this.capabilities.stations.filter((s) => s.id !== this.station?.id);
    peers.append(...others.map((s) => new Option(s.name, s.id)));
    const call = this.button("call_station", async () => {
      if (!this.station) throw new Error(this.t("station_unavailable"));
      await this.station.call(peers.value);
      this.dialog.close();
    });
    call.disabled = !others.length;
    const empty = this.note(this.t("no_stations"));
    empty.hidden = Boolean(others.length);
    peers.hidden = !others.length;
    this.setup.append(
      this.section("station", this.field("station_room", room), this.button("connect_station", async () => {
        if (this.station) await this.station.close();
        this.station = new StationClient(this);
        await this.station.register(room.value);
        await this.refresh();
      })),
      this.section("call_station", empty, peers, call),
      this.button("refresh", () => this.refresh()));
    this.settingsTabs.querySelector('[data-panel="voicemail"]').hidden = !this.capabilities.features.voicemail;
    if (this.capabilities.features.voicemail) {
      const mail = await this.command("voicemail");
      const list = this.section("voicemail");
      if (!mail.messages.length) list.append(this.note(this.t("empty")));
      for (const item of mail.messages) {
        const row = document.createElement("div");
        row.className = "message-row";
        const message = document.createElement("p");
        message.textContent = item.message;
        const actions = document.createElement("div");
        actions.className = "test-row";
        actions.append(this.button("replay", async () => {
          const job = await this.command("voicemail", { action: "replay", id: item.id });
          this.watch(job.id);
        }), this.button("delete", async () => {
          await this.command("voicemail", { action: "delete", id: item.id });
          await this.refresh();
        }));
        row.append(message, actions);
        list.append(row);
      }
      this.panels.voicemail.replaceChildren(list);
    }
  }
  close() {
    this.closed = true;
    for (const timer of this.timers) clearTimeout(timer);
    this.timers.clear();
    this.station?.close();
    this.wizard.closed = true;
    this.dialog.close();
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
    this.edit = this.button("edit_setup", () => {
      this.active = null;
      this.form.hidden = false;
      this.review.replaceChildren();
      this.tests.replaceChildren();
      this.renderActions();
      this.edit.hidden = true;
      controls.quickTests.hidden = false;
    });
    this.edit.hidden = true;
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
      this.edit,
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
    this.form.hidden = true;
    this.edit.hidden = false;
    this.controls.quickTests.hidden = plan.state === "applied";
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


class ShortcutBuilder {
  constructor(controls) {
    this.controls = controls;
    this.root = document.createElement("section");
    this.root.className = "shortcut-builder";
    const label = (key, child) => {
      const result = document.createElement("label");
      result.append(document.createTextNode(controls.t(key)), child);
      return result;
    };
    const description = document.createElement("p");
    description.textContent = controls.t("shortcut_description");
    this.room = controls.select("shortcut_room");
    this.format = controls.select("shortcut_format");
    for (const key of ["native", "native_signed", "cherri", "shortcut", "signed"]) this.format.append(new Option(controls.t(`shortcut_format_${key}`), key));
    this.multiple = document.createElement("input");
    this.multiple.type = "checkbox";
    this.multipleLabel = label("shortcut_multiple", this.multiple);
    this.multipleLabel.className = "remote-consent";
    this.messageMode = controls.select("shortcut_message_mode");
    for (const key of ["ask", "input"]) this.messageMode.append(new Option(controls.t(`shortcut_message_${key}`), key));
    this.messageLabel = label("shortcut_message_mode", this.messageMode);
    this.multiple.addEventListener("change", () => this.update());
    this.messageMode.addEventListener("change", () => this.update());
    this.key = document.createElement("input");
    this.key.type = "password";
    this.key.autocomplete = "off";
    this.key.maxLength = 512;
    this.key.setAttribute("aria-label", controls.t("shortcut_signing_key"));
    this.consent = document.createElement("input");
    this.consent.type = "checkbox";
    const consentLabel = label("shortcut_remote_consent", this.consent);
    consentLabel.className = "remote-consent";
    this.remote = document.createElement("section");
    this.remote.append(label("shortcut_signing_key", this.key), consentLabel);
    this.remote.hidden = true;
    const hint = document.createElement("p");
    hint.className = "muted";
    hint.textContent = controls.t("shortcut_signing_help");
    this.status = document.createElement("p");
    this.status.setAttribute("role", "status");
    this.status.className = "muted";
    this.instructions = document.createElement("div");
    this.instructions.className = "shortcut-instructions";
    this.signingHint = hint;
    this.download = controls.button("shortcut_download", () => this.generate());
    this.room.addEventListener("change", () => this.update());
    this.format.addEventListener("change", () => {
      this.key.value = "";
      this.consent.checked = false;
      this.status.textContent = "";
      this.update();
    });
    this.root.append(description, label("shortcut_room", this.room), label("shortcut_format", this.format),
      this.multipleLabel, this.messageLabel, this.instructions, this.remote, this.download, this.status, hint);
    controls.dialog.addEventListener("close", () => { this.key.value = ""; this.consent.checked = false; });
  }
  rooms(rooms) {
    const selected = this.room.value || this.controls.card._target;
    this.room.replaceChildren(new Option(this.controls.t("shortcut_choose_room"), ""),
      new Option(this.controls.t("shortcut_ask_each_time"), "ask"),
      ...rooms.map(room => new Option(room.name, room.area_id)));
    // Everywhere is an explicit destination, never a missing-room fallback.
    this.room.append(new Option(this.controls.t("shortcut_everywhere"), "all"));
    this.room.value = selected || "ask";
    this.update();
  }
  update() {
    this.download.disabled = !this.room.value || this.busy;
    for (const input of [this.room, this.format, this.key, this.consent, this.multiple, this.messageMode]) input.disabled = Boolean(this.busy);
    const native = this.format.value.startsWith("native");
    const askOption = this.room.querySelector('option[value="ask"]');
    if (askOption) askOption.disabled = !native;
    if (!native && this.room.value === "ask") this.room.value = "";
    this.download.disabled = !this.room.value || this.busy;
    this.download.hidden = false;
    this.instructions.hidden = !native;
    this.signingHint.hidden = false;
    this.remote.hidden = !["signed", "native_signed"].includes(this.format.value);
    this.multipleLabel.hidden = !native || this.room.value !== "ask";
    this.messageLabel.hidden = !native;
    const name = this.room.selectedOptions[0]?.textContent || "";
    const values = {room:this.room.value, name, prompt:this.controls.t("shortcut_prompt", {room:name})};
    this.instructions.replaceChildren(...(this.room.value ?
      [this.room.value === "ask" ? "shortcut_live_help" : "shortcut_native_dictionary",
        this.messageMode.value === "input" ? "shortcut_input_help" : "shortcut_native_ask",
        "shortcut_native_import", "shortcut_native_note"] :
      ["shortcut_choose_room"]).map(key => {
        const paragraph = document.createElement("p");
        paragraph.textContent = this.controls.t(key, values);
        return paragraph;
      }));
  }
  async generate() {
    if (this.busy || !this.room.value) return;
    if (["signed", "native_signed"].includes(this.format.value) && (!this.consent.checked || !this.key.value.trim())) {
      this.status.textContent = this.controls.t("shortcut_signing_required");
      return;
    }
    this.busy = true;
    this.update();
    this.status.textContent = this.controls.t("shortcut_generating");
    try {
      const language = (this.controls.card._hass.language || "en").split("-")[0];
      const response = await this.controls.card._hass.fetchWithAuth("/api/roomcall/shortcut", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({room:this.room.value, format:this.format.value,
          multiple:this.format.value.startsWith("native") && this.room.value === "ask" && this.multiple.checked,
          message_mode:this.format.value.startsWith("native") ? this.messageMode.value : "ask",
          language:["en","es","de","fr","nl"].includes(language)?language:"en",
          allow_remote:this.consent.checked, signing_key:this.key.value}),
      });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(result.error?.message || this.controls.t("shortcut_signing_failed"));
      }
      const header = response.headers.get("Content-Disposition") || "";
      const encoded = header.split("UTF-8''")[1];
      const filename = encoded ? decodeURIComponent(encoded) : "RoomCall.shortcut";
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = filename; this.root.append(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      this.status.textContent = this.controls.t(["signed", "native_signed"].includes(this.format.value) ? "shortcut_signed_ready" : "shortcut_source_ready");
    } catch (error) { this.status.textContent = error.message; }
    finally { this.key.value = ""; this.busy = false; this.update(); }
  }
}
