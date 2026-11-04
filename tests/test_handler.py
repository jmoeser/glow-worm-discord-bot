"""Tests for bot/handler.py."""

import os

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DISCORD_CHANNEL_ID", "123456")
os.environ.setdefault("GLOWWORM_API_URL", "http://localhost:8000")
os.environ.setdefault("GLOWWORM_API_KEY", "test-api-key")

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import httpx
import pytest

from bot.client import APIError
from bot.handler import (
    _amount_str,
    _build_payload,
    _build_success_embed,
    _error_text,
    handle,
)
from bot.parser import ParseResult, resolve_date
from bot.resolver import ResolveResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(content: str, author_id: int = 1) -> MagicMock:
    """Create a minimal discord.Message mock."""
    author = MagicMock()
    author.id = author_id

    channel = AsyncMock()

    msg = MagicMock()
    msg.content = content
    msg.author = author
    msg.channel = channel
    return msg


def _make_http_client() -> AsyncMock:
    client = AsyncMock()
    client.create_transaction = AsyncMock(return_value={"id": 42})
    client.get_budgets = AsyncMock(
        return_value=[{"id": 10, "allocated_amount": 200.0, "spent_amount": 55.0}]
    )
    client.get_sinking_fund = AsyncMock(return_value={"id": 5, "current_balance": 430.0})
    client.get_due_payments = AsyncMock(return_value=[])
    client.confirm_bill = AsyncMock(return_value={"message": "Confirmed."})
    client.skip_bill = AsyncMock(return_value={"message": "Skipped."})
    return client


def _make_bot() -> AsyncMock:
    bot = AsyncMock(spec=discord.Client)
    return bot


CATEGORIES = [
    {"id": 1, "name": "Groceries", "is_budget_category": True},
    {"id": 2, "name": "Entertainment", "is_budget_category": False},
    {"id": 3, "name": "Transfer", "is_budget_category": False},
]
SINKING_FUNDS = [
    {"id": 5, "name": "Short Term Savings"},
    {"id": 6, "name": "Holiday Fund"},
]
BILLS = [
    {"id": 20, "name": "Electricity", "category_id": 2, "bill_type": "variable"},
]


# ---------------------------------------------------------------------------
# _build_payload
# ---------------------------------------------------------------------------


def test_build_payload_budget_expense():
    result = ParseResult(
        intent="expense",
        amount=20.0,
        raw_name_tokens=["groceries"],
        raw_date_hint=None,
        raw_description=None,
    )
    resolved = ResolveResult(
        transaction_type="budget_expense",
        category_id=1,
        budget_id=10,
        description="chemist",
    )
    today = date(2026, 3, 7)
    payload = _build_payload(result, resolved, today)

    assert payload["date"] == "2026-03-07"
    assert payload["amount"] == 20.0
    assert payload["type"] == "expense"
    assert payload["transaction_type"] == "budget_expense"
    assert payload["budget_id"] == 10
    assert payload["category_id"] == 1
    assert payload["description"] == "chemist"


def test_build_payload_contribution():
    result = ParseResult(
        intent="deposit",
        amount=50.0,
        raw_name_tokens=["short", "term", "savings", "transfer"],
        raw_date_hint=None,
        raw_description=None,
    )
    resolved = ResolveResult(
        transaction_type="contribution",
        category_id=3,
        sinking_fund_id=5,
    )
    today = date(2026, 3, 7)
    payload = _build_payload(result, resolved, today)

    assert payload["type"] == "income"
    assert payload["transaction_type"] == "contribution"
    assert payload["sinking_fund_id"] == 5


def test_build_payload_bill():
    result = ParseResult(
        intent="bill",
        amount=120.0,
        raw_name_tokens=["electricity"],
        raw_date_hint=None,
        raw_description=None,
    )
    resolved = ResolveResult(
        transaction_type="regular",
        category_id=2,
        bill_id=20,
    )
    today = date(2026, 3, 7)
    payload = _build_payload(result, resolved, today)

    assert payload["type"] == "expense"
    assert payload["recurring_bill_id"] == 20
    assert "budget_id" not in payload
    assert "sinking_fund_id" not in payload


# ---------------------------------------------------------------------------
# _error_text
# ---------------------------------------------------------------------------


def test_error_text_no_match_expense_adds_url():
    resolved = ResolveResult(error="no_match", error_message="I couldn't find a match.")
    result = ParseResult("expense", 10.0, ["xyz"], None, None)
    with patch("bot.handler.config") as mock_cfg:
        mock_cfg.GLOWWORM_API_URL = "http://glow-worm:8000"
        text = _error_text(resolved, result)
    assert "http://glow-worm:8000" in text
    assert "I couldn't find a match." in text


