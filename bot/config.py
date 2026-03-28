import os
import zoneinfo

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    raise RuntimeError(f"Invalid value for {name}: {value!r}. Expected true/false/1/0/yes/no.")


DISCORD_TOKEN: str = _require("DISCORD_TOKEN")
GLOWWORM_API_URL: str = _require("GLOWWORM_API_URL")
GLOWWORM_API_KEY: str = _require("GLOWWORM_API_KEY")

DISCORD_CHANNEL_ID: int = int(_require("DISCORD_CHANNEL_ID"))

_confirm_raw = os.getenv("CONFIRM_TRANSACTIONS", "true")
CONFIRM_TRANSACTIONS: bool = _parse_bool("CONFIRM_TRANSACTIONS", _confirm_raw)

_tz_name = os.getenv("TIMEZONE", "UTC")
try:
    TIMEZONE = zoneinfo.ZoneInfo(_tz_name)
except zoneinfo.ZoneInfoNotFoundError:
    raise RuntimeError(f"Unrecognised TIMEZONE: {_tz_name!r}")

# Authorisation: comma-separated Discord user IDs allowed to create transactions.
# If unset or empty, all users in the channel are permitted.
_allowed_raw = os.getenv("ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS: frozenset[int] = (
    frozenset(int(uid.strip()) for uid in _allowed_raw.split(",") if uid.strip())
    if _allowed_raw
    else frozenset()
)

# Maximum single transaction amount. Defaults to $500.
_max_amount_raw = os.getenv("MAX_TRANSACTION_AMOUNT", "500")
try:
    MAX_TRANSACTION_AMOUNT: float = float(_max_amount_raw)
    if MAX_TRANSACTION_AMOUNT <= 0:
        raise ValueError
except ValueError:
    raise RuntimeError(
        f"Invalid MAX_TRANSACTION_AMOUNT: {_max_amount_raw!r}. Must be a positive number."
    )
