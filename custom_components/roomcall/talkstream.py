"""Split a continuous talk-back audio stream into spoken phrases.

Used where audio arrives as an open stream instead of a finished recording (the HomeKit talk
button): each phrase becomes one call, ended by a short silence.
"""

from __future__ import annotations

import array
import io
import math
import wave
from collections.abc import Callable
from dataclasses import dataclass, field

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2  # 16-bit mono


@dataclass(slots=True)
class Segmenter:
    """Energy-based voice activity detection with an adaptive noise floor."""

    on_phrase: Callable[[bytes], None]
    start_ms: int = 120  # loud this long to start a phrase
    end_ms: int = 700  # quiet this long to end it
    min_ms: int = 400  # shorter phrases are dropped (taps, clicks)
    max_ms: int = 30000  # long monologues are cut here
    pre_roll_ms: int = 200  # keep a little audio from before the start
    ratio: float = 3.0  # speech must be this much louder than the noise floor
    min_level: float = 250.0  # absolute RMS floor (16-bit), ignores near-silence
    _buffer: bytearray = field(default_factory=bytearray)
    _phrase: bytearray = field(default_factory=bytearray)
    _history: list[bytes] = field(default_factory=list)
    _noise: float = 0.0
    _loud: int = 0
    _quiet: int = 0
    _voiced: int = 0
    _speaking: bool = False

    @staticmethod
    def rms(frame: bytes) -> float:
        samples = array.array("h", frame)
        if not samples:
            return 0.0
        return math.sqrt(sum(s * s for s in samples) / len(samples))

    def feed(self, data: bytes) -> None:
        self._buffer += data
        while len(self._buffer) >= FRAME_BYTES:
            frame = bytes(self._buffer[:FRAME_BYTES])
            del self._buffer[:FRAME_BYTES]
            self._frame(frame)

    def flush(self) -> None:
        """End of stream: emit a phrase in progress."""
        if self._speaking:
            self._emit()

    def _threshold(self) -> float:
        return max(self.min_level, self._noise * self.ratio)

    def _frame(self, frame: bytes) -> None:
        level = self.rms(frame)
        loud = level >= self._threshold()
        if not loud:
            # Learn background noise from quiet frames only: fall fast, rise slowly.
            if self._noise == 0:
                self._noise = level
            elif level < self._noise:
                self._noise = 0.8 * self._noise + 0.2 * level
            else:
                self._noise = 0.98 * self._noise + 0.02 * level
        if not self._speaking:
            self._history.append(frame)
            del self._history[: -max(1, self.pre_roll_ms // FRAME_MS)]
            self._loud = self._loud + 1 if loud else 0
            if self._loud * FRAME_MS >= self.start_ms:
                self._speaking = True
                self._quiet = 0
                self._voiced = self._loud
                self._phrase = bytearray(b"".join(self._history))
                self._history.clear()
            return
        self._phrase += frame
        self._quiet = 0 if loud else self._quiet + 1
        self._voiced += 1 if loud else 0
        too_long = len(self._phrase) // (FRAME_BYTES // FRAME_MS) >= self.max_ms
        if self._quiet * FRAME_MS >= self.end_ms or too_long:
            self._emit()

    def _emit(self) -> None:
        phrase, voiced_ms = bytes(self._phrase), self._voiced * FRAME_MS
        self._phrase = bytearray()
        self._speaking = False
        self._loud = self._quiet = self._voiced = 0
        if voiced_ms >= self.min_ms:
            self.on_phrase(phrase)


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()
