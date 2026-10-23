import Foundation

/// Everything the app does with RoomCall. The UI depends only on this, so screenshots and UI
/// tests can run against `DemoService` while the app itself uses Home Assistant.
protocol RoomCallService: Sendable {
    func overview() async throws -> Overview
    func history() async throws -> [CallSummary]
    func tasks(_ action: String, id: String?) async throws -> [RoomTask]
    func send(_ target: Target, message: String, urgent: Bool) async throws
    func satellites() async throws -> [Satellite]
    func ask(_ satellites: [String], question: String) async throws -> [String]
    func acknowledge(_ id: String) async throws
    func setDoNotDisturb(_ entity: String, on: Bool) async throws
    func clip(_ id: String) async throws -> Data
    func snapshot(_ camera: String) async throws -> Data
    /// When the doorbell last rang, if it is currently in a pressed (not quiet) state.
    func doorbellRang(_ entity: String) async throws -> Date?
    func talk(_ target: Target, urgent: Bool, from: String?) async throws -> any LiveTransport
}

/// A live talk session: 16 kHz mono PCM in, a delivery result out.
protocol LiveTransport: Sendable {
    func pcm(_ bytes: Data) async throws
    func finish() async throws -> [String: Any]
    func close() async
}

extension LiveConnection: LiveTransport {}

extension HAClient: RoomCallService {
    func overview() async throws -> Overview {
        try await decode(Overview.self, "api/roomcall/rooms")
    }
    func history() async throws -> [CallSummary] {
        struct Calls: Decodable { let calls: [CallSummary] }
        return try await decode(Calls.self, "api/roomcall/history").calls
    }
    func tasks(_ action: String, id: String?) async throws -> [RoomTask] {
        var request: [String: Any] = ["action": action]
        if let id { request["id"] = id }
        let result = try await command("tasks", request)
        let data = try JSONSerialization.data(withJSONObject: result["tasks"] ?? [])
        return try JSONDecoder().decode([RoomTask].self, from: data)
    }
    func send(_ target: Target, message: String, urgent: Bool) async throws {
        var request = target.request
        request["message"] = message
        request["priority"] = urgent ? "urgent" : "normal"
        request["idempotency_key"] = UUID().uuidString
        _ = try await command("send", request)
    }
    func satellites() async throws -> [Satellite] {
        let found = try await command("discover")["satellites"] as? [[String: Any]] ?? []
        return found.compactMap { item in
            guard item["can_ask"] as? Bool == true, item["available"] as? Bool == true,
                  let id = item["entity_id"] as? String else { return nil }
            return Satellite(entity_id: id, name: item["name"] as? String ?? id)
        }
    }
    func ask(_ satellites: [String], question: String) async throws -> [String] {
        let job = try await command("ask", ["satellites": satellites, "message": question,
                                            "idempotency_key": UUID().uuidString])
        guard let id = job["id"] as? String else { throw NativeFailure.protocolError }
        // Answers arrive when the satellite has listened and transcribed; poll the job.
        for _ in 0..<45 {
            let status = try await command("status", ["id": id])
            let state = status["state"] as? String ?? ""
            if ["answered", "expired", "failed", "cancelled"].contains(state) {
                let responses = status["responses"] as? [[String: Any]] ?? []
                return responses.compactMap { $0["sentence"] as? String }.filter { !$0.isEmpty }
            }
            try await Task.sleep(nanoseconds: 2_000_000_000)
        }
        return []
    }
    func acknowledge(_ id: String) async throws {
        _ = try await command("acknowledge", ["id": id])
    }
    func setDoNotDisturb(_ entity: String, on: Bool) async throws {
        _ = try await raw("api/services/switch/turn_\(on ? "on" : "off")", payload: ["entity_id": entity])
    }
    func clip(_ id: String) async throws -> Data {
        try await raw("api/roomcall/history/\(id).mp3")
    }
    func snapshot(_ camera: String) async throws -> Data {
        try await raw("api/camera_proxy/\(camera)")
    }
    func doorbellRang(_ entity: String) async throws -> Date? {
        struct State: Decodable { let state: String; let last_changed: String }
        let state = try await decode(State.self, "api/states/\(entity)")
        guard !["unavailable", "unknown", "off"].contains(state.state) else { return nil }
        return Timestamp.parse(state.last_changed)
    }
    func talk(_ target: Target, urgent: Bool, from: String?) async throws -> any LiveTransport {
        let connection = LiveConnection()
        try await connection.open(client: self, target: target, urgent: urgent, from: from)
        return connection
    }
}
