import ActivityKit
import AppIntents
import Foundation

/// An open announcement task on the Lock Screen and in the Dynamic Island.
struct TaskActivityAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var opened: Date?
    }
    let id: String
    let name: String
    let task: String
}

struct TaskEntity: AppEntity {
    static var typeDisplayRepresentation = TypeDisplayRepresentation(name: "ios_task")
    static var defaultQuery = TaskQuery()
    let id: String
    let name: String
    let task: String
    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "\(name)", subtitle: "\(task)")
    }
    init(id: String, name: String, task: String) {
        self.id = id
        self.name = name
        self.task = task
    }
    init(_ task: RoomTask) { self.init(id: task.id, name: task.name, task: task.task) }
}

struct TaskQuery: EntityStringQuery {
    func entities(for identifiers: [String]) async throws -> [TaskEntity] {
        TaskCache.load().filter { identifiers.contains($0.id) }.map { TaskEntity($0) }
    }
    func entities(matching string: String) async throws -> [TaskEntity] {
        TaskCache.load().filter {
            $0.name.localizedCaseInsensitiveContains(string) || $0.task.localizedCaseInsensitiveContains(string)
        }.map { TaskEntity($0) }
    }
    func suggestedEntities() async throws -> [TaskEntity] {
        TaskCache.load().filter(\.isOpen).map { TaskEntity($0) }
    }
}

/// Confirms a task was done, from Siri, Shortcuts or the Live Activity's Done button.
struct CompleteTaskIntent: LiveActivityIntent {
    static var title: LocalizedStringResource = "ios_mark_done"
    static var description = IntentDescription("ios_mark_done_description")
    @Parameter(title: "ios_task") var task: TaskEntity

    init() {}
    init(task: TaskEntity) { self.task = task }

    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard let credentials = try SecureStore.load() else { throw NativeFailure.unauthorized }
        _ = try await HAClient(credentials).command("tasks", ["action": "complete", "id": task.id])
        return .result(dialog: "ios_task_done")
    }
}