def test_error_text_no_match_bill_no_url():
    resolved = ResolveResult(error="no_match", error_message="No bill found.")
    result = ParseResult("bill", 10.0, ["xyz"], None, None)
    with patch("bot.handler.config") as mock_cfg:
        mock_cfg.GLOWWORM_API_URL = "http://glow-worm:8000"
        text = _error_text(resolved, result)
    assert "http://glow-worm:8000" not in text
    assert "No bill found." in text


def test_error_text_ambiguous():
    resolved = ResolveResult(error="ambiguous", error_message="Multiple matches: A, B.")
    result = ParseResult("expense", 10.0, ["a"], None, None)
    text = _error_text(resolved, result)
    assert text == "Multiple matches: A, B."


# ---------------------------------------------------------------------------
# handle — non-matching message (silent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_non_matching_message_is_silent():
    msg = _make_message("hello world")
    http_client = _make_http_client()
    bot = _make_bot()

    await handle(msg, http_client, bot)

    msg.channel.send.assert_not_called()
    http_client.create_transaction.assert_not_called()


# ---------------------------------------------------------------------------
# handle — resolver errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_resolver_error_sends_message():
    msg = _make_message("spent $20 unknownxyz")
    http_client = _make_http_client()
    bot = _make_bot()

    error_result = ResolveResult(
        error="no_match",
        error_message="I couldn't find a category or fund matching 'unknownxyz'.",
    )

    with (
        patch("bot.handler.resolve_expense", return_value=error_result),
        patch("bot.handler.config") as mock_cfg,
    ):
        mock_cfg.GLOWWORM_API_URL = "http://glow-worm:8000"
        mock_cfg.CONFIRM_TRANSACTIONS = False
        await handle(msg, http_client, bot)

    msg.channel.send.assert_called_once()
    sent_text = msg.channel.send.call_args[0][0]
    assert "I couldn't find" in sent_text
    http_client.create_transaction.assert_not_called()


# ---------------------------------------------------------------------------
# handle — auto-commit (CONFIRM_TRANSACTIONS=False)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_auto_commit_budget_expense():
    msg = _make_message("spent $20 groceries")
    http_client = _make_http_client()
    bot = _make_bot()

    resolved = ResolveResult(
        transaction_type="budget_expense",
        category_id=1,
        budget_id=10,
    )

    with (
        patch("bot.handler.resolve_expense", return_value=resolved),
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.cache.get_sinking_funds", return_value=SINKING_FUNDS),
        patch("bot.handler.cache.get_bills", return_value=BILLS),
        patch("bot.handler.config") as mock_cfg,
    ):
        mock_cfg.CONFIRM_TRANSACTIONS = False
        await handle(msg, http_client, bot)

    http_client.create_transaction.assert_called_once()
    payload = http_client.create_transaction.call_args[0][0]
    assert payload["transaction_type"] == "budget_expense"
    assert payload["budget_id"] == 10
    assert payload["amount"] == 20.0

    msg.channel.send.assert_called_once()
    embed_arg = msg.channel.send.call_args[1]["embed"]
    assert "Groceries" in embed_arg.description
    assert "Budget remaining" in embed_arg.description


@pytest.mark.asyncio
async def test_handle_auto_commit_regular_expense():
    msg = _make_message("spent $15 entertainment")
    http_client = _make_http_client()
    bot = _make_bot()

    resolved = ResolveResult(
        transaction_type="regular",
        category_id=2,
    )

    with (
        patch("bot.handler.resolve_expense", return_value=resolved),
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.cache.get_sinking_funds", return_value=SINKING_FUNDS),
        patch("bot.handler.cache.get_bills", return_value=BILLS),
        patch("bot.handler.config") as mock_cfg,
    ):
        mock_cfg.CONFIRM_TRANSACTIONS = False
        await handle(msg, http_client, bot)

    http_client.create_transaction.assert_called_once()
    payload = http_client.create_transaction.call_args[0][0]
    assert payload["transaction_type"] == "regular"
    assert "budget_id" not in payload

    msg.channel.send.assert_called_once()
    embed_arg = msg.channel.send.call_args[1]["embed"]
    assert "Entertainment" in embed_arg.description


