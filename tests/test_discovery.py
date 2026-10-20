from roomcall.discovery import merge_speakers, speaker_from_service
from roomcall.models import SpeakerKind


def test_sonos_record() -> None:
    s = speaker_from_service(
        "_sonos._tcp.local.",
        "Sonos-542A1B2C3D4E@Basement._sonos._tcp.local.",
        ["fe80::1", "192.168.1.50"],
        1443,
        {b"mdl": b"One", b"hhid": b"Sonos_abc"},
    )
    assert s is not None
    assert s.kind is SpeakerKind.SONOS
    assert s.name == "Basement"
    assert s.host == "192.168.1.50"
    assert s.model == "One"
    assert s.id == "sonos:Sonos_abc"


def test_raop_name_strips_mac_prefix() -> None:
    s = speaker_from_service(
        "_raop._tcp.local.",
        "AABBCCDDEEFF@Garage HomePod._raop._tcp.local.",
        ["192.168.1.60"],
        7000,
        {b"am": b"AudioAccessory5,1"},
    )
    assert s is not None
    assert s.name == "Garage HomePod"
    assert s.model == "AudioAccessory5,1"


def test_cast_uses_friendly_name_from_txt() -> None:
    s = speaker_from_service(
        "_googlecast._tcp.local.",
        "Google-Nest-Hub-1234._googlecast._tcp.local.",
        ["192.168.1.70"],
        8009,
        {b"fn": b"Kitchen Hub", b"md": b"Google Nest Hub", b"id": b"abc123"},
    )
    assert s is not None
    assert s.name == "Kitchen Hub"
    assert s.id == "cast:abc123"


def test_ipv6_only_and_unknown_types_are_ignored() -> None:
    assert speaker_from_service("_sonos._tcp.local.", "x", ["fe80::1"], 1, {}) is None
    assert speaker_from_service("_http._tcp.local.", "x", ["10.0.0.1"], 1, {}) is None


def test_merge_prefers_native_sonos_over_airplay_on_same_host() -> None:
    sonos = speaker_from_service("_sonos._tcp.local.", "Basement", ["10.0.0.5"], 1443, {})
    airplay = speaker_from_service("_airplay._tcp.local.", "Basement", ["10.0.0.5"], 7000, {})
    assert sonos is not None and airplay is not None
    merged = merge_speakers([airplay, sonos])
    assert [s.kind for s in merged] == [SpeakerKind.SONOS]
