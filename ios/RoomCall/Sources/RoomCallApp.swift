import SwiftUI

@main
struct RoomCallApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView(model: model)
                .onOpenURL { model.handle($0) }
                .onReceive(NotificationCenter.default.publisher(for: Notification.Name("roomcall.openRoom"))) { _ in
                    model.consumePendingRoom()
                }
                .demoAppearance()
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
}

extension View {
    @ViewBuilder func demoAppearance() -> some View {
        #if DEBUG
        switch Demo.value("-RoomCallDemoAppearance") {
        case "dark": preferredColorScheme(.dark)
        case "light": preferredColorScheme(.light)
        default: self
        }
        #else
        self
        #endif
    }
}