@pytest.mark.asyncio
async def test_handle_auto_commit_withdrawal():
    msg = _make_message("spent $50 short term savings groceries")
    http_client = _make_http_client()
    bot = _make_bot()

    resolved = ResolveResult(
        transaction_type="withdrawal",
        category_id=1,
        sinking_fund_id=5,
    )

    with (
        patch("bot.handler.resolve_expense", return_value=resolved),
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.cache.get_sinking_funds", return_value=SINKING_FUNDS),
        patch("bot.handler.cache.get_bills", return_value=BILLS),
        patch("bot.handler.config") as mock_cfg,
    ):
        mock_cfg.CONFIRM_TRANSACTIONS = False
        await handle(msg, http_client, bot)

    http_client.create_transaction.assert_called_once()
    payload = http_client.create_transaction.call_args[0][0]
    assert payload["transaction_type"] == "withdrawal"
    assert payload["sinking_fund_id"] == 5

    http_client.get_sinking_fund.assert_called_once_with(5)
    embed_arg = msg.channel.send.call_args[1]["embed"]
    assert "Withdrew" in embed_arg.description
    assert "430.00" in embed_arg.description


@pytest.mark.asyncio
async def test_handle_auto_commit_contribution():
    msg = _make_message("deposit $30 short term savings transfer")
    http_client = _make_http_client()
    bot = _make_bot()

    resolved = ResolveResult(
        transaction_type="contribution",
        category_id=3,
        sinking_fund_id=5,
    )

    with (
        patch("bot.handler.resolve_deposit", return_value=resolved),
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.cache.get_sinking_funds", return_value=SINKING_FUNDS),
        patch("bot.handler.cache.get_bills", return_value=BILLS),
        patch("bot.handler.config") as mock_cfg,
    ):
        mock_cfg.CONFIRM_TRANSACTIONS = False
        await handle(msg, http_client, bot)

    payload = http_client.create_transaction.call_args[0][0]
    assert payload["type"] == "income"
    assert payload["transaction_type"] == "contribution"

    embed_arg = msg.channel.send.call_args[1]["embed"]
    assert "Deposited" in embed_arg.description
    assert "430.00" in embed_arg.description


@pytest.mark.asyncio
async def test_handle_auto_commit_bill_payment():
    msg = _make_message("paid electricity $120")
    http_client = _make_http_client()
    bot = _make_bot()

    resolved = ResolveResult(
        transaction_type="regular",
        category_id=2,
        bill_id=20,
    )

    with (
        patch("bot.handler.resolve_bill", return_value=resolved),
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.cache.get_sinking_funds", return_value=SINKING_FUNDS),
        patch("bot.handler.cache.get_bills", return_value=BILLS),
        patch("bot.handler.config") as mock_cfg,
    ):
        mock_cfg.CONFIRM_TRANSACTIONS = False
        await handle(msg, http_client, bot)

    payload = http_client.create_transaction.call_args[0][0]
    assert payload["recurring_bill_id"] == 20

    embed_arg = msg.channel.send.call_args[1]["embed"]
    assert "Paid" in embed_arg.description
    assert "Electricity" in embed_arg.description


# ---------------------------------------------------------------------------
# handle — confirmation flow (CONFIRM_TRANSACTIONS=True)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_confirm_flow_user_confirms():
    msg = _make_message("spent $20 groceries")
    http_client = _make_http_client()
    bot = _make_bot()

    preview_msg = AsyncMock()
    preview_msg.id = 999
    msg.channel.send = AsyncMock(side_effect=[preview_msg, AsyncMock()])

    mock_reaction = MagicMock()
    mock_reaction.emoji = "\u2705"
    mock_reaction.message.id = 999
    bot.wait_for = AsyncMock(return_value=(mock_reaction, msg.author))

    resolved = ResolveResult(
        transaction_type="budget_expense",
        category_id=1,
        budget_id=10,
    )

    with (
        patch("bot.handler.resolve_expense", return_value=resolved),
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.cache.get_sinking_funds", return_value=SINKING_FUNDS),
        patch("bot.handler.cache.get_bills", return_value=BILLS),
        patch("bot.handler.config") as mock_cfg,
    ):
        mock_cfg.CONFIRM_TRANSACTIONS = True
        await handle(msg, http_client, bot)

    bot.wait_for.assert_called_once()
    http_client.create_transaction.assert_called_once()
    # Two sends: preview embed + success embed
    assert msg.channel.send.call_count == 2


@pytest.mark.asyncio
async def test_handle_confirm_flow_user_cancels():
    msg = _make_message("spent $20 groceries")
    http_client = _make_http_client()
    bot = _make_bot()

    preview_msg = AsyncMock()
    preview_msg.id = 999
    cancel_send = AsyncMock()
    msg.channel.send = AsyncMock(side_effect=[preview_msg, cancel_send])

    mock_reaction = MagicMock()
    mock_reaction.emoji = "\u274c"
    mock_reaction.message.id = 999
    bot.wait_for = AsyncMock(return_value=(mock_reaction, msg.author))

    resolved = ResolveResult(
        transaction_type="budget_expense",
        category_id=1,
        budget_id=10,
    )

    with (
        patch("bot.handler.resolve_expense", return_value=resolved),
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.cache.get_sinking_funds", return_value=SINKING_FUNDS),
        patch("bot.handler.cache.get_bills", return_value=BILLS),
        patch("bot.handler.config") as mock_cfg,
    ):
        mock_cfg.CONFIRM_TRANSACTIONS = True
        await handle(msg, http_client, bot)

    http_client.create_transaction.assert_not_called()
    assert msg.channel.send.call_count == 2
    cancel_text = msg.channel.send.call_args_list[1][0][0]
    assert cancel_text == "Cancelled."


