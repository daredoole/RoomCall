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
        let client = HAClient(credentials)
        let targets: [String: Any] = room.id == "all" ? ["everywhere": true] : ["areas": [room.id]]
        _ = try await client.command("send", targets.merging([
            "message": message, "idempotency_key": UUID().uuidString
        ]) { _, new in new })
        return .result(dialog: "ios_queued")
    }
}
