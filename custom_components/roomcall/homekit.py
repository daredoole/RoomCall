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
import tempfile
import zlib
from collections.abc import Callable
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


def talk_sdp(address: str, port: int, srtp_key: str, *, ipv6: bool = False) -> str:
    """SDP that lets ffmpeg receive and decrypt the iPhone's talk-back audio."""
    family = "IP6" if ipv6 else "IP4"
    return "\r\n".join(
        [
            "v=0",
            f"o=- 0 0 IN {family} {address}",
            "s=RoomCall talk",
            f"c=IN {family} {address}",
            "t=0 0",
            f"m=audio {port} RTP/AVP {TALK_PAYLOAD_TYPE}",
            "b=AS:24",
            f"a=rtpmap:{TALK_PAYLOAD_TYPE} opus/48000/2",
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
        "error",
        "-protocol_whitelist",
        "pipe,udp,rtp,file,crypto",
        "-f",
        "sdp",
        "-i",
        "pipe:0",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]


def video_command(ffmpeg: str, picture: str, cfg: dict[str, Any]) -> list[str]:
    """A still picture of the room, streamed as H.264 over SRTP."""
    fps = int(cfg.get("fps") or 15)
    bitrate = int(cfg.get("v_max_bitrate") or 299)
    mtu = int.from_bytes(cfg["v_max_mtu"], "little") if cfg.get("v_max_mtu") else 1378
    payload = cfg.get("v_payload_type") or b"\x63"
    payload_type = payload[0] if isinstance(payload, bytes | bytearray) else int(payload)
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


async def talk_loop(
    ffmpeg: str, sdp: str, on_phrase: Callable[[bytes], None], session: dict[str, Any]
) -> None:
    """Receive talk-back audio for as long as the Home app keeps the camera open.

    ffmpeg gives up after about 20 s without packets (the iPhone only sends while the talk
    button is on), so the receiver is restarted until the session ends.
    """
    segmenter = Segmenter(on_phrase)
    while session.get("active"):
        proc = await asyncio.create_subprocess_exec(
            *talk_command(ffmpeg),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        session["talk"] = proc
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(sdp.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        while chunk := await proc.stdout.read(3200):
            segmenter.feed(chunk)
        await proc.wait()
        segmenter.flush()
        if session.get("active"):
            await asyncio.sleep(0.2)


class HomeKitIntercom:
    """Runs the RoomCall camera bridge for Apple Home."""

    def __init__(self, hass: HomeAssistant, data: RoomCallData, port: int) -> None:
        self.hass = hass
        self.data = data
        self.port = port
        self.drivers: dict[str, Any] = {}  # area_id -> AccessoryDriver
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
        zeroconf = await async_get_async_instance(self.hass)
        self.pincode = await self._load_pincode()
        self._tmp = tempfile.TemporaryDirectory(prefix="roomcall-homekit-")
        tmp = Path(self._tmp.name)
        for index, room in enumerate(sorted(rooms, key=lambda r: r["area_id"])):
            area_id = room["area_id"]
            png = await self.hass.async_add_executor_job(render_room_picture, room["name"])
            picture = tmp / f"{area_id}.png"
            await self.hass.async_add_executor_job(picture.write_bytes, png)
            driver = AccessoryDriver(
                loop=self.hass.loop,
                port=self.port + index,
                address=address,
                pincode=self.pincode.encode(),
                persist_file=self.hass.config.path(".storage", state_file(area_id)),
                async_zeroconf_instance=zeroconf,
            )
            camera = _camera_class()(camera_options(address), driver, f"{room['name']} Intercom")
            camera.setup_intercom(self, area_id, str(picture), png)
            driver.add_accessory(camera)
            await driver.async_start()
            self.drivers[area_id] = driver
        _LOGGER.info(
            "RoomCall Apple Home: %d rooms on %s:%s+ (code %s)",
            len(self.drivers),
            address,
            self.port,
            self.pincode,
        )

    async def async_stop(self) -> None:
        for driver in self.drivers.values():
            with contextlib.suppress(Exception):
                await driver.async_stop()
        self.drivers.clear()
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    async def async_phrase(self, area_id: str, pcm: bytes) -> None:
        """One spoken phrase from the Home app: play it in the room."""
        from .calls import CallError, place_recorded_call
        from .talkstream import pcm_to_wav

        try:
            await place_recorded_call(
                self.hass,
                self.data,
                pcm_to_wav(pcm),
                "audio/wav",
                targets=[area_id],
                from_label="Apple Home",
            )
        except CallError as err:
            _LOGGER.warning("RoomCall HomeKit call to %s not played: %s", area_id, err)
        except Exception:
            _LOGGER.exception("RoomCall HomeKit call to %s failed", area_id)


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
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError as err:
                _LOGGER.error("RoomCall HomeKit stream failed to start: %s", err)
                return False
            sdp = talk_sdp(
                stream_config["address"],
                stream_config["a_port"],
                stream_config["a_srtp_key"],
                ipv6=":" in stream_config["address"],
            )

            def phrase(pcm: bytes) -> None:
                hass.async_create_task(self.intercom.async_phrase(self.area_id, pcm))

            session_info["active"] = True
            session_info["video"] = video
            session_info["loop"] = hass.async_create_background_task(
                talk_loop(ffmpeg, sdp, phrase, session_info),
                f"roomcall homekit talk {self.area_id}",
            )
            return True

        async def stop_stream(self, session_info: dict) -> None:
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