@pytest.mark.asyncio
async def test_handle_confirm_flow_timeout():
    msg = _make_message("spent $20 groceries")
    http_client = _make_http_client()
    bot = _make_bot()

    preview_msg = AsyncMock()
    preview_msg.id = 999
    cancel_send = AsyncMock()
    msg.channel.send = AsyncMock(side_effect=[preview_msg, cancel_send])

    bot.wait_for = AsyncMock(side_effect=TimeoutError())

    resolved = ResolveResult(
        transaction_type="budget_expense",
        category_id=1,
        budget_id=10,
    )

    with (
        patch("bot.handler.resolve_expense", return_value=resolved),
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.cache.get_sinking_funds", return_value=SINKING_FUNDS),
        patch("bot.handler.cache.get_bills", return_value=BILLS),
        patch("bot.handler.config") as mock_cfg,
    ):
        mock_cfg.CONFIRM_TRANSACTIONS = True
        await handle(msg, http_client, bot)

    http_client.create_transaction.assert_not_called()
    cancel_text = msg.channel.send.call_args_list[1][0][0]
    assert cancel_text == "Cancelled."


# ---------------------------------------------------------------------------
# handle — API / network errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_api_error_sends_message():
    msg = _make_message("spent $20 groceries")
    http_client = _make_http_client()
    http_client.create_transaction = AsyncMock(side_effect=APIError(500, "Internal Server Error"))
    bot = _make_bot()

    resolved = ResolveResult(
        transaction_type="budget_expense",
        category_id=1,
        budget_id=10,
    )

    with (
        patch("bot.handler.resolve_expense", return_value=resolved),
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.cache.get_sinking_funds", return_value=SINKING_FUNDS),
        patch("bot.handler.cache.get_bills", return_value=BILLS),
        patch("bot.handler.config") as mock_cfg,
    ):
        mock_cfg.CONFIRM_TRANSACTIONS = False
        await handle(msg, http_client, bot)

    msg.channel.send.assert_called_once()
    sent = msg.channel.send.call_args[0][0]
    assert "HTTP 500" in sent
    assert "glow-worm" in sent


@pytest.mark.asyncio
async def test_handle_network_error_sends_message():
    msg = _make_message("spent $20 groceries")
    http_client = _make_http_client()
    http_client.create_transaction = AsyncMock(side_effect=httpx.RequestError("Connection refused"))
    bot = _make_bot()

    resolved = ResolveResult(
        transaction_type="budget_expense",
        category_id=1,
        budget_id=10,
    )

    with (
        patch("bot.handler.resolve_expense", return_value=resolved),
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.cache.get_sinking_funds", return_value=SINKING_FUNDS),
        patch("bot.handler.cache.get_bills", return_value=BILLS),
        patch("bot.handler.config") as mock_cfg,
    ):
        mock_cfg.CONFIRM_TRANSACTIONS = False
        await handle(msg, http_client, bot)

    msg.channel.send.assert_called_once()
    sent = msg.channel.send.call_args[0][0]
    assert "reach glow-worm" in sent


# ---------------------------------------------------------------------------
# handle — amount limit (fix 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_amount_exceeds_limit_sends_error():
    msg = _make_message("spent $999 groceries")
    http_client = _make_http_client()
    bot = _make_bot()

    with patch("bot.handler.MAX_TRANSACTION_AMOUNT", 500.0):
        await handle(msg, http_client, bot)

    msg.channel.send.assert_called_once()
    sent = msg.channel.send.call_args[0][0]
    assert "999.00" in sent
    assert "500.00" in sent
    http_client.create_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_handle_amount_at_limit_is_accepted():
    msg = _make_message("spent $500 groceries")
    http_client = _make_http_client()
    bot = _make_bot()

    resolved = ResolveResult(transaction_type="budget_expense", category_id=1, budget_id=10)

    with (
        patch("bot.handler.MAX_TRANSACTION_AMOUNT", 500.0),
        patch("bot.handler.resolve_expense", return_value=resolved),
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.cache.get_sinking_funds", return_value=SINKING_FUNDS),
        patch("bot.handler.cache.get_bills", return_value=BILLS),
        patch("bot.handler.config") as mock_cfg,
    ):
        mock_cfg.CONFIRM_TRANSACTIONS = False
        await handle(msg, http_client, bot)

    http_client.create_transaction.assert_called_once()


