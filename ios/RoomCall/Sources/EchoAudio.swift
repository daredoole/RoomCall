import AVFoundation

/// Echo's wire format is mono signed PCM16 little endian at 16 kHz. The audio
/// engine handles the phone's hardware rate and voice processing on both paths.
final class EchoAudio {
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private var tapped = false
    private let lock = NSLock()
    private var queued = 0

    static func playback(_ pcm: Data) throws -> AVAudioPCMBuffer {
        guard [640, 1280].contains(pcm.count),
              let format = AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(pcm.count / 2)),
              let samples = buffer.floatChannelData?[0] else { throw NativeFailure.protocolError }
        buffer.frameLength = buffer.frameCapacity
        for index in 0..<Int(buffer.frameLength) {
            let bits = UInt16(pcm[index * 2]) | UInt16(pcm[index * 2 + 1]) << 8
            samples[index] = Float(Int16(bitPattern: bits)) / 32768
        }
        return buffer
    }

    func start(_ consume: @escaping (Result<Data, Error>) -> Void) throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .voiceChat,
                                options: [.defaultToSpeaker, .allowBluetoothHFP])
        try session.setActive(true)
        try engine.inputNode.setVoiceProcessingEnabled(true)
        engine.attach(player)
        let format = AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1)!
        engine.connect(player, to: engine.mainMixerNode, format: format)
        let input = engine.inputNode
        let hardware = input.outputFormat(forBus: 0)
        let converter = try AudioCapture.converter(from: hardware)
        input.installTap(onBus: 0, bufferSize: 1920, format: hardware) { buffer, _ in
            do { consume(.success(try AudioCapture.pcm(buffer, converter: converter))) }
            catch { consume(.failure(error)) }
        }
        tapped = true
        engine.prepare()
        try engine.start()
        player.play()
    }

    func play(_ pcm: Data) throws {
        let buffer = try Self.playback(pcm)
        lock.lock()
        // After a Wi-Fi stall, drop late audio like the Echo does instead of ending the call.
        guard queued < 25 else { lock.unlock(); return }
        queued += 1
        lock.unlock()
        player.scheduleBuffer(buffer) { [weak self] in
            guard let self else { return }
            self.lock.lock(); self.queued -= 1; self.lock.unlock()
        }
    }
    func stop() {
        engine.stop()
        if tapped { engine.inputNode.removeTap(onBus: 0); tapped = false }
        player.stop()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
    deinit { stop() }
}
