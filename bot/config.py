import os
import zoneinfo

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _parse_bool(value: str) -> bool:
    return value.strip().lower() not in ("false", "0", "no")


DISCORD_TOKEN: str = _require("DISCORD_TOKEN")
GLOWWORM_API_URL: str = _require("GLOWWORM_API_URL")
GLOWWORM_API_KEY: str = _require("GLOWWORM_API_KEY")

DISCORD_CHANNEL_ID: int = int(_require("DISCORD_CHANNEL_ID"))

_confirm_raw = os.getenv("CONFIRM_TRANSACTIONS", "true")
CONFIRM_TRANSACTIONS: bool = _parse_bool(_confirm_raw)

_tz_name = os.getenv("TIMEZONE", "UTC")
try:
    TIMEZONE = zoneinfo.ZoneInfo(_tz_name)
except zoneinfo.ZoneInfoNotFoundError:
    raise RuntimeError(f"Unrecognised TIMEZONE: {_tz_name!r}")
