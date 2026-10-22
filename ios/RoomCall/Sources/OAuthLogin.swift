import AuthenticationServices
import CryptoKit
import UIKit

@MainActor
final class OAuthLogin: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var browser: ASWebAuthenticationSession?

    static func challenge(_ verifier: String) -> String {
        Data(SHA256.hash(data: Data(verifier.utf8))).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
    static func code(from callback: URL, state: String) throws -> String {
        guard callback.scheme == "roomcall", callback.host == "auth-callback",
              let parts = URLComponents(url: callback, resolvingAgainstBaseURL: false) else {
            throw NativeFailure.invalidCallback
        }
        let items = parts.queryItems ?? []
        guard items.filter({ $0.name == "state" }).count == 1,
              items.first(where: { $0.name == "state" })?.value == state,
              items.filter({ $0.name == "code" }).count == 1,
              let code = items.first(where: { $0.name == "code" })?.value, !code.isEmpty else {
            throw NativeFailure.invalidCallback
        }
        return code
    }
    func signIn(server: URL) async throws -> Credentials {
        try await LiveConnection.checkVersion(server)
        let state = UUID().uuidString
        let verifier = UUID().uuidString + UUID().uuidString
        let clientID = server.appendingPathComponent("roomcall/ios").absoluteString
        var parts = URLComponents(url: server.appendingPathComponent("auth/authorize"), resolvingAgainstBaseURL: false)!
        parts.queryItems = [URLQueryItem(name: "client_id", value: clientID),
            URLQueryItem(name: "redirect_uri", value: "roomcall://auth-callback"),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "state", value: state),
            URLQueryItem(name: "code_challenge", value: Self.challenge(verifier)),
            URLQueryItem(name: "code_challenge_method", value: "S256")]
        let callback: URL = try await withCheckedThrowingContinuation { continuation in
            let browser = ASWebAuthenticationSession(url: parts.url!, callbackURLScheme: "roomcall") { url, error in
                if let error { continuation.resume(throwing: error) }
                else if let url { continuation.resume(returning: url) }
                else { continuation.resume(throwing: NativeFailure.invalidCallback) }
            }
            self.browser = browser
            browser.presentationContextProvider = self
            if !browser.start() { continuation.resume(throwing: NativeFailure.invalidCallback) }
        }
        defer { browser = nil }
        let code = try Self.code(from: callback, state: state)
        let result = try await HAClient.token(server: server, values: ["grant_type": "authorization_code",
            "code": code, "client_id": clientID, "code_verifier": verifier])
        guard let access = result["access_token"] as? String,
              let refresh = result["refresh_token"] as? String,
              let seconds = result["expires_in"] as? Double else { throw NativeFailure.protocolError }
        let credentials = Credentials(server: server, accessToken: access,
            refreshToken: refresh, expiresAt: Date().addingTimeInterval(seconds))
        try SecureStore.save(credentials)
        return credentials
    }
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows).first(where: \.isKeyWindow) ?? ASPresentationAnchor()
    }
}
