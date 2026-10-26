"""Browser QA with real WebRTC/fake microphones and the documented API harness.

Run with ``python scripts/qa_communication.py`` after installing Playwright Chromium.
Backend authorization, queueing and signaling are separately exercised in tests_ha.
"""

from __future__ import annotations

from ha_screenshots import ORIGIN, OUT, open_card
from playwright.sync_api import expect, sync_playwright
from qa_layout import check_appearances, check_large_text, check_layout, check_simple


def check_guided_setup(browser, errors) -> None:
    for width, height in [(240, 600), (390, 844), (1280, 900)]:
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        open_card(page, "mobile" if width < 600 else "desktop", "dark")
        card = page.locator("roomcall-card")
        card.get_by_role("button", name="Settings", exact=True).click()
        wizard = card.locator(".setup-wizard")
        expect(wizard.locator(".wizard-status")).to_contain_text("Found")
        speakers = wizard.locator('fieldset input[type="checkbox"]')
        for index in range(1, speakers.count()):
            speakers.nth(index).uncheck()
        wizard.get_by_role("spinbutton", name="Announcement volume").fill("0.3")
        wizard.get_by_role("spinbutton", name="Speaking speed").fill("0.85")
        wizard.get_by_role("button", name="Review setup plan", exact=True).click()
        expect(wizard.locator(".setup-review")).to_contain_text("0.3")
        expect(wizard.locator(".setup-review")).to_contain_text("Speaking speed: 0.85\u00d7")
        assert (
            page.evaluate(
                "ROOMCALL_HARNESS.requests.findLast(r=>r.request?.action==='plan')"
                ".request.settings.speech_rate"
            )
            == 0.85
        )
        assert (
            page.evaluate("ROOMCALL_HARNESS.requests.filter(r=>r.request?.action==='apply').length")
            == 0
        )
        wizard.get_by_role("button", name="Apply this plan", exact=True).click()
        expect(wizard.get_by_role("button", name="Verify setup", exact=True)).to_be_visible()
        wizard.get_by_role("button", name="Verify setup", exact=True).click()
        expect(wizard.locator(".wizard-status")).to_contain_text("Test each speaker")
        # Reloading resumes the persisted plan; it does not apply again or play audio.
        page.reload()
        card = page.locator("roomcall-card")
        card.get_by_role("button", name="Settings", exact=True).click()
        wizard = card.locator(".setup-wizard")
        expect(wizard.locator(".setup-review")).to_contain_text("Applied")
        # The applied plan hides the form, so find its fields by label rather than by role.
        expect(wizard.locator('input[aria-label^="Announcement volume"]')).to_have_value("0.3")
        expect(wizard.locator('input[aria-label="Speaking speed"]')).to_have_value("0.85")
        wizard.get_by_role("button", name="Play test sound", exact=True).click()
        wizard.get_by_role("button", name="Yes", exact=True).click()
        expect(wizard.locator(".wizard-status")).to_contain_text("Setup verified")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert wizard.evaluate("e=>e.scrollWidth<=e.clientWidth")
        if width == 390:
            card.get_by_role("dialog", name="Settings", exact=True).screenshot(
                path=OUT / "ha-guided-setup.png"
            )
        wizard.get_by_role("button", name="Undo this setup", exact=True).click()
        expect(wizard.locator(".wizard-status")).to_contain_text("Explicitly confirm")
        wizard.get_by_role("checkbox", name="Confirm restoring", exact=False).check()
        wizard.get_by_role("button", name="Undo this setup", exact=True).click()
        expect(wizard.locator(".setup-review")).to_contain_text("Previous settings restored")
        page.evaluate("""() => {
          const hass=document.querySelector('roomcall-card')._hass;
          const original=hass.callApi.bind(hass);
          hass.callApi=async(method,path,body)=>body?.operation==='sound_test'
            ? {ok:false,error:{message:'Speaker temporarily unavailable'}}
            : original(method,path,body);
        }""")
        card.locator(".quick-tests").get_by_role(
            "button", name="Play test sound", exact=True
        ).first.click()
        expect(card.locator(".settings-body > .settings-result")).to_contain_text(
            "Speaker temporarily unavailable"
        )
        context.close()


