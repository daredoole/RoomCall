"""Browser QA with real WebRTC/fake microphones and the documented API harness.

Run with ``python scripts/qa_communication.py`` after installing Playwright Chromium.
Backend authorization, queueing and signaling are separately exercised in tests_ha.
"""

from __future__ import annotations

from ha_screenshots import ORIGIN, OUT, open_card
from playwright.sync_api import expect, sync_playwright
from qa_layout import check_large_text, check_layout


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
        wizard.get_by_role("spinbutton").fill("0.3")
        wizard.get_by_role("button", name="Review setup plan", exact=True).click()
        expect(wizard.locator(".setup-review")).to_contain_text("0.3")
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
        expect(wizard.locator('input[type="number"]')).to_have_value("0.3")
        wizard.get_by_role("button", name="Play test sound", exact=True).click()
        wizard.get_by_role("button", name="I heard it", exact=True).click()
        expect(wizard.locator(".wizard-status")).to_contain_text("Setup verified")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert wizard.evaluate("e=>e.scrollWidth<=e.clientWidth")
        if width == 390:
            wizard.screenshot(path=OUT / "ha-guided-setup.png")
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
        check_layout(browser)
        check_large_text(browser)
        check_guided_setup(browser, errors)
        check_shortcuts(browser, errors)
        check_tasks(browser, errors)
        check_task_added_later(browser, errors)
        check_doorbell(browser, errors)
        check_doorbell_changes(browser, errors)
        for width, height in [(390, 844), (1280, 900)]:
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
            expect(card.get_by_role("textbox", name="Message", exact=True)).to_have_value(
                "Dinner is ready"
            )
            card.get_by_role("button", name="Preview recipients", exact=True).click()
            expect(card.locator(".result")).to_contain_text("Kitchen")
            assert (
                page.evaluate("ROOMCALL_HARNESS.requests.filter(r=>r.operation==='send').length")
                == 0
            )
            card.get_by_role("button", name="Send", exact=True).click()
            expect(card.locator(".result")).to_contain_text("audibility unconfirmed")
            sent = page.evaluate(
                "ROOMCALL_HARNESS.requests.filter(r=>r.operation==='send').at(-1).request"
            )
            assert sent["profile"] == "dinner" and "areas" not in sent
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
