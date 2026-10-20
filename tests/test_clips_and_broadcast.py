import time

from roomcall.broadcast import broadcast
from roomcall.clips import ClipStore
from roomcall.models import Speaker, SpeakerKind

from .conftest import FakeSink


def test_clip_store_expires_and_caps(monkeypatch) -> None:
    store = ClipStore(ttl=10, max_clips=2)
    a = store.add(b"a", "audio/mpeg", "mp3")
    store.add(b"b", "audio/mpeg", "mp3")
    store.add(b"c", "audio/mpeg", "mp3")
    assert len(store) == 2
    assert store.get(a.id) is None
    later = time.monotonic() + 11
    monkeypatch.setattr(time, "monotonic", lambda: later)
    store.prune()
    assert len(store) == 0


async def test_broadcast_reports_partial_failure(speakers: list[Speaker]) -> None:
    sink = FakeSink(fail_hosts={"10.0.0.12"})
    sinks = {SpeakerKind.SONOS: sink, SpeakerKind.AIRPLAY: sink, SpeakerKind.CAST: sink}
    result = await broadcast("all", speakers, "http://x/clip.mp3", sinks, volume=0.4)
    assert not result.ok
    failed = [r.speaker.name for r in result.results if not r.ok]
    assert failed == ["Garage HomePod"]
    assert len(sink.plays) == 3
    assert all(v == 0.4 for _, _, v in sink.plays)


async def test_broadcast_without_backend_is_reported(speakers: list[Speaker]) -> None:
    result = await broadcast("all", speakers[:1], "http://x/c.mp3", {})
    assert result.results[0].error == "no backend for sonos"
