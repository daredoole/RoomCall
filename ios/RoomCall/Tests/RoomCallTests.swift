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
