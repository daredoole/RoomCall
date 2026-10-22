import XCTest

final class RoomCallUITests: XCTestCase {
    func testSignInRequiresAValidServerAtAccessibilityTextSize() {
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
}
