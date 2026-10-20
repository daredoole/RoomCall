"""Chime tones played before a call, rendered with ffmpeg's sine generator."""

from __future__ import annotations

# Each style is a list of (frequency Hz, seconds); frequency 0 is a gap.
STYLES: dict[str, list[tuple[int, float]]] = {
    "chirp": [(1047, 0.12), (1319, 0.2)],  # walkie-talkie C6 -> E6
    "ding": [(1568, 0.35)],
    "double": [(988, 0.1), (0, 0.07), (988, 0.1)],
    "rising": [(784, 0.09), (988, 0.09), (1175, 0.16)],
    "doorbell": [(1319, 0.3), (1047, 0.45)],
}
OFF = "off"
CHIME_VOLUME = 0.35
CHIME_GAP_SECONDS = 0.12


def tone_inputs(style: str) -> list[str]:
    """ffmpeg ``-f lavfi`` input arguments for each tone of ``style``."""
    args: list[str] = []
    for freq, seconds in STYLES[style]:
        source = (
            "anullsrc=channel_layout=mono:sample_rate=44100"
            if freq == 0
            else f"sine=frequency={freq}:sample_rate=44100"
        )
        args += ["-f", "lavfi", "-t", str(seconds), "-i", source]
    return args


def chime_filter(first_input: int, count: int, *, from_file: bool = False) -> str:
    """Filter graph that joins chime inputs into one ``[chime]`` stream."""
    norm = "aresample=44100,aformat=channel_layouts=mono"
    if from_file:
        return (
            f"[{first_input}:a]{norm},volume={CHIME_VOLUME},apad=pad_dur={CHIME_GAP_SECONDS}[chime]"
        )
    parts = "".join(f"[{first_input + i}:a]{norm}[t{i}];" for i in range(count))
    joined = "".join(f"[t{i}]" for i in range(count))
    return (
        f"{parts}{joined}concat=n={count}:v=0:a=1,"
        f"volume={CHIME_VOLUME},apad=pad_dur={CHIME_GAP_SECONDS}[chime]"
    )
