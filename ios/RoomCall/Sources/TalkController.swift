import AVFoundation
import Foundation

/// Hold to talk inside the app: press opens a live call and the microphone, release finishes it.
/// Speakers start playing while you are still talking, like the dashboard card.
@MainActor
final class TalkController: ObservableObject {
    enum Phase { case idle, connecting, live, sending }

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var status = L("ios_hold_to_talk")
    private let audio = AudioCapture()
    private var continuation: AsyncThrowingStream<Data, Error>.Continuation?
    private var released = false

    var active: Bool { phase != .idle }

    func press(_ service: any RoomCallService, target: Target, urgent: Bool, from: String?) {
        guard phase == .idle else { return }
        released = false
        phase = .connecting
        status = L("ios_connecting_audio")
        Task { await start(service, target: target, urgent: urgent, from: from) }
    }

    func release() {
        released = true
        if phase == .live { endCapture() }  // still connecting: start() stops once it opens
    }

    #if DEBUG
    /// Documentation screenshots only: show a live call without a microphone.
    func showDemoTalking() {
        phase = .live
        status = L("ios_transmitting")
    }
    #endif

    private func start(_ service: any RoomCallService, target: Target, urgent: Bool, from: String?) async {
        #if DEBUG
        let demo = Demo.enabled
        #else
        let demo = false
        #endif
        if !demo {
            guard await AVAudioApplication.requestRecordPermission() else {
                return finish(L("ios_microphone_denied"))
            }
        }
        do {
            if !demo {
                let session = AVAudioSession.sharedInstance()
                try session.setCategory(.playAndRecord, mode: .voiceChat,
                                        options: [.defaultToSpeaker, .allowBluetoothHFP])
                try session.setActive(true)
            }
            let transport = try await service.talk(target, urgent: urgent, from: from)
            // Let go before the call connected: nothing was recorded, so nothing is sent.
            if released {
                await transport.close()
                return finish(L("ios_hold_to_talk"))
            }
            let stream = AsyncThrowingStream<Data, Error>(bufferingPolicy: .bufferingOldest(50)) {
                self.continuation = $0
            }
            Task {
                do {
                    for try await bytes in stream { try await transport.pcm(bytes) }
                    let result = try await transport.finish()
                    finish(L(result["ok"] as? Bool == true ? "ios_request_accepted" : "ios_delivery_failed"))
                } catch {
                    finish(error.localizedDescription)
                }
                await transport.close()
            }
            if !demo, let continuation {
                try audio.start { result in
                    switch result {
                    case .success(let bytes):
                        if case .dropped = continuation.yield(bytes) {
                            continuation.finish(throwing: NativeFailure.backpressure)
                        }
                    case .failure(let error): continuation.finish(throwing: error)
                    }
                }
            }
            phase = .live
            status = L("ios_transmitting")
            if released { endCapture() }
        } catch {
            audio.stop()
            continuation?.finish(throwing: error)
            continuation = nil
            finish(error.localizedDescription)
        }
    }

    private func endCapture() {
        audio.stop()
        continuation?.finish()
        continuation = nil
        phase = .sending
        status = L("ios_sending")
    }

    private func finish(_ message: String) {
        phase = .idle
        status = message
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