# ---------------------------------------------------------------------------
# _build_payload — description truncation (fix 7)
# ---------------------------------------------------------------------------


def test_build_payload_description_truncated():
    result = ParseResult(
        intent="expense",
        amount=20.0,
        raw_name_tokens=["groceries"],
        raw_date_hint=None,
        raw_description=None,
    )
    long_desc = "x" * 600
    resolved = ResolveResult(
        transaction_type="regular",
        category_id=2,
        description=long_desc,
    )
    today = date(2026, 3, 7)
    payload = _build_payload(result, resolved, today)

    assert len(payload["description"]) == 500  # type: ignore[arg-type]
    assert payload["description"] == long_desc[:500]


def test_build_payload_description_within_limit_unchanged():
    result = ParseResult(
        intent="expense",
        amount=20.0,
        raw_name_tokens=["groceries"],
        raw_date_hint=None,
        raw_description=None,
    )
    resolved = ResolveResult(
        transaction_type="regular",
        category_id=2,
        description="chemist",
    )
    today = date(2026, 3, 7)
    payload = _build_payload(result, resolved, today)

    assert payload["description"] == "chemist"


# ---------------------------------------------------------------------------
# _confirm_flow — duplicate user guard (fix 10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_confirm_flow_blocks_duplicate_user():
    """A second matching message from the same user while a confirmation is pending is rejected."""
    import bot.handler as handler_module

    msg = _make_message("spent $20 groceries", author_id=7)
    http_client = _make_http_client()
    bot = _make_bot()

    resolved = ResolveResult(transaction_type="budget_expense", category_id=1, budget_id=10)

    handler_module._pending_confirmations.add(7)
    try:
        with (
            patch("bot.handler.resolve_expense", return_value=resolved),
            patch("bot.handler.config") as mock_cfg,
        ):
            mock_cfg.CONFIRM_TRANSACTIONS = True
            await handle(msg, http_client, bot)

        msg.channel.send.assert_called_once()
        sent = msg.channel.send.call_args[0][0]
        assert "confirmation" in sent.lower()
        http_client.create_transaction.assert_not_called()
    finally:
        handler_module._pending_confirmations.discard(7)


# ---------------------------------------------------------------------------
# _build_success_embed — assert-to-guard fixes (fix 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_success_embed_missing_category_id_for_budget_expense():
    """Should return a fallback embed rather than crashing when category_id is None."""
    result = ParseResult(
        intent="expense", amount=20.0, raw_name_tokens=[], raw_date_hint=None, raw_description=None
    )
    resolved = ResolveResult(
        transaction_type="budget_expense",
        category_id=None,
        budget_id=10,
    )
    payload = {
        "date": "2026-03-07",
        "amount": 20.0,
        "description": None,
        "category_id": None,
        "type": "expense",
        "transaction_type": "budget_expense",
    }
    http_client = _make_http_client()

    with patch("bot.handler.cache.get_categories", return_value=CATEGORIES):
        embed = await _build_success_embed(http_client, result, resolved, payload)

    assert embed.description is not None
    assert "\u2705" in embed.description
    http_client.get_budgets.assert_not_called()


@pytest.mark.asyncio
async def test_build_success_embed_missing_sinking_fund_id_for_withdrawal():
    """Should return a fallback embed rather than crashing when sinking_fund_id is None."""
    result = ParseResult(
        intent="expense", amount=50.0, raw_name_tokens=[], raw_date_hint=None, raw_description=None
    )
    resolved = ResolveResult(
        transaction_type="withdrawal",
        category_id=1,
        sinking_fund_id=None,
    )
    payload = {
        "date": "2026-03-07",
        "amount": 50.0,
        "description": None,
        "category_id": 1,
        "type": "expense",
        "transaction_type": "withdrawal",
    }
    http_client = _make_http_client()

    with (
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.cache.get_sinking_funds", return_value=SINKING_FUNDS),
    ):
        embed = await _build_success_embed(http_client, result, resolved, payload)

    assert embed.description is not None
    assert "\u2705" in embed.description
    http_client.get_sinking_fund.assert_not_called()


