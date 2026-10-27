import AVFoundation
import Foundation

@MainActor
final class EchoController: ObservableObject {
    enum Phase: Equatable { case idle, connecting, ringing, live }
    @Published private(set) var peers: [EchoPeer] = []
    @Published private(set) var incoming: EchoInvitation?
    @Published private(set) var phase = Phase.idle
    @Published private(set) var status = ""
    @Published private(set) var listening = false
    let stationID: String = {
        if let saved = UserDefaults.standard.string(forKey: "echoStationID") { return saved }
        let id = UUID().uuidString
        UserDefaults.standard.set(id, forKey: "echoStationID")
        return id
    }()
    private var connection: EchoConnection?
    private var receiver: Task<Void, Never>?
    private var sender: Task<Void, Never>?
    private var timeout: Task<Void, Never>?
    private var audio: EchoAudio?
    private var capture: AsyncThrowingStream<Data, Error>.Continuation?
    private var generation = 0
    private var starting = false
    private var wantedInvitation: String?
    var active: Bool { phase != .idle }

    func connect(_ client: HAClient, room: String, name: String) async {
        disconnect()
        let generation = self.generation
        let connection = EchoConnection()
        self.connection = connection
        do {
            let events = try await connection.open(client, station: stationID, room: room, name: name)
            guard generation == self.generation else { await connection.close(); return }
            receiver = Task {
                do {
                    for try await event in events {
                        guard generation == self.generation else { return }
                        try await receive(event)
                    }
                } catch {
                    guard generation == self.generation else { return }
                    status = error.localizedDescription
                    disconnect()
                }
            }
        } catch {
            guard generation == self.generation else { return }
            status = error.localizedDescription
            disconnect()
        }
    }

    func open(_ invitation: String, station: String) {
        guard station == stationID else { status = L("ios_call_expired"); return }
        wantedInvitation = invitation
    }

    func receive(_ event: EchoEvent) async throws {
        switch event {
        case .directory(let peers, let pending):
            self.peers = peers
            listening = true
            incoming = pending.first { wantedInvitation == nil || $0.id == wantedInvitation }
            if wantedInvitation != nil, incoming == nil { status = L("ios_call_expired") }
            wantedInvitation = nil
        case .incoming(let invitation):
            if !active { incoming = invitation }
        case .cancelled(let id):
            if incoming?.id == id { incoming = nil }
        case .calling:
            break
        case .ringing:
            phase = .ringing
            status = L("ios_call_ringing")
        case .accepted:
            timeout?.cancel()
            try startAudio()
            phase = .live
            status = L("ios_call_live")
        case .audio(let pcm):
            if phase == .live { try audio?.play(pcm) }
        case .ended(let reason):
            endAudio()
            phase = .idle
            status = L(reason == "ended" ? "ios_call_ended" : "ios_call_failed")
        }
    }

    func call(_ speaker: String, invitation: String? = nil) async {
        guard !active, !starting, let connection else { return }
        starting = true
        defer { starting = false }
        let generation = self.generation
        phase = .connecting
        status = L("ios_connecting_audio")
        let allowed = await AVAudioApplication.requestRecordPermission()
        guard generation == self.generation else { return }
        guard allowed else {
            phase = .idle
            status = L("ios_microphone_denied"); return
        }
        incoming = nil
        do {
            try await connection.call(speaker, invitation: invitation)
            timeout = Task {
                try? await Task.sleep(nanoseconds: 45_000_000_000)
                guard !Task.isCancelled, generation == self.generation else { return }
                await hangup()
                status = L("ios_call_expired")
            }
        } catch { await hangup(); status = error.localizedDescription }
    }
    func answer() async {
        guard let invitation = incoming else { return }
        #if DEBUG
        if Demo.enabled {
            incoming = nil
            phase = .live
            status = L("ios_call_live")
            return
        }
        #endif
        await call(invitation.speaker, invitation: invitation.id)
    }
    func decline() async {
        guard let invitation = incoming else { return }
        incoming = nil
        try? await connection?.decline(invitation.id)
    }
    func hangup() async {
        endAudio()
        phase = .idle
        status = L("ios_call_ended")
        // Closing also covers cancellation before the server has supplied its call id.
        disconnect()
    }
    private func startAudio() throws {
        guard let connection else { throw NativeFailure.protocolError }
        let audio = EchoAudio()
        self.audio = audio
        // Keep the newest second of microphone audio through an upload stall; stale speech is dropped.
        let stream = AsyncThrowingStream<Data, Error>(bufferingPolicy: .bufferingNewest(25)) {
            capture = $0
        }
        let generation = self.generation
        sender = Task {
            do {
                var pending = Data()
                for try await bytes in stream {
                    pending.append(bytes)
                    while pending.count >= 640 {
                        try Task.checkCancellation()
                        try await connection.pcm(Data(pending.prefix(640)))
                        pending.removeFirst(640)
                    }
                }
            } catch {
                guard !Task.isCancelled, generation == self.generation else { return }
                await hangup()
                status = error.localizedDescription
            }
        }
        let continuation = capture!
        try audio.start { result in
            switch result {
            case .success(let pcm): continuation.yield(pcm)
            case .failure(let error): continuation.finish(throwing: error)
            }
        }
    }
    private func endAudio() {
        timeout?.cancel(); timeout = nil
        audio?.stop(); audio = nil
        capture?.finish(); capture = nil
        sender?.cancel(); sender = nil
    }
    func disconnect() {
        generation += 1
        endAudio()
        receiver?.cancel(); receiver = nil
        let previous = connection
        connection = nil
        Task { await previous?.close() }
        listening = false
        phase = .idle
        incoming = nil
    }

    #if DEBUG
    func showDemoIncoming() {
        peers = [EchoPeer(speaker: "media_player.basement", name: "Basement Echo", available: true)]
        incoming = EchoInvitation(id: "demo-invitation", speaker: "media_player.basement", name: "Basement Echo")
        listening = true
    }
    #endif
}
