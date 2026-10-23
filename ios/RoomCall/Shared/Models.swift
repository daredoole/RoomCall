import Foundation
import Security

func L(_ key: String) -> String { NSLocalizedString(key, comment: "") }

enum NativeFailure: LocalizedError {
    case invalidServer, unauthorized, invalidCallback, unsupportedServer, protocolError
    case backend(String), keychain(OSStatus), backpressure

    var errorDescription: String? {
        switch self {
        case .invalidServer: return L("ios_invalid_server")
        case .unauthorized: return L("ios_sign_in_again")
        case .invalidCallback: return L("ios_invalid_callback")
        case .unsupportedServer: return L("ios_ha_version")
        case .protocolError: return L("ios_connection_failed")
        case .backend(let message): return message
        case .keychain: return L("ios_secure_storage_failed")
        case .backpressure: return L("ios_network_slow")
        }
    }
}

struct Credentials: Codable {
    let server: URL
    var accessToken: String
    let refreshToken: String
    var expiresAt: Date
    var clientID: String { server.appendingPathComponent("roomcall/ios").absoluteString }
}

enum SecureStore {
    static let account = "home-assistant"
    private static let lock = NSRecursiveLock()
    static func load(account: String = SecureStore.account) throws -> Credentials? {
        lock.lock()
        defer { lock.unlock() }
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.daredoole.RoomCall",
            kSecAttrAccount as String: account, kSecReturnData as String: true]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else {
            throw NativeFailure.keychain(status)
        }
        return try JSONDecoder().decode(Credentials.self, from: data)
    }
    static func save(_ credentials: Credentials, account: String = SecureStore.account) throws {
        lock.lock()
        defer { lock.unlock() }
        let data = try JSONEncoder().encode(credentials)
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.daredoole.RoomCall", kSecAttrAccount as String: account]
        let update = SecItemUpdate(query as CFDictionary,
                                  [kSecValueData as String: data] as CFDictionary)
        if update == errSecSuccess { return }
        guard update == errSecItemNotFound else { throw NativeFailure.keychain(update) }
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(item as CFDictionary, nil)
        guard status == errSecSuccess else { throw NativeFailure.keychain(status) }
    }
    static func saveRefreshed(_ credentials: Credentials, account: String = SecureStore.account) throws {
        lock.lock()
        defer { lock.unlock() }
        // A refresh must never recreate a signed-out account or replace a new login.
        guard let current = try load(account: account), current.server == credentials.server,
              current.refreshToken == credentials.refreshToken else { throw NativeFailure.unauthorized }
        try save(credentials, account: account)
    }
    static func delete(account: String = SecureStore.account) throws {
        lock.lock()
        defer { lock.unlock() }
        let status = SecItemDelete([kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.daredoole.RoomCall",
            kSecAttrAccount as String: account] as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw NativeFailure.keychain(status)
        }
    }
}

struct Room: Codable, Identifiable, Hashable {
    let area_id: String
    let name: String
    var id: String { area_id }
    static var everywhere: Room { Room(area_id: "all", name: L("ios_everywhere")) }
}

enum RoomCache {
    static var defaults: UserDefaults {
        UserDefaults(suiteName: "group.com.daredoole.RoomCall") ?? .standard
    }
    static func save(_ rooms: [Room]) {
        defaults.set(try? JSONEncoder().encode(rooms), forKey: "rooms")
    }
    static func load() -> [Room] {
        guard let data = defaults.data(forKey: "rooms") else { return [] }
        return (try? JSONDecoder().decode([Room].self, from: data)) ?? []
    }
    static func route(_ roomID: String?) -> URL {
        var parts = URLComponents(string: "roomcall://open")!
        parts.queryItems = roomID.map { [URLQueryItem(name: "room", value: $0)] }
        return parts.url!
    }
}

enum ServerAddress {
    static func parse(_ text: String) throws -> URL {
        guard var parts = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["https", "http"].contains(parts.scheme?.lowercased() ?? ""),
              let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil, let url = parts.url else {
            throw NativeFailure.invalidServer
        }
        parts.scheme = url.scheme?.lowercased()
        return parts.url!
    }
}

struct Person: Codable, Hashable, Identifiable {
    let entity_id: String
    let name: String
    var id: String { entity_id }
}

struct Doorbell: Codable, Hashable {
    let entity: String
    let camera: String?
    let name: String
}

/// `GET api/roomcall/rooms`: what the card and the app need to draw the intercom.
struct Overview: Codable {
    var rooms: [Room]?
    var people: [Person]?
    var configured: Bool?
    var dnd: Bool?
    var dnd_entity: String?
    var doorbell: Doorbell?
}

struct RoomTask: Codable, Hashable, Identifiable {
    let id: String
    let name: String
    let task: String
    let state: String
    let opened: String?
    let done: String?
    let done_by: String?
    var isOpen: Bool { state == "open" }
}

struct CallSummary: Codable, Hashable, Identifiable {
    let id: String
    let at: String
    let from: String?
    let target: String
    let kind: String
    let priority: String
    let ok: Bool
    let text: String?
    let has_audio: Bool
    let requires_ack: Bool?
}

struct Satellite: Hashable, Identifiable {
    let entity_id: String
    let name: String
    var id: String { entity_id }
}

/// Who a message goes to: a room (or everywhere) or a person, wherever they are.
enum Target: Hashable {
    case room(Room)
    case person(Person)

    var name: String {
        switch self {
        case .room(let room): room.name
        case .person(let person): person.name
        }
    }
    /// Request fields for `send` and `preview`.
    var request: [String: Any] {
        switch self {
        case .room(let room):
            if room.id == "all" { return ["everywhere": true] }
            return ["areas": [room.id]]
        case .person(let person):
            return ["people": [person.entity_id]]
        }
    }
}

/// HA timestamps carry microseconds, which ISO8601DateFormatter does not accept.
enum Timestamp {
    private static let formatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
    private static let plain = ISO8601DateFormatter()

    static func parse(_ value: String?) -> Date? {
        guard let value else { return nil }
        if let date = plain.date(from: value) { return date }
        let trimmed = value.replacingOccurrences(
            of: #"(\.\d{3})\d+"#, with: "$1", options: .regularExpression)
        return formatter.date(from: trimmed)
    }
}

/// Open tasks shared with the widget extension (empty there when re-signing removed the group).
enum TaskCache {
    static func save(_ tasks: [RoomTask]) {
        RoomCache.defaults.set(try? JSONEncoder().encode(tasks), forKey: "tasks")
    }
    static func load() -> [RoomTask] {
        guard let data = RoomCache.defaults.data(forKey: "tasks") else { return [] }
        return (try? JSONDecoder().decode([RoomTask].self, from: data)) ?? []
    }
}