@pytest.mark.asyncio
async def test_build_success_embed_missing_sinking_fund_id_for_contribution():
    """Should return a fallback embed rather than crashing when sinking_fund_id is None."""
    result = ParseResult(
        intent="deposit", amount=30.0, raw_name_tokens=[], raw_date_hint=None, raw_description=None
    )
    resolved = ResolveResult(
        transaction_type="contribution",
        category_id=3,
        sinking_fund_id=None,
    )
    payload = {
        "date": "2026-03-07",
        "amount": 30.0,
        "description": None,
        "category_id": 3,
        "type": "income",
        "transaction_type": "contribution",
    }
    http_client = _make_http_client()

    with (
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.cache.get_sinking_funds", return_value=SINKING_FUNDS),
    ):
        embed = await _build_success_embed(http_client, result, resolved, payload)

    assert embed.description is not None
    assert "\u2705" in embed.description
    http_client.get_sinking_fund.assert_not_called()


# ---------------------------------------------------------------------------
# Due payments — paid / skip / due
# ---------------------------------------------------------------------------


def _due(**overrides: object) -> dict:
    row: dict = {
        "bill_id": 31,
        "name": "HelloFresh",
        "provider": "HelloFresh",
        "expected_amount": "89.95",
        "due_date": "2026-10-26",
        "more_due": 0,
        "paid_from": "budget",
        "paid_from_label": "Groceries budget",
        "budget_month": "October 2026",
        "problem": None,
    }
    row.update(overrides)
    return row


def test_amount_str_quantizes_to_two_places():
    assert _amount_str(89.95) == "89.95"
    assert _amount_str("89.95") == "89.95"
    assert _amount_str("10") == "10.00"
    assert _amount_str(0) is None
    assert _amount_str("nope") is None


@pytest.mark.asyncio
async def test_paid_confirms_single_due_payment_and_shows_glowworm_message():
    msg = _make_message("paid hellofresh $92.40")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(return_value=[_due()])
    http_client.confirm_bill = AsyncMock(
        return_value={"message": "Logged $92.40 from Groceries budget (October 2026)."}
    )
    bot = _make_bot()
    bot.wait_for = AsyncMock(side_effect=AssertionError("should not ask for a reaction"))

    with patch("bot.handler.config") as mock_cfg:
        mock_cfg.CONFIRM_TRANSACTIONS = True
        await handle(msg, http_client, bot)

    http_client.confirm_bill.assert_called_once_with(31, "92.40", "2026-10-26")
    http_client.create_transaction.assert_not_called()
    bot.wait_for.assert_not_called()
    assert msg.channel.send.call_args[0][0] == (
        "Logged $92.40 from Groceries budget (October 2026)."
    )


@pytest.mark.asyncio
async def test_paid_without_amount_uses_expected_amount():
    msg = _make_message("paid hellofresh")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(return_value=[_due(expected_amount=89.95)])
    http_client.confirm_bill = AsyncMock(
        return_value={"message": "Logged to October Groceries; $89.95 taken back from Overflow."}
    )
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.confirm_bill.assert_called_once_with(31, "89.95", "2026-10-26")
    http_client.create_transaction.assert_not_called()
    assert msg.channel.send.call_args[0][0] == (
        "Logged to October Groceries; $89.95 taken back from Overflow."
    )


@pytest.mark.asyncio
async def test_paid_exact_name_beats_longer_prefix():
    msg = _make_message("paid electricity $40")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(
        return_value=[
            _due(
                bill_id=20,
                name="Electricity",
                paid_from="bills_fund",
                paid_from_label="Bills fund",
            ),
            _due(bill_id=21, name="Electricity Plus"),
        ]
    )
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.confirm_bill.assert_called_once_with(20, "40.00", "2026-10-26")


@pytest.mark.asyncio
async def test_paid_partial_name_confirms_unique_due_payment():
    msg = _make_message("paid gas $18.5")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(
        return_value=[
            _due(bill_id=4, name="Gas Bill", due_date="2026-10-15", paid_from_label="Bills fund"),
            _due(bill_id=5, name="Water"),
        ]
    )
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.confirm_bill.assert_called_once_with(4, "18.50", "2026-10-15")


@pytest.mark.asyncio
async def test_paid_several_due_matches_asks_which():
    msg = _make_message("paid bill $30")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(
        return_value=[
            _due(bill_id=4, name="Gas Bill", paid_from_label="Bills fund", due_date="2026-10-15"),
            _due(bill_id=5, name="Water Bill", paid_from_label="Bills fund", due_date="2026-10-20"),
        ]
    )
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.confirm_bill.assert_not_called()
    http_client.create_transaction.assert_not_called()
    sent = msg.channel.send.call_args[0][0]
    assert sent == (
        "I found multiple matches for 'bill': "
        "Gas Bill (Bills fund, due 15 Oct 2026), "
        "Water Bill (Bills fund, due 20 Oct 2026). Which did you mean?"
    )


