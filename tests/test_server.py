from __future__ import annotations

import io
import wave

import pytest

from roomcall.config import Config
from roomcall.models import Speaker, SpeakerKind
from roomcall.server import create_app

from .conftest import FakeSink


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)
    return buf.getvalue()


@pytest.fixture
def make_client(aiohttp_client, speakers: list[Speaker]):
    async def _make(config: Config | None = None, sink: FakeSink | None = None):
        sink = sink or FakeSink()
        cfg = config or Config(public_url="http://roomcall.test", rooms={"Up": ["Office"]})

        async def fake_discover(duration: float) -> list[Speaker]:
            return speakers

        app = create_app(
            cfg,
            {k: sink for k in SpeakerKind},
            fake_discover,
        )
        return await aiohttp_client(app), sink

    return _make


async def test_index_and_rooms(make_client) -> None:
    client, _ = await make_client()
    assert "Hold to talk" in await (await client.get("/")).text()
    rooms = await (await client.get("/api/rooms")).json()
    assert {"name": "Up", "speakers": ["Office"]} in rooms


async def test_talk_delivers_and_serves_clip(make_client) -> None:
    client, sink = await make_client()
    resp = await client.post(
        "/api/talk?target=Up", data=_wav(), headers={"Content-Type": "audio/wav"}
    )
    body = await resp.json()
    assert resp.status == 200 and body["ok"]
    assert sink.plays[0][0] == "Office"
    clip_path = body["clip"].removeprefix("http://roomcall.test")
    clip = await client.get(clip_path)
    assert clip.status == 200
    assert (await clip.read()).startswith(b"RIFF")


async def test_talk_unknown_room_is_404(make_client) -> None:
    client, _ = await make_client()
    resp = await client.post("/api/talk?target=attic", data=_wav())
    assert resp.status == 404


async def test_partial_failure_is_207(make_client) -> None:
    client, _ = await make_client(sink=FakeSink(fail_hosts={"10.0.0.13"}))
    resp = await client.post(
        "/api/talk?target=all", data=_wav(), headers={"Content-Type": "audio/wav"}
    )
    assert resp.status == 207


async def test_token_is_enforced_on_api_only(make_client) -> None:
    client, _ = await make_client(Config(public_url="http://x", token="s3cret"))
    assert (await client.get("/api/rooms")).status == 401
    assert (await client.get("/api/rooms", headers={"X-RoomCall-Token": "s3cret"})).status == 200
    assert (await client.get("/")).status == 200
