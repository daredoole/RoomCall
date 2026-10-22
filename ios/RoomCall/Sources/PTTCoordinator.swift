import AVFoundation
import Combine
import PushToTalk
import UIKit

@MainActor
final class PTTCoordinator: NSObject, ObservableObject, PTChannelManagerDelegate, PTChannelRestorationDelegate {
    @Published private(set) var joined = false
    @Published private(set) var transmitting = false
    @Published private(set) var finishing = false
    @Published private(set) var requested = false
    @Published private(set) var activeRoomName: String?
    @Published var status = L("ios_choose_room")
    private var manager: PTChannelManager?
    private var channel: UUID?
    private var room: Room?
    private var client: HAClient?
    private var wanted = false
    private var opening = false
    private var audioActive = false
    private var cancelPendingStart = false
    private var audio = AudioCapture()
    private var live: LiveConnection?
    private var audioTask: Task<Void, Never>?
    private var continuation: AsyncThrowingStream<Data, Error>.Continuation?
    private var interruption: NSObjectProtocol?

    override init() {
        super.init()
        do {
            if let credentials = try SecureStore.load(),
               let id = RoomCache.defaults.string(forKey: "activeRoom"),
               let restored = RoomCache.load().first(where: { $0.id == id }) {
                client = HAClient(credentials)
                room = restored
                activeRoomName = restored.name
            }
        } catch { status = error.localizedDescription }
        interruption = NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification,
            object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.stop() }
            }
        Task {
            do { manager = try await PTChannelManager.channelManager(delegate: self, restorationDelegate: self) }
            catch { status = error.localizedDescription }
        }
    }
    func join(room: Room, client: HAClient) async {
        guard !joined, !finishing else { return }
        guard let manager else { status = L("ios_connection_failed"); return }
        let allowed = await AVAudioApplication.requestRecordPermission()
        guard allowed else { status = L("ios_microphone_denied"); return }
        do {
            try AVAudioSession.sharedInstance().setCategory(.playAndRecord, mode: .voiceChat,
                                                          options: [.defaultToSpeaker, .allowBluetoothHFP])
        } catch { status = error.localizedDescription; return }
        self.room = room
        activeRoomName = room.name
        self.client = client
        let id = UUID()
        channel = id
        RoomCache.defaults.set(id.uuidString, forKey: "channel")
        RoomCache.defaults.set(room.id, forKey: "activeRoom")
        manager.requestJoinChannel(channelUUID: id,
            descriptor: PTChannelDescriptor(name: room.name, image: UIImage(systemName: "waveform")))
    }
    func leave() {
        stop()
        if let channel { manager?.leaveChannel(channelUUID: channel) }
    }
    func begin() {
        guard let channel, joined, !finishing else { return }
        cancelPendingStart = false
        requested = true
        status = L("ios_connecting_audio")
        manager?.requestBeginTransmitting(channelUUID: channel)
    }
    func stop() {
        cancelPendingStart = true
        requested = false
        wanted = false
        if let channel { manager?.stopTransmitting(channelUUID: channel) }
        endAudio()
    }
    private func startAudio() async {
        guard wanted, audioActive, !opening, live == nil, let room, let client else { return }
        opening = true
        defer { opening = false }
        let connection = LiveConnection()
        live = connection
        do {
            try await connection.open(client: client, room: room)
            guard wanted, audioActive, joined else {
                await connection.close()
                live = nil
                finishing = false
                status = L(joined ? "ios_ready" : "ios_choose_room")
                return
            }
            let stream = AsyncThrowingStream<Data, Error>(bufferingPolicy: .bufferingOldest(50)) { self.continuation = $0 }
            audioTask = Task {
                do {
                    for try await bytes in stream { try await connection.pcm(bytes) }
                    let result = try await connection.finish()
                    status = L((result["ok"] as? Bool == true) ? "ios_request_accepted" : "ios_delivery_failed")
                } catch {
                    status = error.localizedDescription
                    audio.stop()
                    wanted = false
                    requested = false
                    if let channel { manager?.stopTransmitting(channelUUID: channel) }
                }
                await connection.close()
                live = nil
                transmitting = false
                finishing = false
            }
            let continuation = self.continuation!
            try audio.start { result in
                switch result {
                case .success(let bytes):
                    if case .dropped = continuation.yield(bytes) { continuation.finish(throwing: NativeFailure.backpressure) }
                case .failure(let error): continuation.finish(throwing: error)
                }
            }
            transmitting = true
            status = L("ios_transmitting")
        } catch {
            audio.stop()
            continuation?.finish(throwing: error)
            await connection.close()
            live = nil
            finishing = false
            requested = false
            status = error.localizedDescription
            if let channel { manager?.stopTransmitting(channelUUID: channel) }
        }
    }
    private func endAudio() {
        audio.stop()
        continuation?.finish()
        continuation = nil
        transmitting = false
        if live != nil { finishing = true; status = L("ios_sending") }
    }
    nonisolated func channelManager(_ manager: PTChannelManager, didJoinChannel channelUUID: UUID, reason: PTChannelJoinReason) {
        Task { @MainActor in
            guard client != nil else { manager.leaveChannel(channelUUID: channelUUID); return }
            channel = channelUUID
            joined = true
            status = L("ios_ready")
        }
    }
    nonisolated func channelManager(_ manager: PTChannelManager, didLeaveChannel channelUUID: UUID, reason: PTChannelLeaveReason) {
        Task { @MainActor in
            wanted = false
            requested = false
            cancelPendingStart = false
            endAudio()
            joined = false
            channel = nil
            activeRoomName = nil
            RoomCache.defaults.removeObject(forKey: "channel")
            status = L("ios_choose_room")
        }
    }
    nonisolated func channelManager(_ manager: PTChannelManager, channelUUID: UUID, didBeginTransmittingFrom source: PTChannelTransmitRequestSource) {
        Task { @MainActor in
            guard !finishing, !cancelPendingStart else { manager.stopTransmitting(channelUUID: channelUUID); return }
            wanted = true
            requested = true
            await startAudio()
        }
    }
    nonisolated func channelManager(_ manager: PTChannelManager, channelUUID: UUID, didEndTransmittingFrom source: PTChannelTransmitRequestSource) {
        Task { @MainActor in
            wanted = false
            requested = false
            cancelPendingStart = false
            endAudio()
        }
    }
    nonisolated func channelManager(_ manager: PTChannelManager, didActivate audioSession: AVAudioSession) {
        Task { @MainActor in audioActive = true; await startAudio() }
    }
    nonisolated func channelManager(_ manager: PTChannelManager, didDeactivate audioSession: AVAudioSession) {
        Task { @MainActor in audioActive = false; wanted = false; requested = false; endAudio() }
    }
    nonisolated func channelManager(_ manager: PTChannelManager, failedToJoinChannel channelUUID: UUID, error: Error) {
        Task { @MainActor in status = error.localizedDescription }
    }
    nonisolated func channelManager(_ manager: PTChannelManager, failedToBeginTransmittingInChannel channelUUID: UUID, error: Error) {
        Task { @MainActor in wanted = false; requested = false; cancelPendingStart = false; status = error.localizedDescription }
    }
    nonisolated func channelManager(_ manager: PTChannelManager, receivedEphemeralPushToken pushToken: Data) {
        // Outbound prototype: no push token is advertised without a provisioned receiving service.
    }
    nonisolated func incomingPushResult(channelManager: PTChannelManager, channelUUID: UUID, pushPayload: [String: Any]) -> PTPushResult {
        .leaveChannel
    }
    nonisolated func channelDescriptor(restoredChannelUUID channelUUID: UUID) -> PTChannelDescriptor {
        // The authenticated room is restored at startup; capture still requires user transmission.
        PTChannelDescriptor(name: RoomCache.load().first { $0.id == RoomCache.defaults.string(forKey: "activeRoom") }?.name ?? "RoomCall", image: nil)
    }
}
