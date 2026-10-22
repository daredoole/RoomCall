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
    static func load(account: String = SecureStore.account) throws -> Credentials? {
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
    static func delete(account: String = SecureStore.account) throws {
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
