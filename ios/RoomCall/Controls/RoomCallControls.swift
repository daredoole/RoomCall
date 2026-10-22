import AppIntents
import SwiftUI
import WidgetKit

@main
struct RoomCallControlBundle: WidgetBundle {
    var body: some Widget { RoomCallControl() }
}

struct RoomCallControl: ControlWidget {
    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: "com.daredoole.RoomCall.open") {
            ControlWidgetButton(action: OpenRoomCallIntent()) {
                Label("ios_open", systemImage: "mic.fill")
            }
        }
        .displayName("RoomCall")
        .description("ios_open_description")
    }
}
