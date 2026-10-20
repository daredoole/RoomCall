"""Constants for the RoomCall integration."""

from __future__ import annotations

DOMAIN = "roomcall"

CONF_SPEAKERS = "speakers"
CONF_VOLUME = "volume"
CONF_TTS_ENGINE = "tts_engine"
CONF_GAIN_DB = "gain_db"
CONF_CHIME = "chime"

DEFAULT_GAIN_DB = 0.0
DEFAULT_CHIME = True
MESSAGE_CHIME = "chirp"

ALL_ROOMS = "all"

URL_STATIC = "/roomcall_static"
CARD_FILENAME = "roomcall-card.js"
API_TALK = "/api/roomcall/talk"
API_ROOMS = "/api/roomcall/rooms"
API_CLIP = "/api/roomcall/clip/{clip}"

SERVICE_BROADCAST = "broadcast"
ATTR_TARGET_AREAS = "areas"
ATTR_MESSAGE = "message"
ATTR_MEDIA_URL = "media_url"
ATTR_VOLUME = "volume"

SIGNAL_CALL_FINISHED = f"{DOMAIN}_call_finished"

CLIP_TTL_SECONDS = 120
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
