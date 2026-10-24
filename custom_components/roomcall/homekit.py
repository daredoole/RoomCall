"""Apple Home intercom: each room is a HomeKit camera whose talk button plays in that room.

The Home app talks to cameras over SRTP. RoomCall answers as a camera bridge (HAP-python, the
library Home Assistant's own HomeKit integration uses), shows a picture of the room as video,
and decodes the talk-back audio the iPhone sends with ffmpeg. A voice-activity segmenter turns
the open talk stream into phrases, and each phrase is played in the room like any other call.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import tempfile
import zlib
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .talkstream import SAMPLE_RATE, Segmenter

if TYPE_CHECKING:
    from . import RoomCallData

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 21099
STORE_KEY = "roomcall.homekit"
TALK_PAYLOAD_TYPE = 110
TALK_IDLE_SECONDS = 0.5


def camera_options(address: str) -> dict[str, Any]:
    """Stream settings offered to the Home app (H.264 video, Opus-only audio)."""
    from pyhap.camera import (
        VIDEO_CODEC_PARAM_LEVEL_TYPES as LEVELS,
    )
    from pyhap.camera import (
        VIDEO_CODEC_PARAM_PROFILE_ID_TYPES as PROFILES,
    )

    return {
        "video": {
            "codec": {
                "profiles": [PROFILES["BASELINE"], PROFILES["MAIN"], PROFILES["HIGH"]],
                "levels": [LEVELS["TYPE3_1"], LEVELS["TYPE3_2"], LEVELS["TYPE4_0"]],
            },
            "resolutions": [
                [1280, 720, 30],
                [1024, 576, 30],
                [640, 360, 30],
                [480, 270, 30],
                [320, 180, 30],
                [1280, 960, 30],
                [640, 480, 30],
                [480, 360, 30],
                [320, 240, 30],
                [320, 240, 15],
            ],
        },
        # Opus only, so the talk-back audio is Opus too.
        "audio": {
            "codecs": [{"type": "OPUS", "samplerate": 24}, {"type": "OPUS", "samplerate": 16}]
        },
        "srtp": True,
        "address": address,
    }


def state_file(area_id: str) -> str:
    """HomeKit pairing state for one room (a safe file name for any area id)."""
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in area_id)
    return f"roomcall.homekit.{safe}.{zlib.crc32(area_id.encode()):08x}.state"


def talk_sdp(
    address: str,
    port: int,
    srtp_key: str,
    *,
    ipv6: bool = False,
    payload_type: int = TALK_PAYLOAD_TYPE,
) -> str:
    """SDP that lets ffmpeg receive and decrypt the iPhone's talk-back audio.

    ``address`` is *this* machine's address: for an incoming stream ffmpeg binds to the
    SDP's connection address, which is where the iPhone sends the audio.
    """
    family = "IP6" if ipv6 else "IP4"
    return "\r\n".join(
        [
            "v=0",
            f"o=- 0 0 IN {family} {address}",
            "s=RoomCall talk",
            f"c=IN {family} {address}",
            "t=0 0",
            f"m=audio {port} RTP/AVP {payload_type}",
            "b=AS:24",
            f"a=rtpmap:{payload_type} opus/48000/2",
            "a=rtcp-mux",
            f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{srtp_key}",
            "",
        ]
    )


def talk_command(ffmpeg: str) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-protocol_whitelist",
        "pipe,udp,rtp,file,crypto",
        # Low latency: no probing (the SDP describes the stream), no input buffering, and
        # no jitter queue; decoded audio is flushed per packet instead of per 32 KB.
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-probesize",
        "32",
        "-analyzeduration",
        "0",
        "-reorder_queue_size",
        "0",
        "-f",
        "sdp",
        "-i",
        "pipe:0",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-flush_packets",
        "1",
        "-f",
        "s16le",
        "pipe:1",
    ]


def _byte(value: Any, default: int) -> int:
    """HAP-python hands some numbers over as one raw byte."""
    if value is None:
        return default
    if isinstance(value, bytes | bytearray):
        return value[0] if value else default
    return int(value)


def video_command(ffmpeg: str, picture: str, cfg: dict[str, Any]) -> list[str]:
    """A still picture of the room, streamed as H.264 over SRTP."""
    fps = int(cfg.get("fps") or 15)
    bitrate = int(cfg.get("v_max_bitrate") or 299)
    mtu = int.from_bytes(cfg["v_max_mtu"], "little") if cfg.get("v_max_mtu") else 1378
    payload_type = _byte(cfg.get("v_payload_type"), 99)
    port = cfg["v_port"]
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-re",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        picture,
        "-an",
        "-vf",
        f"scale={cfg.get('width', 1280)}:{cfg.get('height', 720)}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "baseline",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-r",
        str(fps),
        "-g",
        str(fps * 2),
        "-b:v",
        f"{bitrate}k",
        "-bufsize",
        f"{bitrate * 2}k",
        "-maxrate",
        f"{bitrate}k",
        "-payload_type",
        str(payload_type),
        "-ssrc",
        str(cfg["v_ssrc"]),
        "-f",
        "rtp",
        "-srtp_out_suite",
        "AES_CM_128_HMAC_SHA1_80",
        "-srtp_out_params",
        cfg["v_srtp_key"],
        f"srtp://{cfg['address']}:{port}?rtcpport={port}&localrtcpport={port}&pkt_size={mtu}",
    ]


def render_room_picture(name: str, size: tuple[int, int] = (1280, 720)) -> bytes:
    """PNG of the room name, shown as the camera's 'video'."""
    from PIL import Image, ImageDraw, ImageFont  # Home Assistant ships Pillow

    width, height = size
    image = Image.new("RGB", size, (16, 24, 40))
    draw = ImageDraw.Draw(image)
    for y in range(height):  # soft vertical gradient
        shade = int(24 + 40 * y / height)
        draw.line([(0, y), (width, y)], fill=(16, shade, 72))
    try:
        big = ImageFont.load_default(size=height // 7)
        small = ImageFont.load_default(size=height // 18)
    except TypeError:  # Pillow < 10.1
        big = small = ImageFont.load_default()
    draw.text((width / 2, height * 0.42), name, font=big, fill="white", anchor="mm")
    draw.text(
        (width / 2, height * 0.62),
        "RoomCall · tap the microphone to talk",
        font=small,
        fill=(170, 190, 230),
        anchor="mm",
    )
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _jpeg(png: bytes) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGB").save(out, "JPEG", quality=85)
    return out.getvalue()


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        async with asyncio.timeout(2):
            await proc.wait()
    except TimeoutError:
        proc.kill()


async def _log_stderr(stream: asyncio.StreamReader, label: str) -> None:
    while line := await stream.readline():
        text = line.decode(errors="replace").strip()
        if text:
            _LOGGER.warning("RoomCall HomeKit %s: %s", label, text)


async def talk_loop(
    ffmpeg: str,
    sdp: str,
    on_phrase: Callable[[bytes], None],
    session: dict[str, Any],
    label: str = "talk",
    *,
    on_start: Callable[[bytes], None] | None = None,
    on_audio: Callable[[bytes], None] | None = None,
    on_end: Callable[[], None] | None = None,
) -> None:
    """Receive talk-back audio for as long as the Home app keeps the camera open.

    ffmpeg gives up after about 20 s without packets (the iPhone only sends while the talk
    button is on), so the receiver is restarted until the session ends.
    """
    phrases = 0

    def counted(pcm: bytes) -> None:
        nonlocal phrases
        phrases += 1
        on_phrase(pcm)

    segmenter = Segmenter(counted, on_start=on_start, on_audio=on_audio, on_end=on_end)
    while session.get("active"):
        proc = await asyncio.create_subprocess_exec(
            *talk_command(ffmpeg),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        session["talk"] = proc
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        errors = asyncio.ensure_future(_log_stderr(proc.stderr, label))
        proc.stdin.write(sdp.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        received = 0
        while True:
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(3200), TALK_IDLE_SECONDS)
            except TimeoutError:
                # The iPhone stops sending (rather than sending silence) when the talk button
                # is released, so a gap in the audio ends the phrase.
                segmenter.flush()
                continue
            if not chunk:
                break
            received += len(chunk)
            segmenter.feed(chunk)
        await proc.wait()
        await errors
        segmenter.flush()
        _LOGGER.info(
            "RoomCall HomeKit %s: receiver ended (exit %s), decoded %.1f s of audio, "
            "%d phrase(s) so far",
            label,
            proc.returncode,
            received / (SAMPLE_RATE * 2),
            phrases,
        )
        if session.get("active"):
            # A receiver that could not even start should not be retried in a tight loop.
            await asyncio.sleep(0.2 if received else 2.0)


class HomeKitIntercom:
    """Runs the RoomCall camera bridge for Apple Home."""

    def __init__(self, hass: HomeAssistant, data: RoomCallData, port: int) -> None:
        self.hass = hass
        self.data = data
        self.port = port
        self.drivers: dict[str, Any] = {}  # area_id -> AccessoryDriver
        self.address: str | None = None  # where the iPhone sends talk-back audio
        self.pincode: str | None = None
        self._tmp: tempfile.TemporaryDirectory[str] | None = None

    @property
    def paired(self) -> bool:
        """True once every room has been added to Apple Home."""
        return bool(self.drivers) and all(d.state.paired for d in self.drivers.values())

    async def _load_pincode(self) -> str:
        """One pairing code for every room, kept across restarts."""
        from pyhap.util import generate_pincode

        store: Store[dict[str, str]] = Store(self.hass, 1, STORE_KEY)
        saved = await store.async_load() or {}
        if not (code := saved.get("pincode")):
            code = generate_pincode().decode()
            await store.async_save({"pincode": code})
        return code

    async def async_start(self, rooms: list[dict[str, Any]]) -> None:
        """Publish each room as its own HomeKit camera accessory.

        Apple Home streams cameras reliably only as stand-alone accessories (not behind a
        bridge), so each room gets its own accessory server on consecutive ports.
        """
        from homeassistant.components.network import async_get_source_ip
        from homeassistant.components.zeroconf import async_get_async_instance
        from pyhap.accessory_driver import AccessoryDriver

        address = await async_get_source_ip(self.hass)
        self.address = address
        zeroconf = await async_get_async_instance(self.hass)
        self.pincode = await self._load_pincode()
        storage = self.hass.config.path(".storage")
        await self.hass.async_add_executor_job(partial(os.makedirs, storage, exist_ok=True))
        self._tmp = await self.hass.async_add_executor_job(
            partial(tempfile.TemporaryDirectory, prefix="roomcall-homekit-")
        )
        tmp = Path(self._tmp.name)
        for index, room in enumerate(sorted(rooms, key=lambda r: r["area_id"])):
            area_id = room["area_id"]
            png = await self.hass.async_add_executor_job(render_room_picture, room["name"])
            picture = tmp / f"{area_id}.png"
            await self.hass.async_add_executor_job(picture.write_bytes, png)
            # Creating the driver and camera reads HAP definitions and pairing state from
            # disk, so do it off the event loop.
            driver = await self.hass.async_add_executor_job(
                partial(
                    AccessoryDriver,
                    loop=self.hass.loop,
                    port=self.port + index,
                    address=address,
                    pincode=self.pincode.encode(),
                    persist_file=self.hass.config.path(".storage", state_file(area_id)),
                    async_zeroconf_instance=zeroconf,
                )
            )
            await self.hass.async_add_executor_job(
                _build_camera, self, driver, address, area_id, room["name"], str(picture), png
            )
            await driver.async_start()
            self.drivers[area_id] = driver
        # The pairing code is only shown to admins in the persistent notification.
        _LOGGER.info(
            "RoomCall Apple Home: %d rooms on %s:%s+", len(self.drivers), address, self.port
        )

    async def async_stop(self) -> None:
        for driver in self.drivers.values():
            with contextlib.suppress(Exception):
                await driver.async_stop()
        self.drivers.clear()
        if self._tmp is not None:
            tmp, self._tmp = self._tmp, None
            await self.hass.async_add_executor_job(tmp.cleanup)

    async def async_live_start(self, area_id: str) -> Any:
        """Start a live call to the room; returns the LiveStream to feed."""
        from .calls import CallRequest, place_call, prepare_call
        from .const import API_LIVE
        from .http import base_url
        from .live import LiveStream

        data = self.data
        request = CallRequest(targets=[area_id], media_id="", kind="talk", from_room="Apple Home")
        prepared = prepare_call(self.hass, data, request)  # raises CallError
        stream = LiveStream(
            get_ffmpeg_manager(self.hass).binary,
            gain_db=data.gain_db,
            chime=data.chime_style,
            chime_url=data.chime_url,
        )
        await stream.start()
        data.live.add(stream)
        request.media_id = f"{base_url(self.hass)}{API_LIVE.format(stream=f'{stream.id}.mp3')}"
        request.live = stream

        async def run() -> None:
            try:
                await place_call(self.hass, data, request, prepared)
            except Exception:
                _LOGGER.exception("RoomCall HomeKit live call to %s failed", area_id)
                await stream.abort()

        self.hass.async_create_background_task(run(), f"roomcall homekit live {area_id}")
        return stream


def _build_camera(
    intercom: HomeKitIntercom,
    driver: Any,
    address: str,
    area_id: str,
    name: str,
    picture: str,
    png: bytes,
) -> Any:
    """Create the room's camera and attach it (this loads saved pairing state from disk)."""
    camera = _camera_class()(camera_options(address), driver, f"{name} Intercom")
    camera.setup_intercom(intercom, area_id, picture, png)
    driver.add_accessory(camera)
    return camera


class _LivePhrase:
    """One spoken phrase from the Home app, streamed to the room while it is spoken."""

    def __init__(self, intercom: HomeKitIntercom, area_id: str, preroll: bytes) -> None:
        self._stream: Any = None
        self._pending = bytearray(preroll)
        self._ended = False
        intercom.hass.async_create_task(self._start(intercom, area_id))

    async def _start(self, intercom: HomeKitIntercom, area_id: str) -> None:
        from .calls import CallError

        try:
            stream = await intercom.async_live_start(area_id)
        except CallError as err:
            _LOGGER.warning("RoomCall HomeKit call to %s not played: %s", area_id, err)
            return
        except Exception:
            _LOGGER.exception("RoomCall HomeKit live call to %s failed", area_id)
            return
        self._stream = stream
        stream.feed(bytes(self._pending))
        self._pending.clear()
        if self._ended:
            stream.end()

    def feed(self, pcm: bytes) -> None:
        if self._stream is not None:
            self._stream.feed(pcm)
        else:
            self._pending += pcm

    def end(self) -> None:
        self._ended = True
        if self._stream is not None:
            self._stream.end()


_CAMERA_CLASS: type | None = None


def _camera_class() -> type:
    """Build the camera class lazily so HAP-python is only imported when HomeKit is on."""
    global _CAMERA_CLASS
    if _CAMERA_CLASS is not None:
        return _CAMERA_CLASS
    from pyhap.camera import Camera

    class RoomCamera(Camera):  # type: ignore[misc]
        """A room in Apple Home; its talk button plays in that room."""

        def setup_intercom(
            self, intercom: HomeKitIntercom, area_id: str, picture: str, png: bytes
        ) -> None:
            self.intercom = intercom
            self.area_id = area_id
            self.picture = picture
            self.snapshot = _jpeg(png)
            # The Speaker service is what makes the Home app show its talk button.
            speaker = self.add_preload_service("Speaker", ["Volume"])
            speaker.configure_char("Mute", value=False)
            speaker.configure_char("Volume", value=100)

        async def start_stream(self, session_info: dict, stream_config: dict) -> bool:
            hass = self.intercom.hass
            ffmpeg = get_ffmpeg_manager(hass).binary
            try:
                video = await asyncio.create_subprocess_exec(
                    *video_command(ffmpeg, self.picture, stream_config),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as err:
                _LOGGER.error("RoomCall HomeKit stream failed to start: %s", err)
                return False
            assert video.stderr is not None
            label = f"{self.area_id} talk"
            session_info["video_log"] = asyncio.ensure_future(
                _log_stderr(video.stderr, f"{self.area_id} video")
            )
            payload_type = _byte(stream_config.get("a_payload_type"), TALK_PAYLOAD_TYPE)
            _LOGGER.info(
                "RoomCall HomeKit %s: stream to %s video:%s audio:%s codec=%s rate=%skHz pt=%s",
                self.area_id,
                stream_config["address"],
                stream_config.get("v_port"),
                stream_config.get("a_port"),
                stream_config.get("a_codec"),
                stream_config.get("a_sample_rate"),
                payload_type,
            )
            local = self.intercom.address or "0.0.0.0"
            sdp = talk_sdp(
                local,
                stream_config["a_port"],
                stream_config["a_srtp_key"],
                ipv6=":" in local,
                payload_type=payload_type,
            )

            # Live: the call starts as soon as you start speaking and streams while you talk.
            current: dict[str, _LivePhrase] = {}

            def start(pcm: bytes) -> None:
                current["call"] = _LivePhrase(self.intercom, self.area_id, pcm)

            def audio(pcm: bytes) -> None:
                if call := current.get("call"):
                    call.feed(pcm)

            def end() -> None:
                if call := current.pop("call", None):
                    call.end()

            def phrase(pcm: bytes) -> None:
                _LOGGER.info(
                    "RoomCall HomeKit %s: phrase of %.1f s", self.area_id, len(pcm) / 32000
                )

            session_info["active"] = True
            session_info["video"] = video
            session_info["loop"] = hass.async_create_background_task(
                talk_loop(
                    ffmpeg,
                    sdp,
                    phrase,
                    session_info,
                    label,
                    on_start=start,
                    on_audio=audio,
                    on_end=end,
                ),
                f"roomcall homekit talk {self.area_id}",
            )
            return True

        async def stop_stream(self, session_info: dict) -> None:
            _LOGGER.info("RoomCall HomeKit %s: stream stopped", self.area_id)
            session_info["active"] = False
            for key in ("video", "talk"):
                if (proc := session_info.pop(key, None)) is not None:
                    await _terminate(proc)
            if loop := session_info.pop("loop", None):
                with contextlib.suppress(asyncio.CancelledError):
                    await loop

        async def reconfigure_stream(self, session_info: dict, stream_config: dict) -> bool:
            return True

        async def async_get_snapshot(self, image_size: dict) -> bytes:
            return self.snapshot

        def get_snapshot(self, image_size: dict) -> bytes:
            return self.snapshot

    _CAMERA_CLASS = RoomCamera
    return RoomCamera
