import AVFoundation
import XCTest
@testable import RoomCall

final class RoomCallTests: XCTestCase {
    func testServerAddressAndPaths() throws {
        let server = try ServerAddress.parse(" https://ha.example/ha ")
        XCTAssertEqual(LiveConnection.websocketURL(server).absoluteString, "wss://ha.example/ha/api/websocket")
        XCTAssertEqual(try ServerAddress.parse("http://ha.local:8123").host, "ha.local")
        XCTAssertEqual(LiveConnection.websocketURL(try ServerAddress.parse("HTTPS://ha.example")).scheme, "wss")
        for invalid in ["", "http://", "file:///etc/passwd", "https://user:password@ha.local", "https://ha.local?token=secret", "https://ha.local/#secret"] {
            XCTAssertThrowsError(try ServerAddress.parse(invalid))
        }
        XCTAssertFalse(LiveConnection.supportsPKCE(["ha_version": "2026.2.3"]))
        XCTAssertTrue(LiveConnection.supportsPKCE(["ha_version": "2026.10.2"]))
        XCTAssertFalse(LiveConnection.supportsPKCE([:]))
    }
    @MainActor
    func testOAuthStateAndPKCE() async throws {
        XCTAssertEqual(OAuthLogin.challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
                       "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
        XCTAssertEqual(try OAuthLogin.code(from: URL(string: "roomcall://auth-callback?state=expected&code=code")!, state: "expected"), "code")
        for value in ["roomcall://auth-callback?state=wrong&code=code", "roomcall://auth-callback?state=expected", "roomcall://auth-callback?state=expected&code=one&code=two", "https://auth-callback?state=expected&code=code"] {
            XCTAssertThrowsError(try OAuthLogin.code(from: URL(string: value)!, state: "expected"))
        }
    }
    @MainActor
    func testPublicOAuthClientKeepsAuthorizationOnSelectedServer() throws {
        let server = URL(string: "https://ha.example.test/ha")!
        let url = OAuthLogin.authorizationURL(server: server, state: "state", challenge: "challenge")
        XCTAssertEqual(url.host, server.host)
        XCTAssertEqual(url.path, "/ha/auth/authorize")
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!
        let values = Dictionary(uniqueKeysWithValues: items.map { ($0.name, $0.value ?? "") })
        XCTAssertEqual(values["client_id"], "https://daredoole.github.io/RoomCall/ios-client.html")
        XCTAssertEqual(values["redirect_uri"], "roomcall://auth-callback")
        XCTAssertEqual(values["code_challenge_method"], "S256")
        XCTAssertEqual(values["code_challenge"], "challenge")
        XCTAssertEqual(values["state"], "state")
    }
    func testCredentialClientIdentitySurvivesUpgradeAndRefresh() async throws {
        let server = URL(string: "https://ha.example.test")!
        let old = Credentials(server: server, accessToken: "old", refreshToken: "old-refresh", expiresAt: .distantPast)
        // The original encoding omits this optional key entirely.
        let legacy = try JSONDecoder().decode(Credentials.self, from: JSONEncoder().encode(old))
        XCTAssertNil(legacy.oauthClientID)
        XCTAssertEqual(legacy.clientID, "https://ha.example.test/roomcall/ios")
        let modern = Credentials(server: server, accessToken: "new", refreshToken: "new-refresh",
                                 expiresAt: .distantPast, oauthClientID: OAuthClient.id)
        for credentials in [legacy, modern] {
            let account = "test-client-" + UUID().uuidString
            defer { try? SecureStore.delete(account: account) }
            try SecureStore.save(credentials, account: account)
            let config = URLSessionConfiguration.ephemeral
            config.protocolClasses = [StubProtocol.self]
            StubProtocol.handler = { request in
                XCTAssertEqual(request.url?.host, server.host)
                XCTAssertEqual(request.url?.path, "/auth/token")
                let body = request.httpBody ?? request.httpBodyStream.map(Self.read) ?? Data()
                let parts = URLComponents(string: "?" + String(decoding: body, as: UTF8.self))!
                XCTAssertEqual(parts.queryItems?.first { $0.name == "client_id" }?.value, credentials.clientID)
                return (200, Data(#"{"access_token":"renewed","expires_in":3600}"#.utf8))
            }
            let client = HAClient(credentials, session: URLSession(configuration: config), keychainAccount: account)
            let (_, token) = try await client.authorization()
            XCTAssertEqual(token, "renewed")
            XCTAssertEqual(try SecureStore.load(account: account)?.clientID, credentials.clientID)
        }
    }
    func testLiveReceiptFinishesBeforeTranscriptionAndPreservesEvidence() async throws {
        var receives = 0
        let result = try await LiveConnection.deliveryResult { timeout in
            XCTAssertGreaterThan(timeout, 30) // Queued delivery has its own budget.
            receives += 1
            if receives == 1 { return ["type": "result", "success": true] }
            XCTAssertEqual(receives, 2, "Must not wait for the later done/transcript event")
            return ["event": ["type": "delivered", "id": "call", "ok": true,
                              "audible": NSNull(), "evidence": ["speaker": ["request_accepted": true]]]]
        }
        XCTAssertEqual(receives, 2)
        XCTAssertEqual(result["type"] as? String, "delivered")
        XCTAssertEqual(result["ok"] as? Bool, true)
        XCTAssertTrue(result["audible"] is NSNull)
        XCTAssertNotNil(result["evidence"])
    }
    func testLiveReceiptPreservesPartialFailureAndLegacyDone() async throws {
        for kind in ["delivered", "done"] {
            let result = try await LiveConnection.deliveryResult { _ in
                ["event": ["type": kind, "ok": false, "results": [
                    ["speaker": "Fast", "ok": true], ["speaker": "Slow", "ok": false]
                ]]]
            }
            XCTAssertEqual(result["ok"] as? Bool, false)
            XCTAssertEqual((result["results"] as? [[String: Any]])?.count, 2)
        }
    }
    func testLiveReceiptKeepsKnownErrorsSeparateFromLostConfirmation() async throws {
        for message in [["event": ["type": "error", "message": "Blocked"]],
                        ["success": false, "error": ["message": "Blocked"]]] as [[String: Any]] {
            do {
                _ = try await LiveConnection.deliveryResult { _ in message }
                XCTFail("Server rejection must remain a failure")
            } catch { XCTAssertEqual(error.localizedDescription, "Blocked") }
        }
        do {
            _ = try await LiveConnection.deliveryResult { _ in throw URLError(.timedOut) }
            XCTFail("Lost acknowledgment must remain unknown")
        } catch { XCTAssertEqual(error.localizedDescription, NativeFailure.deliveryUnknown.localizedDescription) }
        do {
            _ = try await LiveConnection.deliveryResult { _ in ["event": ["type": "delivered"]] }
            XCTFail("Malformed receipt must not imply failure or success")
        } catch { XCTAssertEqual(error.localizedDescription, NativeFailure.deliveryUnknown.localizedDescription) }
    }
    func testLiveReceiptBoundsUnrelatedMessages() async throws {
        var receives = 0
        do {
            _ = try await LiveConnection.deliveryResult { _ in
                receives += 1
                return ["type": "unrelated"]
            }
            XCTFail("Missing receipt must remain unknown")
        } catch { XCTAssertEqual(error.localizedDescription, NativeFailure.deliveryUnknown.localizedDescription) }
        XCTAssertEqual(receives, 16)
    }
    func testNativePCMConversionUsesServerFormat() throws {
        let input = AVAudioFormat(standardFormatWithSampleRate: 48_000, channels: 1)!
        let converter = try AudioCapture.converter(from: input)
        let buffer = AVAudioPCMBuffer(pcmFormat: input, frameCapacity: 1920)!
        buffer.frameLength = 1920
        for index in 0..<1920 { buffer.floatChannelData![0][index] = 0.25 }
        let pcm = try AudioCapture.pcm(buffer, converter: converter)
        XCTAssertEqual(converter.outputFormat.sampleRate, 16_000)
        XCTAssertEqual(converter.outputFormat.channelCount, 1)
        XCTAssertEqual(converter.outputFormat.commonFormat, .pcmFormatInt16)
        XCTAssertGreaterThan(pcm.count, 1000)
        XCTAssertEqual(pcm.count % 2, 0)
        XCTAssertNotEqual(Set(pcm), [0])
    }
    func testKeychainRoundTripAndRemoval() throws {
        let account = "test-" + UUID().uuidString
        try SecureStore.delete(account: account)
        defer { try? SecureStore.delete(account: account) }
        XCTAssertNil(try SecureStore.load(account: account))
        let credentials = Credentials(server: URL(string: "https://ha.example")!,
            accessToken: "test-access", refreshToken: "test-refresh", expiresAt: .distantFuture)
        try SecureStore.save(credentials, account: account)
        XCTAssertEqual(try SecureStore.load(account: account)?.refreshToken, "test-refresh")
        XCTAssertEqual(try SecureStore.load(account: account)?.clientID, "https://ha.example/roomcall/ios")
        try SecureStore.save(credentials, account: account)
        try SecureStore.delete(account: account)
        XCTAssertNil(try SecureStore.load(account: account))
    }
    func testLateRefreshCannotRestoreSignOutOrReplaceNewLogin() async throws {
        let account = "test-" + UUID().uuidString
        defer { try? SecureStore.delete(account: account) }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        let session = URLSession(configuration: config)
        let expired = Credentials(server: URL(string: "https://ha.example")!,
            accessToken: "expired", refreshToken: "original-login", expiresAt: .distantPast)
        let response = Data("{\"access_token\":\"renewed\",\"expires_in\":3600}".utf8)
        try SecureStore.save(expired, account: account)
        StubProtocol.handler = { _ in (200, response) }
        let normal = HAClient(expired, session: session, keychainAccount: account)
        let (_, token) = try await normal.authorization()
        XCTAssertEqual(token, "renewed")
        XCTAssertEqual(try SecureStore.load(account: account)?.accessToken, "renewed")

        // Complete the refresh only after sign-out removes its credentials.
        StubProtocol.handler = { _ in
            try SecureStore.delete(account: account)
            return (200, response)
        }
        let signedOut = HAClient(expired, session: session, keychainAccount: account)
        for _ in 0..<2 {
            do { _ = try await signedOut.authorization(); XCTFail("Signed-out refresh succeeded") }
            catch { XCTAssertEqual(error.localizedDescription, NativeFailure.unauthorized.localizedDescription) }
        }
        XCTAssertNil(try SecureStore.load(account: account))

        // Another login may complete while an older request is still in flight.
        let replacement = Credentials(server: expired.server, accessToken: "new-login-access",
            refreshToken: "new-login-refresh", expiresAt: .distantFuture)
        StubProtocol.handler = { _ in
            try SecureStore.save(replacement, account: account)
            return (200, response)
        }
        let previous = HAClient(expired, session: session, keychainAccount: account)
        do { _ = try await previous.authorization(); XCTFail("Superseded refresh succeeded") }
        catch { XCTAssertEqual(error.localizedDescription, NativeFailure.unauthorized.localizedDescription) }
        XCTAssertEqual(try SecureStore.load(account: account)?.refreshToken, "new-login-refresh")
        XCTAssertEqual(try SecureStore.load(account: account)?.accessToken, "new-login-access")
    }
    func testRoomIntentQueriesAndEscapedRoutes() async throws {
        defer { RoomCache.save([]) }
        RoomCache.save([Room(area_id: "kitchen", name: "Kitchen"), Room(area_id: "office", name: "Office")])
        let query = RoomQuery()
        let found = try await query.entities(matching: "KITCH")
        XCTAssertEqual(found.map(\.id), ["kitchen"])
        let selected = try await query.entities(for: ["office"])
        XCTAssertEqual(selected.map(\.name), ["Office"])
        let parts = URLComponents(url: RoomCache.route("kitchen&room=all"), resolvingAgainstBaseURL: false)!
        XCTAssertEqual(parts.queryItems?.count, 1)
        XCTAssertEqual(parts.queryItems?.first?.value, "kitchen&room=all")
    }
    func testCommandRequestsKeepTokensOutOfURLsAndPreserveErrors() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        let client = HAClient(Credentials(server: URL(string: "https://ha.example")!, accessToken: "secret",
            refreshToken: "refresh", expiresAt: .distantFuture), session: URLSession(configuration: config))
        StubProtocol.handler = { request in
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer secret")
            XCTAssertNil(request.url?.query)
            XCTAssertEqual(request.url?.path, "/api/roomcall/command")
            return (200, Data("{\"ok\":false,\"error\":{\"code\":\"dnd_blocked\",\"message\":\"Blocked\"}}".utf8))
        }
        do { _ = try await client.command("send", ["areas": ["kitchen"], "message": "Hello"]); XCTFail() }
        catch { XCTAssertEqual(error.localizedDescription, "Blocked") }
        StubProtocol.handler = { _ in (401, Data()) }
        do { _ = try await client.get("api/roomcall/rooms"); XCTFail() }
        catch { XCTAssertEqual(error.localizedDescription, NativeFailure.unauthorized.localizedDescription) }
    }
    func testTimestampsWithMicrosecondsAndTargets() throws {
        XCTAssertNotNil(Timestamp.parse("2026-10-22T15:41:01.511431+00:00"))
        XCTAssertNotNil(Timestamp.parse("2026-10-22T15:41:01+00:00"))
        XCTAssertNil(Timestamp.parse("yesterday"))
        XCTAssertNil(Timestamp.parse(nil))
        XCTAssertEqual(Target.room(.everywhere).request["everywhere"] as? Bool, true)
        XCTAssertEqual(Target.room(Room(area_id: "kitchen", name: "Kitchen")).request["areas"] as? [String], ["kitchen"])
        XCTAssertEqual(Target.person(Person(entity_id: "person.alex", name: "Alex")).request["people"] as? [String],
                       ["person.alex"])
    }
    func testServiceDecodesOverviewTasksHistoryAndDoorbell() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        let client = HAClient(Credentials(server: URL(string: "https://ha.example")!, accessToken: "secret",
            refreshToken: "refresh", expiresAt: .distantFuture), session: URLSession(configuration: config))
        var sent: [String: Any] = [:]
        StubProtocol.handler = { request in
            switch request.url?.path {
            case "/api/roomcall/rooms":
                return (200, Data(#"{"rooms":[{"area_id":"kitchen","name":"Kitchen","speakers":["a"]}],"people":[{"entity_id":"person.alex","name":"Alex"}],"dnd":false,"dnd_entity":"switch.dnd","doorbell":{"entity":"binary_sensor.door","camera":"camera.door","name":"Front Door"}}"#.utf8))
            case "/api/roomcall/history":
                return (200, Data(#"{"calls":[{"id":"c1","at":"2026-10-22T15:41:01.511431+00:00","from":"kitchen","target":"basement","kind":"talk","priority":"normal","ok":true,"text":"Hi","has_audio":true,"requires_ack":false}]}"#.utf8))
            case "/api/roomcall/command":
                let body = try JSONSerialization.jsonObject(with: request.httpBodyStream.map(Self.read) ?? Data()) as? [String: Any] ?? [:]
                sent = body
                return (200, Data(#"{"ok":true,"tasks":[{"id":"t1","name":"Laundry","task":"Took out the dry clothes","state":"open","opened":"2026-10-22T15:41:01+00:00","done":null,"done_by":null,"remind_every":15,"entity_id":"sensor.t1"}]}"#.utf8))
            case "/api/states/binary_sensor.door":
                let now = ISO8601DateFormatter().string(from: Date())
                return (200, Data("{\"state\":\"on\",\"last_changed\":\"\(now)\"}".utf8))
            default:
                return (404, Data())
            }
        }
        let overview = try await client.overview()
        XCTAssertEqual(overview.rooms?.first?.name, "Kitchen")
        XCTAssertEqual(overview.doorbell?.name, "Front Door")
        let history = try await client.history()
        XCTAssertEqual(history.first?.text, "Hi")
        let tasks = try await client.tasks("complete", id: "t1")
        XCTAssertEqual(tasks.first?.isOpen, true)
        XCTAssertEqual(sent["operation"] as? String, "tasks")
        XCTAssertEqual((sent["request"] as? [String: Any])?["id"] as? String, "t1")
        try await client.send(.room(Room(area_id: "kitchen", name: "Kitchen")), message: "Dinner", urgent: true)
        let request = sent["request"] as? [String: Any]
        XCTAssertEqual(request?["areas"] as? [String], ["kitchen"])
        XCTAssertEqual(request?["priority"] as? String, "urgent")
        XCTAssertNotNil(request?["idempotency_key"])
        let rang = try await client.doorbellRang("binary_sensor.door")
        XCTAssertNotNil(rang)
    }
    private static func read(_ stream: InputStream) -> Data {
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return data
    }

    func testThemeColoursMatchTheDashboardCard() {
        let blue = RGB(hex: "#0a84ff")!
        XCTAssertEqual(blue.hex, "#0A84FF")
        XCTAssertNil(RGB(hex: "blue"))
        XCTAssertEqual(RGB(blue.color).hex, "#0A84FF")
        // Light colours get dark text; saturated ones keep white.
        XCTAssertEqual(RGB(hex: "#FFD60A")!.onColor.hex, "#101317")
        XCTAssertEqual(blue.onColor.hex, "#FFFFFF")
        // HSL round trip.
        let (h, s, l) = blue.hsl
        XCTAssertEqual(RGB.fromHSL(h, s, l).hex, blue.hex)
        XCTAssertEqual(ThemeSettings.presets.map(\.hex).first, ThemeSettings.defaultAccent)
    }

    func testMaterialTonesComeFromTheSeed() {
        let teal = RGB(hex: "#00A389")!, purple = RGB(hex: "#7C4DFF")!
        XCTAssertNotEqual(Tones(seed: teal, dark: false), Tones(seed: purple, dark: false))
        let dark = Tones(seed: teal, dark: true), light = Tones(seed: teal, dark: false)
        // Dark mode uses a lighter primary on a darker surface, like Material 3.
        XCTAssertGreaterThan(dark.primary.hsl.l, 0.65)
        XCTAssertLessThan(light.primary.hsl.l, 0.45)
        XCTAssertLessThan(dark.surface.luminance, light.surface.luminance)
        XCTAssertLessThan(dark.onPrimary.luminance, dark.primary.luminance)
        XCTAssertEqual(ColorMode.auto.scheme, nil)
        XCTAssertEqual(ColorMode.dark.scheme, .dark)
        XCTAssertEqual(AppStyle(rawValue: "material"), .material)
    }
}

final class StubProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (Int, Data))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let (status, data) = try Self.handler!(request)
            client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}
