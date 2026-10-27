import SwiftUI

struct EchoCallBanner: View {
    @ObservedObject var echo: EchoController
    @ObservedObject var talk: TalkController
    let reconnect: () async -> Void

    var body: some View {
        if let incoming = echo.incoming {
            VStack(spacing: 8) {
                Label("ios_incoming_call", systemImage: "phone.arrow.down.left.fill").font(.headline)
                Text(verbatim: incoming.name)
                HStack {
                    Button("ios_decline", role: .destructive) { Task { await echo.decline() } }
                        .accessibilityIdentifier("echo-decline")
                    Spacer()
                    Button("ios_answer") { Task { await echo.answer() } }
                        .buttonStyle(.borderedProminent).disabled(talk.active)
                        .accessibilityIdentifier("echo-answer")
                }
            }
            .padding().background(.regularMaterial)
        } else if echo.active {
            HStack {
                Text(verbatim: echo.status)
                Spacer()
                Button("ios_hang_up", role: .destructive) {
                    Task { await echo.hangup(); await reconnect() }
                }
                .accessibilityIdentifier("echo-hangup")
            }
            .padding().background(.regularMaterial)
        }
    }
}

struct EchoCallPanel: View {
    @ObservedObject var echo: EchoController
    @ObservedObject var talk: TalkController
    @State private var selected = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("ios_live_call", systemImage: "phone.fill").font(.headline)
            if !echo.peers.isEmpty {
                Picker("ios_echo", selection: $selected) {
                    Text("ios_choose_echo").tag("")
                    ForEach(echo.peers) { peer in
                        Text(verbatim: peer.name).tag(peer.speaker)
                    }
                }
                Button("ios_call_echo") { Task { await echo.call(selected) } }
                    .buttonStyle(.borderedProminent)
                    .disabled(!echo.listening || echo.active || talk.active ||
                              !echo.peers.contains { $0.speaker == selected && $0.available })
                    .accessibilityIdentifier("echo-call")
            }
            Text("ios_live_call_hint").font(.footnote).foregroundStyle(.secondary)
            if !echo.status.isEmpty {
                Text(verbatim: echo.status).font(.callout)
                    .accessibilityIdentifier("echo-status")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding().background(.thinMaterial, in: RoundedRectangle(cornerRadius: 16))
    }
}
