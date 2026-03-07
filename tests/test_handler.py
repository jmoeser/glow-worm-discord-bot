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
from bot.handler import _build_payload, _error_text, handle
from bot.parser import ParseResult
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