def check_device_discovery(browser, errors) -> None:
    """Audio routes and controls stay distinct, including a recent unavailable Cast TV."""
    for width in (240, 390, 600):
        context = browser.new_context(viewport={"width": width, "height": 950})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        open_card(page, "mobile", "dark", "&discovery=tv")
        card = page.locator("roomcall-card")
        card.get_by_role("button", name="Settings", exact=True).click()
        wizard = card.locator(".setup-wizard")
        audio = wizard.get_by_role("checkbox", name="Living Room TV · Google Cast", exact=False)
        remote = wizard.get_by_role(
            "checkbox", name="Living Room TV · Android TV Remote", exact=False
        )
        expect(audio).to_be_enabled()
        expect(remote).to_be_disabled()
        expect(wizard).to_contain_text("Unavailable; seen in the past 24 hours")
        expect(wizard).to_contain_text("Control only. For audio")
        if width == 390:
            remote.scroll_into_view_if_needed()
            card.get_by_role("dialog", name="Settings", exact=True).screenshot(
                path=OUT / "ha-device-discovery.png"
            )
        for checkbox in wizard.locator('fieldset input[type="checkbox"]').all():
            if checkbox.is_enabled():
                checkbox.uncheck()
        audio.check()
        wizard.get_by_role("button", name="Review setup plan", exact=True).click()
        assert page.evaluate(
            "ROOMCALL_HARNESS.requests.findLast(r=>r.request?.action==='plan').request.settings.speakers"
        ) == ["media_player.google_tv"]
        assert wizard.evaluate("e=>e.scrollWidth<=e.clientWidth")
        context.close()


def check_room_listening(browser, errors) -> None:
    """One speaker at a time; No/Skip persist without becoming audible success."""
    for width in (240, 390, 600):
        context = browser.new_context(viewport={"width": width, "height": 844})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        open_card(page, "mobile", "dark")
        card = page.locator("roomcall-card")
        card.get_by_role("button", name="Settings", exact=True).click()
        wizard = card.locator(".setup-wizard")
        expect(wizard.locator(".wizard-status")).to_contain_text("Found")
        speakers = wizard.locator('fieldset input[type="checkbox"]')
        for index in range(3, speakers.count()):
            speakers.nth(index).uncheck()
        wizard.get_by_role("button", name="Review setup plan", exact=True).click()
        wizard.get_by_role("button", name="Apply this plan", exact=True).click()
        expect(wizard.get_by_role("button", name="Play test sound", exact=True)).to_have_count(1)
        expect(wizard.get_by_role("button", name="Yes", exact=True)).to_have_count(0)
        wizard.get_by_role("button", name="Play test sound", exact=True).click()
        wizard.get_by_role("button", name="Yes", exact=True).click()
        expect(wizard.locator(".test-row")).not_to_contain_text("Did you hear it?")
        wizard.get_by_role("button", name="Play test sound", exact=True).click()
        expect(wizard.locator(".test-row")).to_contain_text("Did you hear it?")
        if width == 390:
            card.get_by_role("dialog", name="Settings", exact=True).screenshot(
                path=OUT / "ha-room-listening.png"
            )
        wizard.get_by_role("button", name="No", exact=True).click()
        expect(wizard.get_by_role("link", name="Troubleshoot silent speakers")).to_be_visible()
        wizard.get_by_role("button", name="Skip", exact=True).click()
        expect(wizard.locator(".wizard-status")).to_contain_text("Test each speaker")
        expect(wizard.locator(".test-row")).to_have_count(0)
        tests = page.evaluate("ROOMCALL_HARNESS.requests.filter(r=>r.request?.action==='test')")
        assert len(tests) == 2 and tests[0]["request"]["speaker"] != tests[1]["request"]["speaker"]
        page.reload()
        card.get_by_role("button", name="Settings", exact=True).click()
        wizard = card.locator(".setup-wizard")
        expect(wizard).to_contain_text("Heard (you confirmed)")
        expect(wizard).to_contain_text("Not heard; unverified")
        expect(wizard).to_contain_text("Skipped; unverified")
        assert wizard.evaluate("e=>e.scrollWidth<=e.clientWidth")
        # HA restarts can lose a non-durable test job while retaining its setup plan.
        page.evaluate("""() => {
          const plans=JSON.parse(sessionStorage.getItem('roomcall-plans'));
          const plan=plans[0][1], speaker=plan.settings.speakers[0];
          delete plan.confirmations[speaker]; plan.tests[speaker]='expired-test';
          sessionStorage.setItem('roomcall-plans',JSON.stringify(plans));
        }""")
        page.reload()
        card.get_by_role("button", name="Settings", exact=True).click()
        wizard = card.locator(".setup-wizard")
        expect(wizard.get_by_role("button", name="Play test sound", exact=True)).to_be_visible()
        wizard.get_by_role("button", name="Skip", exact=True).click()
        expect(wizard).to_contain_text("Skipped; unverified")
        context.close()


