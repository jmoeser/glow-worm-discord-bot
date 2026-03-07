"""Tests for bot/resolver.py"""
import os

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DISCORD_CHANNEL_ID", "123456")
os.environ.setdefault("GLOWWORM_API_URL", "http://localhost:8000")
os.environ.setdefault("GLOWWORM_API_KEY", "test-api-key")

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from bot.resolver import (
    ResolveResult,
    match_name,
    resolve_bill,
    resolve_deposit,
    resolve_expense,
)

# ---------------------------------------------------------------------------
# Fixtures / shared test data
# ---------------------------------------------------------------------------

CATEGORIES = [
    {"id": 1, "name": "Groceries", "is_budget_category": True},
    {"id": 2, "name": "Entertainment", "is_budget_category": True},
    {"id": 3, "name": "Transfer", "is_budget_category": False},
    {"id": 4, "name": "Fuel", "is_budget_category": False},
    {"id": 5, "name": "General Expenses", "is_budget_category": True},
]

SINKING_FUNDS = [
    {"id": 10, "name": "Holiday Fund"},
    {"id": 11, "name": "Short Term Savings"},
    {"id": 12, "name": "Emergency Fund"},
]

BILLS = [
    {"id": 20, "name": "Electricity", "category_id": 4, "bill_type": "variable"},
    {"id": 21, "name": "Gas Bill", "category_id": 4, "bill_type": "variable"},
]

TODAY = date(2026, 3, 7)
BUDGET = {"id": 99, "category_id": 1, "month": 3, "year": 2026}


def _make_client(budgets=None):
    client = AsyncMock()
    client.get_budgets = AsyncMock(return_value=budgets if budgets is not None else [BUDGET])
    return client


@pytest.fixture(autouse=True)
def patch_cache():
    with (
        patch("bot.resolver.cache.get_categories", return_value=CATEGORIES),
        patch("bot.resolver.cache.get_sinking_funds", return_value=SINKING_FUNDS),
        patch("bot.resolver.cache.get_bills", return_value=BILLS),
    ):
        yield


# ---------------------------------------------------------------------------
# match_name
# ---------------------------------------------------------------------------


class TestMatchName:
    def test_exact_match(self):
        result = match_name(["Groceries"], CATEGORIES)
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_exact_match_case_insensitive(self):
        result = match_name(["groceries"], CATEGORIES)
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_exact_multi_word(self):
        result = match_name(["Short", "Term", "Savings"], SINKING_FUNDS)
        assert len(result) == 1
        assert result[0]["id"] == 11

    def test_starts_with_match(self):
        result = match_name(["Entertain"], CATEGORIES)
        assert len(result) == 1
        assert result[0]["id"] == 2

    def test_contains_match(self):
        result = match_name(["General"], CATEGORIES)
        assert len(result) == 1
        assert result[0]["id"] == 5

    def test_ambiguous_starts_with(self):
        # Both "Holiday Fund" and "Emergency Fund" contain "Fund"
        result = match_name(["Fund"], SINKING_FUNDS)
        assert len(result) > 1

    def test_no_match(self):
        result = match_name(["xyz_no_match"], CATEGORIES)
        assert result == []

    def test_exact_beats_starts_with(self):
        # "Fuel" exactly matches — should not return "General Expenses" (no match) or others
        result = match_name(["Fuel"], CATEGORIES)
        assert len(result) == 1
        assert result[0]["id"] == 4


# ---------------------------------------------------------------------------
# resolve_expense — category path
# ---------------------------------------------------------------------------


class TestResolveExpenseCategory:
    async def test_exact_budget_category(self):
        client = _make_client([BUDGET])
        result = await resolve_expense(["groceries"], client, TODAY)
        assert result.error is None
        assert result.transaction_type == "budget_expense"
        assert result.category_id == 1
        assert result.budget_id == 99
        assert result.description is None

    async def test_budget_category_with_description(self):
        client = _make_client([BUDGET])
        result = await resolve_expense(["groceries", "chemist"], client, TODAY)
        assert result.error is None
        assert result.transaction_type == "budget_expense"
        assert result.category_id == 1
        assert result.description == "chemist"

    async def test_non_budget_category(self):
        client = _make_client()
        result = await resolve_expense(["fuel"], client, TODAY)
        assert result.error is None
        assert result.transaction_type == "regular"
        assert result.category_id == 4
        assert result.budget_id is None

    async def test_partial_category_match(self):
        client = _make_client()
        result = await resolve_expense(["entertain"], client, TODAY)
        assert result.error is None
        assert result.transaction_type == "budget_expense"
        assert result.category_id == 2

    async def test_no_budget_for_category(self):
        client = _make_client([])  # no budgets
        result = await resolve_expense(["groceries"], client, TODAY)
        assert result.error == "no_budget"
        assert "Groceries" in result.error_message
        assert "March 2026" in result.error_message

    async def test_ambiguous_category(self):
        # Both "Holiday Fund" and "Emergency Fund" contain "fund" — but for categories
        # test with a substring that matches multiple
        cats_with_ambig = CATEGORIES + [
            {"id": 9, "name": "Grocery Store", "is_budget_category": False}
        ]
        with patch("bot.resolver.cache.get_categories", return_value=cats_with_ambig):
            client = _make_client()
            result = await resolve_expense(["groc"], client, TODAY)
            assert result.error == "ambiguous"
            assert len(result.ambiguous_names) >= 2


