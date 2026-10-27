import XCTest

final class RoomCallUITests: XCTestCase {
    func testIncomingEchoShowsAnswerAndDecline() {
        executionTimeAllowance = 120
        let app = launch(["-RoomCallDemo", "-RoomCallDemoIncoming", "-RoomCallDemoAppearance", "light"])
        XCTAssertTrue(app.buttons["echo-answer"].waitForExistence(timeout: 30))
        XCTAssertTrue(app.staticTexts["Basement Echo"].exists)
        XCTAssertTrue(app.buttons["echo-decline"].exists)
        keep("ios-incoming-echo-light")
        app.buttons["echo-decline"].tap()
        XCTAssertFalse(app.buttons["echo-answer"].waitForExistence(timeout: 3))
    }

    func testSignInRequiresAValidServerAtAccessibilityTextSize() {
        // A cold CI automation session can consume most of the one-minute default.
        executionTimeAllowance = 120
        let app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(en)", "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        let field = app.textFields["server-url"]
        let signIn = app.buttons["sign-in"]
        // SwiftUI Form virtualizes rows below the viewport at large text sizes.
        for _ in 0..<6 {
            if field.exists && signIn.exists { break }
            app.swipeUp()
        }
        XCTAssertTrue(field.exists)
        XCTAssertTrue(signIn.exists)
        XCTAssertFalse(signIn.isEnabled)
        field.tap()
        field.typeText("https://ha.example")
        XCTAssertTrue(signIn.isEnabled)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "Sign in at accessibility text size"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    /// Real flows against the in-app demo household: confirm a task, send a message, pick targets.
    func testTasksMessagesAndTargets() {
        executionTimeAllowance = 120
        let app = launch(["-RoomCallDemo"])
        // The open laundry task is on the Talk screen; confirming it with its own words clears it.
        let done = app.buttons["done-laundry"]
        XCTAssertTrue(done.waitForExistence(timeout: 30))
        done.tap()
        let notice = app.staticTexts["notice"]
        XCTAssertTrue(notice.waitForExistence(timeout: 10))
        XCTAssertEqual(notice.label, "Marked done.")
        XCTAssertFalse(app.buttons["done-laundry"].waitForExistence(timeout: 3))
        // Pick a room, then type a message to it.
        app.buttons["Kitchen"].tap()
        XCTAssertTrue(app.staticTexts["To: Kitchen"].exists)
        app.tabBars.buttons.element(boundBy: 1).tap()
        let field = app.textFields["message-text"]
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        field.tap()
        field.typeText("Dinner is ready")
        let send = app.buttons["send-message"]
        XCTAssertEqual(send.label, "Send")
        send.tap()
        XCTAssertTrue(notice.waitForExistence(timeout: 10))
        XCTAssertEqual(notice.label, "Message queued; audibility is unconfirmed.")
    }

    /// Settings → Appearance: pick a colour preset; restore the default for later screenshot runs.
    func testAppearanceColourPreset() {
        executionTimeAllowance = 120
        let app = launch(["-RoomCallDemo", "-RoomCallDemoTab", "settings"])
        let purple = app.buttons["accent-purple"]
        for _ in 0..<4 where !purple.exists { app.swipeUp() }
        XCTAssertTrue(purple.waitForExistence(timeout: 30))
        purple.tap()
        XCTAssertTrue(purple.isSelected)
        let blue = app.buttons["accent-blue"]
        blue.tap()
        XCTAssertTrue(blue.isSelected)
        XCTAssertFalse(purple.isSelected)
    }

    // MARK: Documentation screenshots (exported from the result bundle in CI)

    func testScreenshotSignIn() {
        executionTimeAllowance = 120
        for appearance in ["light", "dark"] {
            let app = launch(["-RoomCallDemoAppearance", appearance])
            let field = app.textFields["server-url"]
            XCTAssertTrue(field.waitForExistence(timeout: 30))
            keep("ios-sign-in-\(appearance)")
            app.terminate()
        }
    }

    func testScreenshotTabsLight() { tabs("light") }
    func testScreenshotTabsDark() { tabs("dark") }

    func testScreenshotTalkingAndDoorbell() {
        executionTimeAllowance = 120
        for appearance in ["light", "dark"] {
            let talking = launch(["-RoomCallDemo", "-RoomCallDemoRoom", "kitchen", "-RoomCallDemoTalking",
                                  "-RoomCallDemoAppearance", appearance])
            XCTAssertTrue(talking.descendants(matching: .any)["talk-button"].waitForExistence(timeout: 30))
            sleep(1)
            keep("ios-talking-\(appearance)")
            talking.terminate()
        }
        for appearance in ["light", "dark"] {
            let door = launch(["-RoomCallDemo", "-RoomCallDemoDoorbell", "-RoomCallDemoAppearance", appearance])
            XCTAssertTrue(door.staticTexts["Someone is at the Front Door."].waitForExistence(timeout: 30))
            sleep(2)  // the snapshot loads
            keep("ios-doorbell-\(appearance)")
            door.terminate()
        }
    }

    private func tabs(_ appearance: String) {
        executionTimeAllowance = 120
        let app = launch(["-RoomCallDemo", "-RoomCallDemoRoom", "kitchen", "-RoomCallDemoAppearance", appearance])
        XCTAssertTrue(app.buttons["Kitchen"].waitForExistence(timeout: 30))
        sleep(1)
        keep("ios-rooms-\(appearance)")
        for (index, name) in ["message", "tasks", "history", "settings"].enumerated() {
            app.tabBars.buttons.element(boundBy: index + 1).tap()
            sleep(1)
            keep("ios-\(name)-\(appearance)")
        }
    }

    private func launch(_ arguments: [String]) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(en)", "-AppleLocale", "en_US"] + arguments
        app.launch()
        return app
    }

    private func keep(_ name: String) {
        let screenshot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        screenshot.name = name
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }
}
