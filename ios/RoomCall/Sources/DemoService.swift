#if DEBUG
import Foundation
import UIKit

/// Launch arguments used only by UI tests and documentation screenshots.
enum Demo {
    static let arguments = ProcessInfo.processInfo.arguments
    static var enabled: Bool { arguments.contains("-RoomCallDemo") }
    static func value(_ name: String) -> String? {
        guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else { return nil }
        return arguments[index + 1]
    }
    static var rooms: [Room] {
        [Room.everywhere] + [("basement", "Basement"), ("garage", "Garage"), ("kitchen", "Kitchen"),
                             ("living_room", "Living Room"), ("office", "Office")]
            .map { Room(area_id: $0.0, name: $0.1) }
    }
}

/// Sample household data: no server, no credentials, no network.
actor DemoService: RoomCallService {
    private var openTasks: [RoomTask] = [
        RoomTask(id: "laundry", name: "Laundry", task: "Took out the dry clothes", state: "open",
                 opened: ISO8601DateFormatter().string(from: Date().addingTimeInterval(-720)),
                 done: nil, done_by: nil),
        RoomTask(id: "dishwasher", name: "Dishwasher", task: "Emptied the dishwasher", state: "idle",
                 opened: nil, done: nil, done_by: nil),
    ]
    private var dnd = false

    func overview() async throws -> Overview {
        Overview(rooms: Array(Demo.rooms.dropFirst()),
                 people: [Person(entity_id: "person.alex", name: "Alex"),
                          Person(entity_id: "person.sam", name: "Sam")],
                 configured: true, dnd: dnd, dnd_entity: "switch.roomcall_do_not_disturb",
                 doorbell: Doorbell(entity: "binary_sensor.front_door_visitor",
                                    camera: "camera.front_door", name: "Front Door"))
    }
    func history() async throws -> [CallSummary] {
        let ago = { (minutes: Double) in ISO8601DateFormatter().string(from: Date().addingTimeInterval(-minutes * 60)) }
        return [
            CallSummary(id: "a1", at: ago(2), from: "kitchen", target: "basement", kind: "talk",
                        priority: "normal", ok: true, text: "Dinner's ready, come on up",
                        has_audio: true, requires_ack: nil),
            CallSummary(id: "a2", at: ago(25), from: "office", target: "all", kind: "talk",
                        priority: "urgent", ok: true, text: "Who left the garage open?",
                        has_audio: true, requires_ack: true),
            CallSummary(id: "a3", at: ago(70), from: nil, target: "living_room", kind: "message",
                        priority: "normal", ok: true, text: "Basement says: coming up in a minute",
                        has_audio: false, requires_ack: nil),
        ]
    }
    func tasks(_ action: String, id: String?) async throws -> [RoomTask] {
        if action == "complete", let index = openTasks.firstIndex(where: { $0.id == id }) {
            let task = openTasks[index]
            openTasks[index] = RoomTask(id: task.id, name: task.name, task: task.task, state: "done",
                                        opened: nil, done: ISO8601DateFormatter().string(from: Date()),
                                        done_by: "Alex")
        }
        return openTasks
    }
    func send(_ target: Target, message: String, urgent: Bool) async throws {}
    func satellites() async throws -> [Satellite] {
        [Satellite(entity_id: "assist_satellite.kitchen", name: "Kitchen Voice")]
    }
    func ask(_ satellites: [String], question: String) async throws -> [String] { ["Pizza, please"] }
    func acknowledge(_ id: String) async throws {}
    func setDoNotDisturb(_ entity: String, on: Bool) async throws { dnd = on }
    func clip(_ id: String) async throws -> Data { Data() }
    func snapshot(_ camera: String) async throws -> Data {
        await MainActor.run {
            let size = CGSize(width: 640, height: 360)
            return UIGraphicsImageRenderer(size: size).pngData { context in
                UIColor(red: 0.16, green: 0.2, blue: 0.27, alpha: 1).setFill()
                context.fill(CGRect(origin: .zero, size: size))
                UIColor(red: 0.85, green: 0.6, blue: 0.3, alpha: 1).setFill()
                context.cgContext.fillEllipse(in: CGRect(x: 270, y: 80, width: 100, height: 100))
                context.fill(CGRect(x: 250, y: 190, width: 140, height: 170))
            }
        }
    }
    func doorbellRang(_ entity: String) async throws -> Date? {
        Demo.arguments.contains("-RoomCallDemoDoorbell") ? Date() : nil
    }
    func talk(_ target: Target, urgent: Bool, from: String?) async throws -> any LiveTransport {
        DemoTransport()
    }
}

struct DemoTransport: LiveTransport {
    func pcm(_ bytes: Data) async throws {}
    func finish() async throws -> [String: Any] { ["ok": true] }
    func close() async {}
}
#endif
