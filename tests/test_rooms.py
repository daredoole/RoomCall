from roomcall.models import Speaker
from roomcall.rooms import RoomMap


def test_all_returns_every_speaker(speakers: list[Speaker]) -> None:
    assert len(RoomMap().resolve("all", speakers)) == 4


def test_configured_room_groups_speakers(speakers: list[Speaker]) -> None:
    rooms = RoomMap.from_config({"Downstairs": ["basement", "kitchen-hub"]})
    names = {s.name for s in rooms.resolve("downstairs", speakers)}
    assert names == {"Basement", "Kitchen Hub"}


def test_unassigned_speaker_is_its_own_room(speakers: list[Speaker]) -> None:
    rooms = RoomMap.from_config({"Downstairs": ["Basement"]})
    assert [s.name for s in rooms.resolve("Garage HomePod", speakers)] == ["Garage HomePod"]
    assert "Garage HomePod" in rooms.room_names(speakers)


def test_unknown_target_resolves_to_nothing(speakers: list[Speaker]) -> None:
    assert RoomMap().resolve("attic", speakers) == []
