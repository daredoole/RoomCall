import AppIntents
import Foundation

struct RoomEntity: AppEntity {
    static var typeDisplayRepresentation = TypeDisplayRepresentation(name: "ios_room")
    static var defaultQuery = RoomQuery()
    let id: String
    let name: String
    var displayRepresentation: DisplayRepresentation { DisplayRepresentation(title: "\(name)") }
}

struct RoomQuery: EntityStringQuery {
    func entities(for identifiers: [String]) async throws -> [RoomEntity] {
        RoomCache.load().filter { identifiers.contains($0.id) }.map { RoomEntity(id: $0.id, name: $0.name) }
    }
    func entities(matching string: String) async throws -> [RoomEntity] {
        RoomCache.load().filter { $0.name.localizedCaseInsensitiveContains(string) }.map { RoomEntity(id: $0.id, name: $0.name) }
    }
    func suggestedEntities() async throws -> [RoomEntity] {
        RoomCache.load().map { RoomEntity(id: $0.id, name: $0.name) }
    }
}

struct OpenRoomCallIntent: AppIntent {
    static var title: LocalizedStringResource = "ios_open"
    static var description = IntentDescription("ios_open_description")
    @Parameter(title: "ios_room") var room: RoomEntity?
    func perform() async throws -> some IntentResult & OpensIntent {
        .result(opensIntent: OpenURLIntent(RoomCache.route(room?.id)))
    }
}
