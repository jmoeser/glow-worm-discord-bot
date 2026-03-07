"""Unit tests for bot/parser.py."""

import os
from datetime import date, timedelta

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DISCORD_CHANNEL_ID", "123456")
os.environ.setdefault("GLOWWORM_API_URL", "http://localhost:8000")
os.environ.setdefault("GLOWWORM_API_KEY", "test-api-key")

import pytest

from bot.config import TIMEZONE
from bot.parser import ParseResult, parse, resolve_date

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today() -> date:
    from datetime import datetime
    return datetime.now(TIMEZONE).date()


# ===========================================================================
# parse() — expense / withdrawal trigger words
# ===========================================================================


@pytest.mark.parametrize("trigger", ["spent", "spend", "buy", "bought", "purchase", "purchased"])
def test_expense_all_trigger_words(trigger):
    result = parse(f"{trigger} $20 groceries")
    assert result is not None
    assert result.intent == "expense"
    assert result.amount == 20.0
    assert result.raw_name_tokens == ["groceries"]


# ===========================================================================
# parse() — deposit / contribution trigger words
# ===========================================================================


@pytest.mark.parametrize("trigger", ["deposit", "deposited", "save", "saved"])
def test_deposit_all_trigger_words(trigger):
    result = parse(f"{trigger} $50 holiday fund transfer")
    assert result is not None
    assert result.intent == "deposit"
    assert result.amount == 50.0
    assert result.raw_name_tokens == ["holiday", "fund", "transfer"]


# ===========================================================================
# parse() — bill trigger
# ===========================================================================


def test_bill_trigger():
    result = parse("paid electricity $120")
    assert result is not None
    assert result.intent == "bill"
    assert result.amount == 120.0
    assert result.raw_name_tokens == ["electricity"]


def test_bill_trigger_multi_word_name():
    result = parse("paid city water 98.50")
    assert result is not None
    assert result.intent == "bill"
    assert result.amount == 98.50
    assert result.raw_name_tokens == ["city", "water"]


# ===========================================================================
# parse() — dollar sign optional
# ===========================================================================


def test_expense_with_dollar_sign():
    result = parse("spent $20 groceries")
    assert result is not None
    assert result.amount == 20.0


def test_expense_without_dollar_sign():
    result = parse("spent 20 groceries")
    assert result is not None
    assert result.amount == 20.0


def test_deposit_with_dollar_sign():
    result = parse("deposit $100 holiday fund transfer")
    assert result is not None
    assert result.amount == 100.0


def test_deposit_without_dollar_sign():
    result = parse("deposit 100 holiday fund transfer")
    assert result is not None
    assert result.amount == 100.0


# ===========================================================================
# parse() — decimal amounts
# ===========================================================================


def test_expense_decimal_amount():
    result = parse("buy $4.50 coffee")
    assert result is not None
    assert result.amount == 4.50


def test_expense_decimal_one_place():
    result = parse("spent 9.5 lunch")
    assert result is not None
    assert result.amount == 9.5


def test_bill_decimal_amount():
    result = parse("paid electricity $98.75")
    assert result is not None
    assert result.amount == 98.75


# ===========================================================================
# parse() — date hints
# ===========================================================================


def test_expense_no_date_hint():
    result = parse("spent $20 groceries")
    assert result is not None
    assert result.raw_date_hint is None


def test_expense_date_hint_yesterday():
    result = parse("bought 12 lunch yesterday")
    assert result is not None
    assert result.raw_date_hint == "yesterday"
    assert result.raw_name_tokens == ["lunch"]


def test_expense_date_hint_today():
    result = parse("spent $20 groceries today")
    assert result is not None
    assert result.raw_date_hint == "today"
    assert result.raw_name_tokens == ["groceries"]


def test_expense_date_hint_last_friday():
    result = parse("purchased $99 electronics last friday")
    assert result is not None
    assert result.raw_date_hint == "last friday"
    assert result.raw_name_tokens == ["electronics"]


def test_bill_date_hint_yesterday():
    result = parse("paid electricity $120 yesterday")
    assert result is not None
    assert result.raw_date_hint == "yesterday"