def check_shortcuts(browser, errors) -> None:
    for width in (240, 390, 900):
        context = browser.new_context(viewport={"width": width, "height": 820})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        open_card(page, "mobile", "dark")
        card = page.locator("roomcall-card")
        card.get_by_role("button", name="Settings", exact=True).click()
        card.get_by_role("tab", name="Siri Shortcuts", exact=True).click()
        panel = card.locator(".shortcut-builder")
        panel.get_by_role("combobox", name="Destination room", exact=True).select_option("basement")
        expect(panel).to_contain_text("Fixed destination: Basement (basement)")
        panel.get_by_role("combobox", name="Destination room", exact=True).select_option("ask")
        panel.get_by_role("checkbox", name="Allow selecting several rooms", exact=True).check()
        panel.get_by_role("combobox", name="Message source", exact=True).select_option("input")
        expect(panel).to_contain_text("retrieves your current permitted RoomCall rooms")
        expect(panel).to_contain_text("empty input is rejected")
        with page.expect_download():
            panel.get_by_role("button", name="Generate shortcut", exact=True).click()
        exported = page.evaluate(
            "ROOMCALL_HARNESS.requests.filter(r=>r.operation==='shortcut').at(-1).request"
        )
        assert exported["room"] == "ask" and exported["multiple"] is True
        assert exported["format"] == "native" and exported["message_mode"] == "input"
        if width == 390:
            card.locator(".settings-body").evaluate("e => { e.scrollTop = 0; }")
            card.locator("dialog").screenshot(path=OUT / "ha-siri-shortcuts.png")
        panel.get_by_role("combobox", name="Setup method", exact=True).select_option("cherri")
        expect(panel.get_by_role("button", name="Generate shortcut", exact=True)).to_be_disabled()
        panel.get_by_role("combobox", name="Destination room", exact=True).select_option("basement")
        with page.expect_download() as pending:
            panel.get_by_role("button", name="Generate shortcut", exact=True).click()
        download = pending.value
        assert download.suggested_filename == "RoomCall basement.cherri"
        assert "basement" in download.path().read_text()
        expect(panel).to_contain_text("Sign it before importing")
        assert panel.evaluate("e=>e.scrollWidth<=e.clientWidth")
        panel.get_by_role("combobox", name="Setup method", exact=True).select_option("signed")
        panel.get_by_role("button", name="Generate shortcut", exact=True).click()
        expect(panel).to_contain_text("require a HubSign key")
        assert (
            page.evaluate("ROOMCALL_HARNESS.requests.filter(r=>r.operation==='shortcut').length")
            == 2
        )
        panel.get_by_role("textbox", name="HubSign API key (not saved)", exact=True).fill("bad")
        panel.get_by_role("checkbox").check()
        panel.get_by_role("button", name="Generate shortcut", exact=True).click()
        expect(panel).to_contain_text("Signing failed")
        expect(
            panel.get_by_role("textbox", name="HubSign API key (not saved)", exact=True)
        ).to_have_value("")
        panel.get_by_role("textbox", name="HubSign API key (not saved)", exact=True).fill("test")
        with page.expect_download() as pending:
            panel.get_by_role("button", name="Generate shortcut", exact=True).click()
        assert pending.value.suggested_filename == "RoomCall basement.shortcut"
        expect(panel).to_contain_text("Signed shortcut downloaded")
        page.keyboard.press("Escape")


