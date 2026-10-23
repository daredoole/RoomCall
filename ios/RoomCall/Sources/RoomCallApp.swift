import SwiftUI

@main
struct RoomCallApp: App {
    @StateObject private var model = AppModel()
    @AppStorage(ThemeSettings.modeKey, store: RoomCache.defaults) private var mode = ColorMode.auto

    var body: some Scene {
        WindowGroup {
            RootView(model: model)
                .onOpenURL { model.handle($0) }
                .onReceive(NotificationCenter.default.publisher(for: Notification.Name("roomcall.openRoom"))) { _ in
                    model.consumePendingRoom()
                }
                .preferredColorScheme(demoScheme ?? mode.scheme)
                .task { applyDemoState() }
        }
    }

    private func applyDemoState() {
        #if DEBUG
        guard Demo.enabled else { return }
        switch Demo.value("-RoomCallDemoTab") {
        case "message": model.tab = .message
        case "tasks": model.tab = .tasks
        case "history": model.tab = .history
        case "settings": model.tab = .settings
        default: model.tab = .talk
        }
        if Demo.arguments.contains("-RoomCallDemoTalking") { model.talk.showDemoTalking() }
        #endif
    }

    /// Screenshot runs pick light or dark explicitly.
    private var demoScheme: ColorScheme? {
        #if DEBUG
        switch Demo.value("-RoomCallDemoAppearance") {
        case "dark": return .dark
        case "light": return .light
        default: return nil
        }
        #else
        return nil
        #endif
    }
}
