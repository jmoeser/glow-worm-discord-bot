import re
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bot.config import TIMEZONE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns (from SPEC)
# ---------------------------------------------------------------------------

AMOUNT_PAT = r"\$?(\d+(?:\.\d{1,2})?)"
DATE_PAT = r"(?:\s+(yesterday|today|last\s+\w+))?$"

EXPENSE_RE = re.compile(
    r"^(spent|spend|buy|bought|purchase|purchased)\s+"
    + AMOUNT_PAT + r"\s+(.+?)" + DATE_PAT,
    re.IGNORECASE,
)

DEPOSIT_RE = re.compile(
    r"^(deposit|deposited|save|saved)\s+"
    + AMOUNT_PAT + r"\s+(.+?)" + DATE_PAT,
    re.IGNORECASE,
)

BILL_RE = re.compile(
    r"^paid\s+(.+?)\s+" + AMOUNT_PAT + DATE_PAT,
    re.IGNORECASE,
)

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ParseResult:
    intent: str          # "expense", "deposit", or "bill"
    amount: float
    raw_name_tokens: list[str]
    raw_date_hint: str | None
    raw_description: str | None


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def resolve_date(date_hint: str | None) -> date:
    """Resolve a raw date hint string to a concrete date in the configured timezone."""
    today = datetime.now(TIMEZONE).date()

    if date_hint is None:
        return today

    hint = date_hint.lower().strip()

    if hint == "today":
        return today

    if hint == "yesterday":
        return today - timedelta(days=1)

    if hint.startswith("last "):
        weekday_name = hint[5:].strip()
        target = _WEEKDAYS.get(weekday_name)
        if target is not None:
            days_ago = (today.weekday() - target) % 7
            if days_ago == 0:
                days_ago = 7  # "last X" when today is X means the previous week
            return today - timedelta(days=days_ago)

    logger.warning("Unrecognised date hint %r — defaulting to today", date_hint)
    return today


def parse(message: str) -> ParseResult | None:
    """Parse a Discord message into a ParseResult, or return None if it doesn't match."""
    text = message.strip()

    m = EXPENSE_RE.match(text)
    if m:
        _trigger, amount_str, name_text, date_hint = m.groups()
        return ParseResult(
            intent="expense",
            amount=float(amount_str),
            raw_name_tokens=name_text.split(),
            raw_date_hint=date_hint,
            raw_description=None,
        )

    m = DEPOSIT_RE.match(text)
    if m:
        _trigger, amount_str, name_text, date_hint = m.groups()
        return ParseResult(
            intent="deposit",
            amount=float(amount_str),
            raw_name_tokens=name_text.split(),
            raw_date_hint=date_hint,
            raw_description=None,
        )

    m = BILL_RE.match(text)
    if m:
        name_text, amount_str, date_hint = m.groups()
        return ParseResult(
            intent="bill",
            amount=float(amount_str),
            raw_name_tokens=name_text.split(),
            raw_date_hint=date_hint,
            raw_description=None,
        )

    return None