def check_task_added_later(browser, errors) -> None:
    """A dashboard that loaded with no tasks shows one when its sensor appears and opens."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    open_card(page, "mobile", "dark")
    card = page.locator("roomcall-card")
    region = card.get_by_role("region", name="To do", exact=True)
    expect(region).to_be_hidden()
    page.evaluate("""() => {
      const h = ROOMCALL_HARNESS, task = h.tasks[0];
      task.state = "open";
      h.hass.states[task.entity_id] = {entity_id: task.entity_id, state: "open",
        last_changed: new Date().toISOString(), attributes: {task: task.task, done_by: null}};
      document.querySelector("roomcall-card").hass = {...h.hass};
    }""")
    expect(region).to_be_visible()
    expect(region).to_contain_text("Laundry")
    context.close()


def check_doorbell(browser, errors) -> None:
    """A press shows the banner; Answer opens the camera's live view; no ring at load."""
    for width in (240, 390, 900):
        context = browser.new_context(viewport={"width": width, "height": 844})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        open_card(page, "mobile" if width < 600 else "desktop", "dark", "&doorbell=on")
        card = page.locator("roomcall-card")
        banner = card.locator(".doorbell")
        expect(banner).to_be_hidden()
        page.evaluate("""() => {
          const h = ROOMCALL_HARNESS, card = document.querySelector("roomcall-card");
          window.opened = null;
          card.addEventListener("hass-more-info", (e) => { window.opened = e.detail.entityId; });
          h.hass.states["binary_sensor.front_door_visitor"] = {
            state: "on", last_changed: new Date().toISOString(), attributes: {} };
          card.hass = {...h.hass};
        }""")
        expect(banner).to_be_visible()
        expect(banner).to_contain_text("Someone is at the Front Door.")
        assert card.evaluate(
            "e=>{const c=e.shadowRoot.querySelector('ha-card');return c.scrollWidth<=c.clientWidth}"
        )
        answer = banner.get_by_role("button", name="Answer", exact=True)
        bounds = answer.bounding_box()
        assert bounds and bounds["height"] >= 44, bounds
        if width == 390:
            page.wait_for_timeout(300)
            card.screenshot(path=OUT / "ha-card-doorbell.png")
        answer.click()
        assert page.evaluate("window.opened") == "camera.front_door"
        expect(banner).to_be_hidden()
        # A bounce within 10 seconds, or coming back from unavailable, doesn't reopen it.
        for state in ("off", "on", "unavailable", "on", "unknown", "on"):
            page.evaluate(
                """state => {
              const h = ROOMCALL_HARNESS, card = document.querySelector("roomcall-card");
              h.hass.states["binary_sensor.front_door_visitor"] = {
                state, last_changed: new Date(Date.now() + 1000).toISOString(), attributes: {} };
              card.hass = {...h.hass};
            }""",
                state,
            )
            page.wait_for_timeout(50)
        expect(banner).to_be_hidden()
        context.close()


def check_doorbell_changes(browser, errors) -> None:
    """An open dashboard picks up a newly configured doorbell; stale rings don't show."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    open_card(page, "mobile", "dark")
    card = page.locator("roomcall-card")
    banner = card.locator(".doorbell")
    page.evaluate("""() => {
      const h = ROOMCALL_HARNESS, card = document.querySelector("roomcall-card");
      window.roomcallDoorbell = {entity: "binary_sensor.porch", camera: null, name: "Porch"};
      h.hass.states["binary_sensor.porch"] = {state: "off", last_changed: new Date().toISOString(),
        attributes: {}};
      // Just fetched: the throttled refresh must still happen once the minute is up.
      card._doorbellFetched = Date.now() - 59800;
      card.hass = {...h.hass};
    }""")
    page.wait_for_function("document.querySelector('roomcall-card')._doorbell?.name === 'Porch'")
    # A ring from an hour ago, seen when a suspended tablet wakes, is ignored.
    page.evaluate("""() => {
      const h = ROOMCALL_HARNESS, card = document.querySelector("roomcall-card");
      h.hass.states["binary_sensor.porch"] = {state: "on", attributes: {},
        last_changed: new Date(Date.now() - 3600000).toISOString()};
      card.hass = {...h.hass};
    }""")
    page.wait_for_timeout(50)
    expect(banner).to_be_hidden()
    page.evaluate("""() => {
      const h = ROOMCALL_HARNESS, card = document.querySelector("roomcall-card");
      h.hass.states["binary_sensor.porch"] = {state: "off", attributes: {},
        last_changed: new Date().toISOString()};
      card.hass = {...h.hass};
      h.hass.states["binary_sensor.porch"] = {state: "on", attributes: {},
        last_changed: new Date(Date.now() + 1).toISOString()};
      card.hass = {...h.hass};
    }""")
    expect(banner).to_contain_text("Someone is at the Porch.")
    context.close()


def check_phone_tab(browser, errors) -> None:
    """Settings → iPhone app shows a scannable connect code and a matching link."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    open_card(page, "mobile", "dark")
    card = page.locator("roomcall-card")
    card.get_by_role("button", name="Settings", exact=True).click()
    dialog = card.get_by_role("dialog", name="Settings", exact=True)
    dialog.get_by_role("tab", name="iPhone app", exact=True).click()
    qr = dialog.get_by_role("img", name="QR code to connect the RoomCall iPhone app", exact=True)
    expect(qr).to_be_visible()
    link = dialog.get_by_role("link", name="Open in the RoomCall app", exact=True)
    assert link.get_attribute("href") == "roomcall://connect?server=http%3A%2F%2Flocalhost%3A8123"
    assert dialog.evaluate("e=>e.scrollWidth<=e.clientWidth")
    context.close()


