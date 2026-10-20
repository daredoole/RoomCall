"""The talk-back segmenter turns a continuous stream into separate phrases."""

from __future__ import annotations

import array
import math
import random

from custom_components.roomcall.talkstream import SAMPLE_RATE, Segmenter, pcm_to_wav


def _tone(ms: int, amplitude: int = 6000) -> bytes:
    n = SAMPLE_RATE * ms // 1000
    return array.array(
        "h", (int(amplitude * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE)) for i in range(n))
    ).tobytes()


def _noise(ms: int, amplitude: int = 80) -> bytes:
    rng = random.Random(ms)
    n = SAMPLE_RATE * ms // 1000
    return array.array("h", (rng.randint(-amplitude, amplitude) for _ in range(n))).tobytes()


def _run(stream: bytes, chunk: int = 777, **kwargs) -> list[bytes]:
    phrases: list[bytes] = []
    seg = Segmenter(phrases.append, **kwargs)
    for i in range(0, len(stream), chunk):  # odd chunk sizes, like a network stream
        seg.feed(stream[i : i + chunk])
    seg.flush()
    return phrases


def _ms(pcm: bytes) -> int:
    return len(pcm) * 1000 // (SAMPLE_RATE * 2)


def test_two_phrases_separated_by_silence():
    stream = _noise(500) + _tone(1500) + _noise(1000) + _tone(800) + _noise(1000)
    phrases = _run(stream)
    assert len(phrases) == 2
    assert 1500 <= _ms(phrases[0]) <= 1500 + 700 + 200 + 40
    assert 800 <= _ms(phrases[1]) <= 800 + 700 + 200 + 40


def test_background_noise_alone_is_ignored():
    assert _run(_noise(5000)) == []


def test_short_click_is_dropped():
    assert _run(_noise(500) + _tone(200) + _noise(1000)) == []


def test_brief_pause_inside_a_phrase_does_not_split_it():
    stream = _noise(300) + _tone(600) + _noise(300) + _tone(600) + _noise(1000)
    assert len(_run(stream)) == 1


def test_phrase_in_progress_is_flushed_at_end_of_stream():
    phrases = _run(_noise(300) + _tone(900))
    assert len(phrases) == 1


def test_long_monologue_is_cut():
    phrases = _run(_noise(300) + _tone(5000) + _noise(1000), max_ms=2000)
    assert len(phrases) >= 2
    assert all(_ms(p) <= 2000 + 40 for p in phrases[:-1])


def test_pcm_to_wav_header():
    wav = pcm_to_wav(_tone(100))
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