# ---------------------------------------------------------------------------
# resolve_expense — sinking fund (withdrawal) path
# ---------------------------------------------------------------------------


class TestResolveExpenseFund:
    async def test_exact_fund_with_category(self):
        client = _make_client()
        result = await resolve_expense(
            ["holiday", "fund", "groceries"], client, TODAY
        )
        assert result.error is None
        assert result.transaction_type == "withdrawal"
        assert result.sinking_fund_id == 10
        assert result.category_id == 1

    async def test_multi_word_fund_name(self):
        client = _make_client()
        result = await resolve_expense(
            ["short", "term", "savings", "transfer"], client, TODAY
        )
        assert result.error is None
        assert result.transaction_type == "withdrawal"
        assert result.sinking_fund_id == 11
        assert result.category_id == 3

    async def test_fund_partial_match_with_category(self):
        client = _make_client()
        # "holiday" starts-with-matches "Holiday Fund"
        result = await resolve_expense(["holiday", "transfer"], client, TODAY)
        assert result.error is None
        assert result.transaction_type == "withdrawal"
        assert result.sinking_fund_id == 10
        assert result.category_id == 3

    async def test_fund_no_category_tokens(self):
        client = _make_client()
        result = await resolve_expense(["holiday", "fund"], client, TODAY)
        assert result.error == "no_category"
        assert "category" in result.error_message.lower()

    async def test_fund_matched_category_not_found(self):
        client = _make_client()
        result = await resolve_expense(
            ["holiday", "fund", "unknowncat"], client, TODAY
        )
        assert result.error == "no_match_category"
        assert "Holiday Fund" in result.error_message
        assert "unknowncat" in result.error_message

    async def test_fund_ambiguous_category(self):
        cats_with_ambig = CATEGORIES + [
            {"id": 9, "name": "Grocery Store", "is_budget_category": False}
        ]
        with patch("bot.resolver.cache.get_categories", return_value=cats_with_ambig):
            client = _make_client()
            result = await resolve_expense(
                ["holiday", "fund", "groc"], client, TODAY
            )
            assert result.error == "ambiguous"

    async def test_no_match_at_all(self):
        client = _make_client()
        result = await resolve_expense(["xyzzy", "blorp"], client, TODAY)
        assert result.error == "no_match"


# ---------------------------------------------------------------------------
# resolve_deposit — contribution path
# ---------------------------------------------------------------------------


class TestResolveDeposit:
    async def test_exact_fund_with_category(self):
        client = _make_client()
        result = await resolve_deposit(
            ["holiday", "fund", "transfer"], client, TODAY
        )
        assert result.error is None
        assert result.transaction_type == "contribution"
        assert result.sinking_fund_id == 10
        assert result.category_id == 3

    async def test_multi_word_fund_with_category(self):
        client = _make_client()
        result = await resolve_deposit(
            ["short", "term", "savings", "transfer"], client, TODAY
        )
        assert result.error is None
        assert result.transaction_type == "contribution"
        assert result.sinking_fund_id == 11
        assert result.category_id == 3

    async def test_fund_no_category(self):
        client = _make_client()
        result = await resolve_deposit(["emergency", "fund"], client, TODAY)
        assert result.error == "no_category"
        assert "category" in result.error_message.lower()

    async def test_fund_not_found(self):
        client = _make_client()
        result = await resolve_deposit(["mystery", "fund", "transfer"], client, TODAY)
        assert result.error == "no_match"

    async def test_fund_category_not_found(self):
        client = _make_client()
        result = await resolve_deposit(
            ["holiday", "fund", "nosuchcat"], client, TODAY
        )
        assert result.error == "no_match_category"
        assert "Holiday Fund" in result.error_message


# ---------------------------------------------------------------------------
# resolve_bill
# ---------------------------------------------------------------------------


class TestResolveBill:
    def test_exact_bill_match(self):
        result = resolve_bill(["electricity"])
        assert result.error is None
        assert result.transaction_type == "regular"
        assert result.bill_id == 20
        assert result.category_id == 4

    def test_partial_bill_match(self):
        result = resolve_bill(["gas"])
        assert result.error is None
        assert result.bill_id == 21

    def test_ambiguous_bill(self):
        bills_ambig = BILLS + [
            {"id": 22, "name": "Electricity Plus", "category_id": 4, "bill_type": "variable"}
        ]
        with patch("bot.resolver.cache.get_bills", return_value=bills_ambig):
            result = resolve_bill(["electric"])
            assert result.error == "ambiguous"
            assert len(result.ambiguous_names) >= 2

    def test_no_match_bill(self):
        result = resolve_bill(["internet"])
        assert result.error == "no_match"

    def test_multi_word_bill(self):
        result = resolve_bill(["gas", "bill"])
        assert result.error is None
        assert result.bill_id == 21
