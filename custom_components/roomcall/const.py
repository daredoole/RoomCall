"""Constants for the RoomCall integration."""

from __future__ import annotations

DOMAIN = "roomcall"
VERSION = "0.17.0"

CONF_SPEAKERS = "speakers"
CONF_VOLUME = "volume"
CONF_TTS_ENGINE = "tts_engine"
CONF_GAIN_DB = "gain_db"
CONF_CHIME = "chime"

# Options grouped into sections in the options flow.
SECTION_CHIME = "chime_options"
CONF_CHIME_STYLE = "style"
CONF_CHIME_URL = "custom_url"

SECTION_DELIVERY = "delivery"
CONF_STAGGER_MS = "stagger_ms"
CONF_RETRY = "retry"
CONF_URGENT_VOLUME = "urgent_volume"
CONF_RESTORE = "restore"

SECTION_QUIET = "quiet_hours"
CONF_QUIET_START = "start"
CONF_QUIET_END = "end"
CONF_QUIET_VOLUME = "volume"
CONF_QUIET_SKIP_AREAS = "skip_areas"

SECTION_HOMEKIT = "homekit"
CONF_HOMEKIT_ENABLED = "enabled"
CONF_HOMEKIT_PORT = "port"
DEFAULT_HOMEKIT_PORT = 21099

SECTION_REPLY = "replies"
CONF_NOTIFY = "notify"
CONF_REPLY_PATH = "dashboard_path"
CONF_STT_ENGINE = "stt_engine"

DEFAULT_GAIN_DB = 0.0
DEFAULT_CHIME = True
DEFAULT_CHIME_STYLE = "chirp"
DEFAULT_STAGGER_MS = 250
DEFAULT_RETRY = True
DEFAULT_URGENT_VOLUME = 0.8
DEFAULT_RESTORE = True

ALL_ROOMS = "all"

PRIORITY_NORMAL = "normal"
PRIORITY_URGENT = "urgent"
PRIORITIES = (PRIORITY_NORMAL, PRIORITY_URGENT)

URL_STATIC = "/roomcall_static"
CARD_FILENAME = "roomcall-card.js"
API_TALK = "/api/roomcall/talk"
API_ROOMS = "/api/roomcall/rooms"
API_CLIP = "/api/roomcall/clip/{clip}"
API_CHIME = "/api/roomcall/chime/{style}"
API_HISTORY = "/api/roomcall/history"
API_LIVE = "/api/roomcall/live/{stream}"
API_HISTORY_CLIP = "/api/roomcall/history/{call_id}"

SERVICE_BROADCAST = "broadcast"
ATTR_TARGET_AREAS = "areas"
ATTR_TARGET_PEOPLE = "people"
ATTR_TARGET_SPEAKERS = "speakers"
ATTR_MESSAGE = "message"
ATTR_MEDIA_URL = "media_url"
ATTR_VOLUME = "volume"
ATTR_PRIORITY = "priority"

SIGNAL_CALL_FINISHED = f"{DOMAIN}_call_finished"
SIGNAL_DND_CHANGED = f"{DOMAIN}_dnd_changed"
SIGNAL_ANNOUNCEMENT = f"{DOMAIN}_announcement"

# Announcements (config subentries).
SUBENTRY_SCHEDULED = "scheduled"
SUBENTRY_TRIGGERED = "triggered"
CONF_MESSAGE = "message"
CONF_TIME = "time"
CONF_DAYS = "days"
CONF_ROOMS = "rooms"
CONF_TARGET_SPEAKERS = "target_speakers"
CONF_PEOPLE = "people"
CONF_PRIORITY = "priority"
CONF_ENTITY = "entity"
CONF_TO_STATE = "to_state"
CONF_FOR = "for"
CONF_COOLDOWN = "cooldown"
CONF_TASK = "task"
CONF_REMIND_EVERY = "remind_every"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_COOLDOWN = 60
DEFAULT_REMIND_EVERY = 15  # minutes; 0 = never repeat
DEFAULT_SNOOZE = 30  # minutes
EVENT_TASK_COMPLETED = f"{DOMAIN}_task_completed"

CLIP_TTL_SECONDS = 120
HISTORY_SIZE = 20
HISTORY_TTL_SECONDS = 24 * 3600
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
