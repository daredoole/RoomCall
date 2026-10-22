import XCTest

final class RoomCallUITests: XCTestCase {
    func testSignInRequiresAValidServerAtAccessibilityTextSize() {
        let app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(en)", "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        let field = app.textFields["server-url"]
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["sign-in"].isEnabled)
        field.tap()
        field.typeText("https://ha.example")
        XCTAssertTrue(app.buttons["sign-in"].isEnabled)
    }
}
