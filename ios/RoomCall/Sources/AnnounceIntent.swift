import AppIntents
import Foundation

struct AnnounceIntent: AppIntent {
    static var title: LocalizedStringResource = "ios_announce"
    static var description = IntentDescription("ios_announce_description")
    @Parameter(title: "ios_room") var room: RoomEntity
    @Parameter(title: "ios_message") var message: String
    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard let credentials = try SecureStore.load() else { throw NativeFailure.unauthorized }
        guard RoomCache.load().contains(where: { $0.id == room.id }) else { throw NativeFailure.protocolError }
        let target: Target = .room(Room(area_id: room.id, name: room.name))
        try await HAClient(credentials).send(target, message: message, urgent: false)
        return .result(dialog: "ios_queued")
    }
}

/// "Turn on RoomCall Do Not Disturb": only urgent calls play while it's on.
struct DoNotDisturbIntent: AppIntent {
    static var title: LocalizedStringResource = "ios_dnd_intent"
    static var description = IntentDescription("ios_dnd_description")
    @Parameter(title: "ios_dnd") var on: Bool
    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard let credentials = try SecureStore.load(),
              let entity = RoomCache.defaults.string(forKey: "dndEntity") else { throw NativeFailure.unauthorized }
        try await HAClient(credentials).setDoNotDisturb(entity, on: on)
        return .result(dialog: IntentDialog(on ? "ios_dnd_on" : "ios_dnd_off"))
    }
}
