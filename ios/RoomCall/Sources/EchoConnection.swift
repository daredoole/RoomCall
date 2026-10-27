import Foundation

struct EchoPeer: Decodable, Identifiable, Equatable {
    let speaker: String
    let name: String
    let available: Bool
    var id: String { speaker }
}

struct EchoInvitation: Equatable, Identifiable {
    let id: String
    let speaker: String
    let name: String
}

enum EchoEvent {
    case directory([EchoPeer], [EchoInvitation])
    case incoming(EchoInvitation), cancelled(String)
    case calling(String), ringing, accepted
    case audio(Data), ended(String)
}

/// One authenticated HA socket owns the station and its Echo call. Closing it
/// ends the live call, while the saved notification destination stays registered.
actor EchoConnection {
    private var socket: URLSessionWebSocketTask?
    private var reader: Task<Void, Never>?
    private var sequence = 1
    private var callID: String?

    func open(_ client: HAClient, station: String, room: String, name: String) async throws
        -> AsyncThrowingStream<EchoEvent, Error> {
        let (server, token) = try await client.authorization()
        let socket = URLSession.shared.webSocketTask(with: LiveConnection.websocketURL(server))
        self.socket = socket
        socket.resume()
        let directory: EchoEvent
        do {
            guard try LiveConnection.decode(await LiveConnection.read(socket))["type"] as? String == "auth_required"
            else { throw NativeFailure.protocolError }
            try await send(["type": "auth", "access_token": token])
            guard try LiveConnection.decode(await LiveConnection.read(socket))["type"] as? String == "auth_ok"
            else { throw NativeFailure.unauthorized }
            try await send(["id": 1, "type": "roomcall/echo_station", "station_id": station,
                            "room": room, "name": String(name.prefix(80))])
            let registered = try LiveConnection.decode(await LiveConnection.read(socket))
            try Self.check(registered)
            guard let event = try Self.event(registered) else { throw NativeFailure.protocolError }
            directory = event
        } catch { close(); throw error }
        return AsyncThrowingStream(bufferingPolicy: .bufferingOldest(128)) { continuation in
            continuation.yield(directory)
            reader = Task {
                do {
                    while !Task.isCancelled {
                        let message = try LiveConnection.decode(await socket.receive())
                        try Self.check(message)
                        if let event = try Self.event(message) {
                            if case .calling(let id) = event { callID = id }
                            if case .dropped = continuation.yield(event) { throw NativeFailure.backpressure }
                        }
                    }
                    continuation.finish()
                } catch { continuation.finish(throwing: error) }
            }
            continuation.onTermination = { _ in Task { await self.close() } }
        }
    }

    static func check(_ message: [String: Any]) throws {
        if message["success"] as? Bool == false {
            let error = message["error"] as? [String: Any]
            throw NativeFailure.backend(error?["message"] as? String ?? L("ios_connection_failed"))
        }
    }

    static func invitation(_ event: [String: Any]) -> EchoInvitation? {
        guard let id = event["id"] as? String, let speaker = event["speaker"] as? String,
              let name = event["from"] as? String else { return nil }
        return EchoInvitation(id: id, speaker: speaker, name: name)
    }

    static func event(_ message: [String: Any]) throws -> EchoEvent? {
        if message["id"] as? Int == 1, let result = message["result"] as? [String: Any] {
            let bytes = try JSONSerialization.data(withJSONObject: result["echoes"] ?? [])
            let peers = try JSONDecoder().decode([EchoPeer].self, from: bytes)
            let invites = (result["invitations"] as? [[String: Any]] ?? []).compactMap(invitation)
            return .directory(peers, invites)
        }
        guard let event = message["event"] as? [String: Any] else { return nil }
        switch event["type"] as? String {
        case "echo_incoming":
            guard let incoming = invitation(event) else { throw NativeFailure.protocolError }
            return .incoming(incoming)
        case "echo_cancelled": return .cancelled(event["id"] as? String ?? "")
        case "calling":
            guard let id = event["id"] as? String else { throw NativeFailure.protocolError }
            return .calling(id)
        case "ringing": return .ringing
        case "accepted": return .accepted
        case "audio":
            guard let encoded = event["pcm"] as? String, let pcm = Data(base64Encoded: encoded),
                  [640, 1280].contains(pcm.count) else { throw NativeFailure.protocolError }
            return .audio(pcm)
        case "ended": return .ended(event["reason"] as? String ?? "ended")
        default: return nil
        }
    }

    private func send(_ object: [String: Any]) async throws {
        guard let socket else { throw NativeFailure.protocolError }
        let data = try JSONSerialization.data(withJSONObject: object)
        try await socket.send(.string(String(decoding: data, as: UTF8.self)))
    }
    private func command(_ type: String, _ values: [String: Any]) async throws {
        sequence += 1
        try await send(values.merging(["id": sequence, "type": "roomcall/" + type]) { _, new in new })
    }
    func call(_ speaker: String, invitation: String?) async throws {
        var values: [String: Any] = ["speaker": speaker]
        if let invitation { values["invitation"] = invitation }
        try await command("echo_call", values)
    }
    func pcm(_ bytes: Data) async throws {
        guard let callID, bytes.count == 640 else { throw NativeFailure.protocolError }
        try await command("echo_audio", ["call_id": callID, "pcm": bytes.base64EncodedString()])
    }
    func hangup() async {
        if let callID { try? await command("echo_audio", ["call_id": callID, "hangup": true]) }
        callID = nil
    }
    func decline(_ invitation: String) async throws {
        try await command("echo_decline", ["invitation": invitation])
    }
    func close() {
        reader?.cancel()
        reader = nil
        socket?.cancel(with: .normalClosure, reason: nil)
        socket = nil
        callID = nil
    }
}