def check_tasks(browser, errors) -> None:
    """An open task is confirmed with its own words; there is no dismiss button."""
    for width in (240, 390, 900):
        context = browser.new_context(viewport={"width": width, "height": 844})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        open_card(page, "mobile" if width < 600 else "desktop", "dark", "&tasks=open")
        card = page.locator("roomcall-card")
        region = card.get_by_role("region", name="To do", exact=True)
        expect(region).to_be_visible()
        expect(region).to_contain_text("Laundry")
        done = region.get_by_role("button", name="Took out the dry clothes", exact=True)
        later = region.get_by_role("button", name="Remind me later: Laundry", exact=True)
        for button in (done, later):
            bounds = button.bounding_box()
            assert bounds and bounds["width"] >= 44 and bounds["height"] >= 44, bounds
        assert card.evaluate(
            "e=>{const c=e.shadowRoot.querySelector('ha-card');return c.scrollWidth<=c.clientWidth}"
        )
        assert region.get_by_role("button").count() == 2
        done.click()
        expect(region).to_be_hidden()
        assert (
            page.evaluate(
                "ROOMCALL_HARNESS.requests.filter(r=>r.operation==='tasks'"
                "&&r.request.action==='complete'&&r.request.id==='laundry').length"
            )
            == 1
        )
        context.close()


def check_playback_restore(browser, errors) -> None:
    """Acceptance stays separate from late restoration; failed restore never resends."""
    for width in (240, 390, 900):
        context = browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        open_card(page, "mobile", "dark")
        page.evaluate("""() => {
          const card = document.querySelector('roomcall-card');
          const original = card._hass.callApi.bind(card._hass);
          card._hass.states['media_player.demo_receiver'] = {
            state:'on',attributes:{friendly_name:'Living Room Receiver'}
          };
          window.qaRestoreStage = 'pending';
          window.qaRestoreEnabled = true;
          window.qaRestoreJobs = new Map();
          window.qaRestoreStatusCalls = 0;
          card._hass.callApi = async (method, path, body) => {
            const result = await original(method, path, body);
            if (body?.operation === 'preview') result.interruptions = Object.fromEntries(
              result.endpoints.map(id => [id, {
              controller:'media_player.demo_receiver',
              source:'Living Room TV (shared HDMI audio path)',
              restore_enabled:qaRestoreEnabled,android_app_reopen:true,media_resume:true
            }]));
            if (body?.operation === 'send') {
              result.result = {delivered:['Living Room'],failed:{},evidence:{
                'media_player.living_room':{
                request_accepted:true,interruption:{prepared:true,restore:qaRestoreStage}
              }}};
              qaRestoreJobs.set(result.id,result);
            }
            if (body?.operation === 'status' && qaRestoreJobs.has(body.request.id)) {
              qaRestoreStatusCalls++;
              const job = qaRestoreJobs.get(body.request.id);
              job.result.evidence['media_player.living_room'].interruption.restore = qaRestoreStage;
              return job;
            }
            return result;
          };
        }""")
        card = page.locator("roomcall-card")
        card.locator(".rooms button").filter(has_text="Living Room").click()
        card.locator('[data-mode="type"]').click()
        card.get_by_role("textbox", name="Message", exact=True).fill("Dinner is ready")
        card.get_by_role("button", name="Preview recipients", exact=True).click()
        expect(card.locator(".result")).to_contain_text("Living Room Receiver")
        expect(card.locator(".result")).to_contain_text("exact session recovery is not guaranteed")
        if width == 390:
            card.screenshot(path=OUT / "ha-playback-preview.png")
        card.get_by_role("button", name="Send", exact=True).click()
        timeline = card.get_by_role("list", name="Delivery timeline")
        expect(timeline).to_contain_text("Restoration pending")
        expect(card.locator(".result")).to_contain_text("accepted")
        page.evaluate("qaRestoreStage = 'requests_completed'")
        expect(timeline).to_contain_text("session resumption is best effort")
        if width == 390:
            card.screenshot(path=OUT / "ha-playback-restoration.png")
        count = page.evaluate("qaRestoreStatusCalls")
        page.wait_for_timeout(850)
        assert page.evaluate("qaRestoreStatusCalls") == count
        page.evaluate("qaRestoreStage = 'failed'")
        card.get_by_role("button", name="Send", exact=True).click()
        expect(timeline).to_contain_text("Restoration failed")
        assert (
            page.evaluate("ROOMCALL_HARNESS.requests.filter(r=>r.operation==='send').length") == 2
        )
        page.evaluate("qaRestoreEnabled = false")
        card.get_by_role("button", name="Preview recipients", exact=True).click()
        expect(card.locator(".result")).to_contain_text("automatic restoration is disabled")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        context.close()


