"""Convert browser recordings (WebM/Opus, MP4/AAC) into MP3 every speaker can play."""

from __future__ import annotations

import asyncio
import shutil

PLAYABLE = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav"}


class TranscodeError(RuntimeError):
    pass


async def to_playable(data: bytes, content_type: str, *, gain_db: float = 0.0) -> tuple[bytes, str]:
    """Return ``(audio, extension)``; passes MP3/WAV through, otherwise encodes MP3 via ffmpeg."""
    base_type = content_type.split(";", 1)[0].strip().lower()
    if base_type in PLAYABLE and gain_db == 0.0:
        return data, PLAYABLE[base_type]
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise TranscodeError("ffmpeg is required to convert browser recordings")
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-af",
        f"volume={gain_db}dB",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "128k",
        "-f",
        "mp3",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(data)
    if proc.returncode != 0 or not out:
        raise TranscodeError(err.decode(errors="replace").strip() or "ffmpeg produced no audio")
    return out, "mp3"