@pytest.mark.asyncio
async def test_paid_with_no_due_match_logs_variable_bill():
    msg = _make_message("paid electricity $120 yesterday")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(return_value=[_due()])
    bot = _make_bot()

    with (
        patch("bot.handler.cache.get_bills", return_value=BILLS),
        patch("bot.handler.cache.get_categories", return_value=CATEGORIES),
        patch("bot.handler.config") as mock_cfg,
    ):
        mock_cfg.CONFIRM_TRANSACTIONS = False
        await handle(msg, http_client, bot)

    http_client.confirm_bill.assert_not_called()
    payload = http_client.create_transaction.call_args[0][0]
    assert payload["recurring_bill_id"] == 20
    assert payload["amount"] == 120.0
    assert payload["transaction_type"] == "regular"
    assert payload["date"] == resolve_date("yesterday").isoformat()


@pytest.mark.asyncio
async def test_paid_without_amount_and_no_due_match_asks_for_amount():
    msg = _make_message("paid electricity")
    http_client = _make_http_client()
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.confirm_bill.assert_not_called()
    http_client.create_transaction.assert_not_called()
    sent = msg.channel.send.call_args[0][0]
    assert "couldn't find a due payment matching 'electricity'" in sent
    assert "paid electricity $120" in sent


@pytest.mark.asyncio
async def test_paid_due_list_error_does_not_log_a_transaction():
    msg = _make_message("paid electricity $120")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(side_effect=APIError(503, "Glow-worm is down."))
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.create_transaction.assert_not_called()
    http_client.confirm_bill.assert_not_called()
    assert msg.channel.send.call_args[0][0] == "Glow-worm is down."


@pytest.mark.asyncio
async def test_paid_due_list_network_error_does_not_log_a_transaction():
    msg = _make_message("paid electricity $120")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(side_effect=httpx.RequestError("Connection refused"))
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.create_transaction.assert_not_called()
    assert "reach glow-worm" in msg.channel.send.call_args[0][0]


@pytest.mark.asyncio
async def test_paid_confirm_409_shows_glowworm_detail():
    msg = _make_message("paid hellofresh $92.40")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(return_value=[_due()])
    http_client.confirm_bill = AsyncMock(
        side_effect=APIError(409, "Already done. Showing what's next.")
    )
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.create_transaction.assert_not_called()
    assert msg.channel.send.call_args[0][0] == "Already done. Showing what's next."


@pytest.mark.asyncio
async def test_paid_confirm_400_shows_blocked_detail():
    msg = _make_message("paid hellofresh $92.40")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(return_value=[_due()])
    http_client.confirm_bill = AsyncMock(
        side_effect=APIError(
            400,
            "There's no Groceries budget for October 2026. Set it on the Budgets page first.",
        )
    )
    bot = _make_bot()

    await handle(msg, http_client, bot)

    assert msg.channel.send.call_args[0][0] == (
        "There's no Groceries budget for October 2026. Set it on the Budgets page first."
    )


@pytest.mark.asyncio
async def test_paid_shows_problem_on_the_due_row_without_confirming():
    msg = _make_message("paid hellofresh")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(
        return_value=[
            _due(
                problem={
                    "code": "source_missing",
                    "detail": "Kids fund was deleted. Change where this is paid from.",
                }
            )
        ]
    )
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.confirm_bill.assert_not_called()
    assert msg.channel.send.call_args[0][0] == (
        "Kids fund was deleted. Change where this is paid from."
    )


@pytest.mark.asyncio
async def test_paid_date_hint_does_not_change_the_due_date_sent():
    msg = _make_message("paid hellofresh $92.40 yesterday")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(return_value=[_due(due_date="2026-10-26")])
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.confirm_bill.assert_called_once_with(31, "92.40", "2026-10-26")
    http_client.create_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_paid_over_limit_does_not_confirm():
    msg = _make_message("paid hellofresh $999")
    http_client = _make_http_client()
    bot = _make_bot()

    with patch("bot.handler.MAX_TRANSACTION_AMOUNT", 500.0):
        await handle(msg, http_client, bot)

    http_client.get_due_payments.assert_not_called()
    http_client.confirm_bill.assert_not_called()
    assert "999.00" in msg.channel.send.call_args[0][0]


@pytest.mark.asyncio
async def test_paid_expected_amount_over_limit_is_rejected():
    msg = _make_message("paid hellofresh")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(return_value=[_due(expected_amount="600.00")])
    bot = _make_bot()

    with patch("bot.handler.MAX_TRANSACTION_AMOUNT", 500.0):
        await handle(msg, http_client, bot)

    http_client.confirm_bill.assert_not_called()
    sent = msg.channel.send.call_args[0][0]
    assert "600.00" in sent
    assert "500.00" in sent