def check_echo_calls(browser, errors, *, capture=True) -> None:
    """Real AudioWorklet PCM, consent gating, remote playback and microphone teardown."""
    for width in (240, 390, 600):
        context = browser.new_context(viewport={"width": width, "height": 844})
        if browser.browser_type.name != "firefox":
            context.grant_permissions(["microphone"], origin=ORIGIN)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""
          window.qaTracks=[];
          const capture=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
          navigator.mediaDevices.getUserMedia=async(...args)=>{
            const stream=await capture(...args);qaTracks.push(...stream.getTracks());return stream;
          };
        """)
        open_card(page, "mobile", "dark")
        page.evaluate("""() => {
          const hass=document.querySelector('roomcall-card')._hass;
          hass.user={is_admin:true};
          const api=hass.callApi.bind(hass);
          const subscribe=hass.connection.subscribeMessage.bind(hass.connection);
          const ws=hass.callWS.bind(hass);
          window.echoQA={frames:[],closed:0,opened:0};
          hass.callApi=async(...args)=>{
            const result=await api(...args);
            if(args[2]?.operation==='discover') {
              result.echoes=[{speaker:'media_player.basement',name:'Basement Echo',available:true}];
              result.endpoints[0].route='esphome';
            }
            return result;
          };
          hass.connection.subscribeMessage=async(cb,msg)=>{
            if(msg.type!=='roomcall/echo_call')return subscribe(cb,msg);
            echoQA.opened++;
            echoQA.cb=cb;cb({type:'ringing',id:'echo-call'});
            return ()=>echoQA.closed++;
          };
          hass.callWS=async(msg)=>{
            if(msg.type!=='roomcall/echo_audio')return ws(msg);
            echoQA.frames.push(msg.pcm);
            echoQA.cb({type:'audio',id:'echo-call',pcm:msg.pcm});return {};
          };
        }""")
        card = page.locator("roomcall-card")
        card.get_by_role("button", name="Settings", exact=True).click()
        card.get_by_role("tab", name="Room stations", exact=True).click()
        expect(card.get_by_role("textbox", name="House word", exact=True)).to_have_count(1)
        assert card.get_by_role("dialog").evaluate("e=>e.scrollWidth<=e.clientWidth")
        card.get_by_role("button", name="Call Echo", exact=True).click()
        expect(card.locator(".result")).to_contain_text("Action button")
        expect(card.locator(".destination")).to_be_hidden()
        expect(card.locator(".recipients")).to_be_hidden()
        expect(card.locator("button.talk")).to_be_hidden()
        expect(card.locator(".communication h3").filter(has_text="Basement Echo")).to_be_visible()
        assert page.evaluate("echoQA.frames.length") == 0
        if width == 390 and capture:
            card.locator(".result").scroll_into_view_if_needed()
            page.screenshot(path=OUT / "ha-echo-call.png")
        page.evaluate("""() => {
          echoQA.cb({type:'accepted',id:'echo-call'});
          const call=document.querySelector('roomcall-card')._communication.echoCall;
          echoQA.analyser=call.context.createAnalyser();call.node.connect(echoQA.analyser);
        }""")
        page.wait_for_function("echoQA.frames.length>3")
        assert page.evaluate("atob(echoQA.frames[0]).length") == 1280
        page.wait_for_function("""() => {
          const samples=new Float32Array(echoQA.analyser.fftSize);
          echoQA.analyser.getFloatTimeDomainData(samples);
          return samples.some(value=>Math.abs(value)>.001);
        }""")
        card.get_by_role("button", name="Hang up", exact=True).click()
        page.wait_for_function("qaTracks.length>0&&qaTracks.every(t=>t.readyState==='ended')")
        assert page.evaluate("echoQA.closed") == 1
        expect(card.locator(".result")).to_contain_text("ended", ignore_case=True)
        # A remote hang-up also closes the mic and the socket subscription.
        card.get_by_role("button", name="Settings", exact=True).click()
        card.get_by_role("tab", name="Room stations", exact=True).click()
        card.get_by_role("button", name="Call Echo", exact=True).click()
        page.wait_for_function("echoQA.opened===2")
        page.evaluate("echoQA.cb({type:'ended',id:'echo-call',reason:'ended'})")
        page.wait_for_function("qaTracks.every(t=>t.readyState==='ended')")
        assert page.evaluate("echoQA.closed") == 2
        # Closing the card while microphone permission is pending must stop a late stream.
        page.evaluate("""() => {
          const capture=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
          navigator.mediaDevices.getUserMedia=async(...args)=>{
            const stream=await capture(...args);
            await new Promise(resolve=>echoQA.release=resolve);return stream;
          };
        }""")
        card.get_by_role("button", name="Settings", exact=True).click()
        card.get_by_role("tab", name="Room stations", exact=True).click()
        card.get_by_role("button", name="Call Echo", exact=True).click()
        page.wait_for_function("!!echoQA.release")
        page.evaluate(
            "document.querySelector('roomcall-card')._communication.close();echoQA.release()"
        )
        page.wait_for_function("qaTracks.every(t=>t.readyState==='ended')")
        assert page.evaluate("echoQA.opened") == 2
        context.close()

    # A browser/device that never starts playback must report an actionable
    # error, close capture, and never ring the Echo with unusable audio.
    context = browser.new_context()
    if browser.browser_type.name != "firefox":
        context.grant_permissions(["microphone"], origin=ORIGIN)
    page = context.new_page()
    page.add_init_script("""
      window.qaTracks=[];
      AudioContext.prototype.resume=()=>new Promise(()=>{});
      const capture=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia=async(...args)=>{
        const stream=await capture(...args);qaTracks.push(...stream.getTracks());return stream;
      };
    """)
    open_card(page, "mobile", "dark")
    page.evaluate("""() => {
      const hass=document.querySelector('roomcall-card')._hass;
      const api=hass.callApi.bind(hass);
      hass.callApi=async(...args)=>{
        const result=await api(...args);
        if(args[2]?.operation==='discover')
          result.echoes=[{speaker:'media_player.basement',name:'Basement Echo'}];
        return result;
      };
      window.echoOpened=0;
      hass.connection.subscribeMessage=async()=>{echoOpened++;return ()=>{};};
    }""")
    card = page.locator("roomcall-card")
    card.get_by_role("button", name="Settings", exact=True).click()
    card.get_by_role("tab", name="Room stations", exact=True).click()
    card.get_by_role("button", name="Call Echo", exact=True).click()
    expect(card.get_by_role("dialog")).not_to_be_visible()
    expect(card.locator(".result")).to_contain_text("Browser audio could not start", timeout=12000)
    page.wait_for_function("qaTracks.length>0&&qaTracks.every(t=>t.readyState==='ended')")
    assert page.evaluate("echoOpened") == 0
    expect(card.get_by_role("button", name="Hang up", exact=True)).to_have_count(0)
    context.close()


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
            ]
        )
        errors = []
        check_echo_calls(browser, errors)
        check_layout(browser)
        check_simple(browser)
        check_appearances(browser)
        check_large_text(browser)
        check_guided_setup(browser, errors)
        check_room_listening(browser, errors)
        check_device_discovery(browser, errors)
        check_shortcuts(browser, errors)
        check_playback_restore(browser, errors)
        check_tasks(browser, errors)
        check_task_added_later(browser, errors)
        check_doorbell(browser, errors)
        check_doorbell_changes(browser, errors)
        check_phone_tab(browser, errors)
        for width, height in [(240, 800), (390, 844), (1280, 900)]:
            context = browser.new_context(viewport={"width": width, "height": height})
            context.grant_permissions(["microphone"], origin=ORIGIN)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            open_card(page, "mobile" if width < 600 else "desktop", "dark")
            card = page.locator("roomcall-card")
            expect(card.locator(".communication").first).to_be_visible()
            card.locator('[data-mode="type"]').click()
            card.get_by_role("combobox", name="Household profiles", exact=True).select_option(
                "dinner"
            )
            expect(card.locator(".destination")).to_have_text("Profile: Dinner")
            expect(card.get_by_role("textbox", name="Message", exact=True)).to_have_value(
                "Dinner is ready"
            )
            card.get_by_role("button", name="Preview recipients", exact=True).click()
            expect(card.locator(".result")).to_contain_text("Kitchen")
            assert (
                page.evaluate("ROOMCALL_HARNESS.requests.filter(r=>r.operation==='send').length")
                == 0
            )
            card.locator(".rooms button").filter(has_text="Basement").click()
            expect(card.locator(".destination")).to_have_text("To: Basement")
            card.get_by_role("button", name="Preview recipients", exact=True).click()
            expect(card.locator(".result")).to_contain_text("Basement")
            card.get_by_role("button", name="Send", exact=True).click()
            expect(card.locator(".result")).to_contain_text("audibility unconfirmed")
            timeline = card.get_by_role("list", name="Delivery timeline", exact=True)
            expect(timeline).to_contain_text("Generating speech")
            expect(timeline).to_contain_text("Audio fetched after dispatch")
            expect(timeline).to_contain_text("Playback reported after dispatch")
            sent = page.evaluate(
                "ROOMCALL_HARNESS.requests.filter(r=>r.operation==='send').at(-1).request"
            )
            assert sent["profile"] == "dinner" and sent["areas"] == ["basement"]
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if width == 390:
                page.screenshot(path=OUT / "ha-card-type.png")
            card.locator('[data-mode="ask"]').click()
            card.get_by_role("button", name="Send", exact=True).click()
            expect(card.locator(".result")).to_contain_text("Pizza")
            if width == 390:
                page.screenshot(path=OUT / "ha-card-ask.png")
            card.get_by_role("button", name="Settings", exact=True).click()
            card.get_by_role("button", name="Play test sound", exact=True).first.click()
            card.get_by_role("button", name="I heard it", exact=True).click()
            expect(card.get_by_role("button", name="I heard it", exact=True)).to_have_count(0)
            expect(card.locator(".quick-tests")).to_contain_text("I heard it")
            if width == 390:
                page.screenshot(path=OUT / "ha-card-setup.png")
            context.close()

        context = browser.new_context(viewport={"width": 1000, "height": 1600})
        context.grant_permissions(["microphone"], origin=ORIGIN)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""
            window.qaTracks=[];
            const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia=async(...args)=>{
              const stream=await original(...args);
              qaTracks.push(...stream.getTracks());return stream;
            };
        """)
        open_card(page, "mobile", "dark", "&stations=2")
        cards = page.locator("roomcall-card")
        expect(cards.locator(".result")).to_have_count(2)
        for index in (0, 1):
            card = cards.nth(index)
            card.get_by_role("button", name="Settings", exact=True).click()
            card.get_by_role("tab", name="Room stations", exact=True).click()
            card.get_by_role("button", name="Enable this station", exact=True).click()
            card.get_by_role("button", name="Close", exact=True).click()
        cards.first.get_by_role("button", name="Settings", exact=True).click()
        cards.first.get_by_role("tab", name="Room stations", exact=True).click()
        cards.first.get_by_role("button", name="Refresh", exact=True).click()
        page.wait_for_function("ROOMCALL_HARNESS.stations.size===2")
        cards.first.locator('select[aria-label="Call station"]').select_option(label="Basement")
        cards.first.get_by_role("button", name="Call station", exact=True).click()
        expect(cards.nth(1).get_by_role("button", name="Answer", exact=True)).to_be_visible()
        assert page.evaluate("qaTracks.length") == 1  # Callee mic is still closed.
        cards.nth(1).get_by_role("button", name="Answer", exact=True).click()
        page.wait_for_function(
            "[...document.querySelectorAll('roomcall-card')].every(c=>c._communication.station?.peer?.connectionState==='connected')",
            timeout=20000,
        )
        expect(cards.locator("audio")).to_have_count(2)
        cards.first.get_by_role("button", name="Hang up", exact=True).click()
        page.wait_for_function("qaTracks.every(t=>t.readyState==='ended')")
        assert page.evaluate("ROOMCALL_HARNESS.sessions.size") == 0
        context.close()
        browser.close()
        assert not errors, errors
    print("Desktop/mobile messaging, previews, Q&A, sound tests and consent-based WebRTC passed.")


if __name__ == "__main__":
    main()
