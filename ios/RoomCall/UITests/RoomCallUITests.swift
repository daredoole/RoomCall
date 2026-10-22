import XCTest

final class RoomCallUITests: XCTestCase {
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

    // Real simulator captures for the README and docs (exported from the result bundle in CI).
    func testScreenshotSignIn() {
        executionTimeAllowance = 120
        for appearance in ["light", "dark"] {
            let app = launch(["-RoomCallDemoAppearance", appearance])
            let field = app.textFields["server-url"]
            XCTAssertTrue(field.waitForExistence(timeout: 30))
            field.tap()
            field.typeText("https://homeassistant.local:8123\n")
            keep("ios-sign-in-\(appearance)")
            app.terminate()
        }
    }

    func testScreenshotRooms() {
        executionTimeAllowance = 120
        for appearance in ["light", "dark"] {
            let app = launch(["-RoomCallDemo", "-RoomCallDemoRoom", "kitchen", "-RoomCallDemoAppearance", appearance])
            XCTAssertTrue(app.buttons["Kitchen"].waitForExistence(timeout: 30))
            keep("ios-rooms-\(appearance)")
            app.terminate()
        }
    }

    func testScreenshotTalking() {
        executionTimeAllowance = 120
        for appearance in ["light", "dark"] {
            let app = launch(["-RoomCallDemo", "-RoomCallDemoRoom", "kitchen", "-RoomCallDemoJoined",
                              "-RoomCallDemoTalking", "-RoomCallDemoAppearance", appearance])
            XCTAssertTrue(app.staticTexts["ptt-status"].waitForExistence(timeout: 30))
            sleep(2)
            keep("ios-talking-\(appearance)")
            app.terminate()
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
