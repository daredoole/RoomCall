import AVFoundation

final class AudioCapture {
    private let engine = AVAudioEngine()
    private var installed = false

    static func converter(from input: AVAudioFormat) throws -> AVAudioConverter {
        guard let output = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000,
                                        channels: 1, interleaved: true),
              let converter = AVAudioConverter(from: input, to: output) else {
            throw NativeFailure.protocolError
        }
        converter.primeMethod = .none
        return converter
    }
    static func pcm(_ buffer: AVAudioPCMBuffer, converter: AVAudioConverter) throws -> Data {
        let frames = AVAudioFrameCount(ceil(Double(buffer.frameLength) * 16_000 / buffer.format.sampleRate) + 32)
        guard let output = AVAudioPCMBuffer(pcmFormat: converter.outputFormat, frameCapacity: frames) else {
            throw NativeFailure.protocolError
        }
        var provided = false
        var error: NSError?
        converter.convert(to: output, error: &error) { _, status in
            if provided { status.pointee = .noDataNow; return nil }
            provided = true
            status.pointee = .haveData
            return buffer
        }
        if let error { throw error }
        let audio = output.audioBufferList.pointee.mBuffers
        guard let bytes = audio.mData else { throw NativeFailure.protocolError }
        return Data(bytes: bytes, count: Int(audio.mDataByteSize))
    }
    func start(consume: @escaping (Result<Data, Error>) -> Void) throws {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        let converter = try Self.converter(from: format)
        input.installTap(onBus: 0, bufferSize: 1920, format: format) { buffer, _ in
            do { consume(.success(try Self.pcm(buffer, converter: converter))) }
            catch { consume(.failure(error)) }
        }
        installed = true
        engine.prepare()
        try engine.start()
    }
    func stop() {
        engine.stop()
        if installed { engine.inputNode.removeTap(onBus: 0); installed = false }
    }
    deinit { stop() }
}
