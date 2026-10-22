import Foundation

actor HAClient {
    private var credentials: Credentials
    private let session: URLSession
    init(_ credentials: Credentials, session: URLSession = .shared) {
        self.credentials = credentials
        self.session = session
    }
    func authorization() async throws -> (URL, String) {
        if credentials.expiresAt.timeIntervalSinceNow < 60 {
            let data = try await Self.token(server: credentials.server,
                values: ["grant_type": "refresh_token", "refresh_token": credentials.refreshToken,
                         "client_id": credentials.clientID], session: session)
            guard let token = data["access_token"] as? String,
                  let seconds = data["expires_in"] as? Double else { throw NativeFailure.protocolError }
            credentials.accessToken = token
            credentials.expiresAt = Date().addingTimeInterval(seconds)
            try SecureStore.save(credentials)
        }
        return (credentials.server, credentials.accessToken)
    }
    func get(_ path: String) async throws -> [String: Any] {
        try await request(path, payload: nil)
    }
    func command(_ operation: String, _ values: [String: Any] = [:]) async throws -> [String: Any] {
        let result = try await request("api/roomcall/command",
                                      payload: ["operation": operation, "request": values])
        guard result["ok"] as? Bool == true else {
            let error = result["error"] as? [String: Any]
            throw NativeFailure.backend(error?["message"] as? String ?? L("ios_connection_failed"))
        }
        return result
    }
    private func request(_ path: String, payload: [String: Any]?) async throws -> [String: Any] {
        let (server, token) = try await authorization()
        var request = URLRequest(url: server.appendingPathComponent(path))
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let payload {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        }
        let (data, response) = try await session.data(for: request)
        try Self.check(response)
        guard let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw NativeFailure.protocolError
        }
        return result
    }
    static func token(server: URL, values: [String: String], session: URLSession = .shared) async throws -> [String: Any] {
        var request = URLRequest(url: server.appendingPathComponent("auth/token"))
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        var parts = URLComponents()
        parts.queryItems = values.sorted(by: { $0.key < $1.key }).map { URLQueryItem(name: $0.key, value: $0.value) }
        request.httpBody = parts.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B").data(using: .utf8)
        let (data, response) = try await session.data(for: request)
        try check(response)
        guard let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw NativeFailure.protocolError
        }
        return result
    }
    static func check(_ response: URLResponse) throws {
        guard let response = response as? HTTPURLResponse else { throw NativeFailure.protocolError }
        if response.statusCode == 401 || response.statusCode == 403 { throw NativeFailure.unauthorized }
        guard (200..<300).contains(response.statusCode) else { throw NativeFailure.protocolError }
    }
}

actor LiveConnection {
    private var socket: URLSessionWebSocketTask?
    private var handler: UInt8?
    private let session: URLSession
    init(session: URLSession = .shared) { self.session = session }

    static func websocketURL(_ server: URL) -> URL {
        var parts = URLComponents(url: server.appendingPathComponent("api/websocket"), resolvingAgainstBaseURL: false)!
        parts.scheme = parts.scheme == "https" ? "wss" : "ws"
        return parts.url!
    }
    static func supportsPKCE(_ hello: [String: Any]) -> Bool {
        let version = (hello["ha_version"] as? String ?? "").split(separator: ".").prefix(2).compactMap { Int($0) }
        return version.count == 2 && (version[0] > 2026 || (version[0] == 2026 && version[1] >= 10))
    }
    static func checkVersion(_ server: URL) async throws {
        let task = URLSession.shared.webSocketTask(with: websocketURL(server))
        task.resume()
        defer { task.cancel(with: .normalClosure, reason: nil) }
        let hello = try await decode(task.receive())
        guard supportsPKCE(hello) else { throw NativeFailure.unsupportedServer }
    }
    func open(client: HAClient, room: Room) async throws {
        let (server, token) = try await client.authorization()
        let task = session.webSocketTask(with: Self.websocketURL(server))
        socket = task
        task.resume()
        do {
            let hello = try await Self.decode(task.receive())
            guard hello["type"] as? String == "auth_required" else { throw NativeFailure.protocolError }
            try await send(["type": "auth", "access_token": token])
            let authenticated = try await Self.decode(task.receive())
            guard authenticated["type"] as? String == "auth_ok" else { throw NativeFailure.unauthorized }
            try await send(["id": 1, "type": "roomcall/live", "target": room.id, "from": "RoomCall iOS"])
            for _ in 0..<8 {
                let message = try await Self.decode(task.receive())
                if message["success"] as? Bool == false {
                    let error = message["error"] as? [String: Any]
                    throw NativeFailure.backend(error?["message"] as? String ?? L("ios_connection_failed"))
                }
                if let event = message["event"] as? [String: Any], event["type"] as? String == "ready",
                   let value = event["handler_id"] as? Int, let id = UInt8(exactly: value) {
                    handler = id
                    return
                }
            }
            throw NativeFailure.protocolError
        } catch {
            close()
            throw error
        }
    }
    private func send(_ object: [String: Any]) async throws {
        guard let socket else { throw NativeFailure.protocolError }
        let data = try JSONSerialization.data(withJSONObject: object)
        try await socket.send(.string(String(decoding: data, as: UTF8.self)))
    }
    static func decode(_ message: @autoclosure () async throws -> URLSessionWebSocketTask.Message) async throws -> [String: Any] {
        let data: Data
        switch try await message() {
        case .data(let bytes): data = bytes
        case .string(let value): data = Data(value.utf8)
        @unknown default: throw NativeFailure.protocolError
        }
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw NativeFailure.protocolError
        }
        return object
    }
    func pcm(_ bytes: Data) async throws {
        guard let socket, let handler else { throw NativeFailure.protocolError }
        try await socket.send(.data(Data([handler]) + bytes))
    }
    func finish() async throws -> [String: Any] {
        guard let socket, let handler else { throw NativeFailure.protocolError }
        try await socket.send(.data(Data([handler])))
        for _ in 0..<16 {
            let message = try await Self.decode(socket.receive())
            if let event = message["event"] as? [String: Any] {
                if event["type"] as? String == "error" {
                    throw NativeFailure.backend(event["message"] as? String ?? L("ios_connection_failed"))
                }
                if event["type"] as? String == "done" { close(); return event }
            }
        }
        throw NativeFailure.protocolError
    }
    func close() {
        socket?.cancel(with: .normalClosure, reason: nil)
        socket = nil
        handler = nil
    }
}