@pytest.mark.asyncio
async def test_paid_confirm_network_error():
    msg = _make_message("paid hellofresh $10")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(return_value=[_due()])
    http_client.confirm_bill = AsyncMock(side_effect=httpx.RequestError("timed out"))
    bot = _make_bot()

    await handle(msg, http_client, bot)

    assert "reach glow-worm" in msg.channel.send.call_args[0][0]


@pytest.mark.asyncio
async def test_paid_confirm_without_message_uses_fallback():
    msg = _make_message("paid hellofresh $10")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(return_value=[_due()])
    http_client.confirm_bill = AsyncMock(return_value={})
    bot = _make_bot()

    await handle(msg, http_client, bot)

    assert msg.channel.send.call_args[0][0] == "Confirmed."


@pytest.mark.asyncio
async def test_skip_confirms_one_due_payment():
    msg = _make_message("skip hellofresh")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(return_value=[_due()])
    http_client.skip_bill = AsyncMock(
        return_value={"message": "Skipped 26 Oct 2026. Next due 2 Nov 2026."}
    )
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.skip_bill.assert_called_once_with(31, "2026-10-26")
    http_client.confirm_bill.assert_not_called()
    http_client.create_transaction.assert_not_called()
    assert msg.channel.send.call_args[0][0] == "Skipped 26 Oct 2026. Next due 2 Nov 2026."


@pytest.mark.asyncio
async def test_skip_with_problem_still_calls_skip():
    msg = _make_message("Skip HelloFresh")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(
        return_value=[_due(problem={"code": "no_budget", "detail": "There's no Groceries budget."})]
    )
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.skip_bill.assert_called_once_with(31, "2026-10-26")


@pytest.mark.asyncio
async def test_skip_several_matches_asks_which():
    msg = _make_message("skip bill")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(
        return_value=[
            _due(name="Gas Bill", paid_from_label="Bills fund", due_date="2026-10-15"),
            _due(
                bill_id=32, name="Water Bill", paid_from_label="Bills fund", due_date="2026-11-01"
            ),
        ]
    )
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.skip_bill.assert_not_called()
    sent = msg.channel.send.call_args[0][0]
    assert "Which did you mean?" in sent
    assert "Gas Bill" in sent
    assert "Water Bill" in sent


@pytest.mark.asyncio
async def test_skip_no_match():
    msg = _make_message("skip rent")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(return_value=[_due()])
    bot = _make_bot()

    await handle(msg, http_client, bot)

    http_client.skip_bill.assert_not_called()
    assert msg.channel.send.call_args[0][0] == "I couldn't find a due payment matching 'rent'."


@pytest.mark.asyncio
async def test_skip_409_shows_detail():
    msg = _make_message("skip hellofresh")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(return_value=[_due()])
    http_client.skip_bill = AsyncMock(
        side_effect=APIError(409, "This bill is paid automatically from the Bills fund.")
    )
    bot = _make_bot()

    await handle(msg, http_client, bot)

    assert msg.channel.send.call_args[0][0] == (
        "This bill is paid automatically from the Bills fund."
    )


@pytest.mark.asyncio
async def test_due_lists_payments_in_api_order():
    msg = _make_message("what's due")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(
        return_value=[
            _due(
                name="HelloFresh",
                expected_amount="89.95",
                due_date="2026-10-26",
                more_due=1,
                paid_from_label="Groceries budget",
            ),
            _due(
                bill_id=8,
                name="Water",
                expected_amount="210.00",
                due_date="2026-10-15",
                paid_from="bills_fund",
                paid_from_label="Bills fund",
                budget_month=None,
                problem={
                    "code": "no_budget",
                    "detail": "There's no Groceries budget for October 2026.",
                },
            ),
        ]
    )
    bot = _make_bot()

    await handle(msg, http_client, bot)

    assert msg.channel.send.call_args[0][0] == (
        "Due to confirm:\n"
        "• HelloFresh — $89.95 — Groceries budget — due 26 Oct 2026 — +1 more\n"
        "• Water — $210.00 — Bills fund — due 15 Oct 2026\n"
        "  There's no Groceries budget for October 2026."
    )


@pytest.mark.asyncio
async def test_due_empty():
    msg = _make_message("due")
    http_client = _make_http_client()
    bot = _make_bot()

    await handle(msg, http_client, bot)

    assert msg.channel.send.call_args[0][0] == "Nothing is due to confirm."


@pytest.mark.asyncio
async def test_due_api_error_shows_detail():
    msg = _make_message("whats due")
    http_client = _make_http_client()
    http_client.get_due_payments = AsyncMock(side_effect=APIError(401, "Unauthorised"))
    bot = _make_bot()

    await handle(msg, http_client, bot)

    assert msg.channel.send.call_args[0][0] == "Unauthorised"