def test_deposit_date_hint_last_monday():
    result = parse("saved 25 emergency fund transfer last monday")
    assert result is not None
    assert result.raw_date_hint == "last monday"
    assert result.raw_name_tokens == ["emergency", "fund", "transfer"]


# ===========================================================================
# parse() — multi-word name tokens
# ===========================================================================


def test_expense_multi_word_name_tokens():
    result = parse("spent $15 short term savings groceries")
    assert result is not None
    assert result.raw_name_tokens == ["short", "term", "savings", "groceries"]


def test_expense_multi_word_name_tokens_with_date():
    result = parse("spend 50 holiday fund electronics yesterday")
    assert result is not None
    assert result.raw_name_tokens == ["holiday", "fund", "electronics"]
    assert result.raw_date_hint == "yesterday"


# ===========================================================================
# parse() — case insensitivity
# ===========================================================================


def test_expense_trigger_uppercase():
    result = parse("SPENT $20 groceries")
    assert result is not None
    assert result.intent == "expense"


def test_expense_mixed_case():
    result = parse("Bought $5.00 Coffee")
    assert result is not None
    assert result.amount == 5.0


# ===========================================================================
# parse() — non-matching messages (should return None)
# ===========================================================================


def test_non_matching_empty():
    assert parse("") is None


def test_non_matching_random_text():
    assert parse("hello world") is None


def test_non_matching_command_prefix():
    assert parse("!spent $20 groceries") is None


def test_non_matching_slash_command():
    assert parse("/pay electricity $120") is None


def test_non_matching_no_amount():
    # "spent groceries" — no amount present
    assert parse("spent groceries") is None


def test_non_matching_question():
    assert parse("how much is left in groceries?") is None


def test_non_matching_paid_no_amount():
    # "paid electricity" with no amount
    assert parse("paid electricity") is None


# ===========================================================================
# resolve_date()
# ===========================================================================


def test_resolve_date_none_returns_today():
    today = _today()
    assert resolve_date(None) == today


def test_resolve_date_today():
    today = _today()
    assert resolve_date("today") == today


def test_resolve_date_yesterday():
    today = _today()
    assert resolve_date("yesterday") == today - timedelta(days=1)


@pytest.mark.parametrize("weekday_name,weekday_index", [
    ("monday", 0),
    ("tuesday", 1),
    ("wednesday", 2),
    ("thursday", 3),
    ("friday", 4),
    ("saturday", 5),
    ("sunday", 6),
])
def test_resolve_date_last_weekday(weekday_name, weekday_index):
    today = _today()
    result = resolve_date(f"last {weekday_name}")
    # Result must be in the past
    assert result < today
    # Result must be the correct weekday
    assert result.weekday() == weekday_index
    # Result must be within the last 7 days
    assert (today - result).days <= 7


def test_resolve_date_last_weekday_not_today():
    """'last <weekday>' when today is that weekday should return 7 days ago."""
    today = _today()
    weekday_name = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][today.weekday()]
    result = resolve_date(f"last {weekday_name}")
    assert result == today - timedelta(days=7)


def test_resolve_date_unknown_hint_returns_today():
    """An unrecognised date hint should fall back to today."""
    today = _today()
    assert resolve_date("next week") == today


# ===========================================================================
# parse() — ParseResult fields
# ===========================================================================


def test_parse_result_description_is_none():
    """Parser does not set description — resolver does that."""
    result = parse("spent $20 groceries")
    assert result.raw_description is None


def test_parse_result_expense_fields():
    result = parse("spent $20.50 groceries yesterday")
    assert result == ParseResult(
        intent="expense",
        amount=20.50,
        raw_name_tokens=["groceries"],
        raw_date_hint="yesterday",
        raw_description=None,
    )


def test_parse_result_deposit_fields():
    result = parse("deposit $30 short term savings transfer")
    assert result == ParseResult(
        intent="deposit",
        amount=30.0,
        raw_name_tokens=["short", "term", "savings", "transfer"],
        raw_date_hint=None,
        raw_description=None,
    )


def test_parse_result_bill_fields():
    result = parse("paid electricity $120 yesterday")
    assert result == ParseResult(
        intent="bill",
        amount=120.0,
        raw_name_tokens=["electricity"],
        raw_date_hint="yesterday",
        raw_description=None,
    )
