"""Unit tests for bot/client.py.

Env vars are set before any bot imports so that bot.config loads cleanly.
Each test replaces client._client with an AsyncMock to avoid real HTTP calls.
"""

import os

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DISCORD_CHANNEL_ID", "123456")
os.environ.setdefault("GLOWWORM_API_URL", "http://localhost:8000")
os.environ.setdefault("GLOWWORM_API_KEY", "test-api-key")

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from bot.client import APIError, GlowWormClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _response(status: int, data) -> httpx.Response:
    """Build a real httpx.Response with a JSON body."""
    return httpx.Response(
        status,
        json=data,
        request=httpx.Request("GET", "http://localhost:8000"),
    )


def _error_response(status: int, detail: str) -> httpx.Response:
    return _response(status, {"detail": detail})


@pytest.fixture
def client():
    """A GlowWormClient whose inner httpx client is fully mocked."""
    c = GlowWormClient()
    c._client = MagicMock()
    return c


# ---------------------------------------------------------------------------
# APIError
# ---------------------------------------------------------------------------


def test_api_error_attributes():
    err = APIError(404, "not found")
    assert err.status_code == 404
    assert err.detail == "not found"
    assert "404" in str(err)
    assert "not found" in str(err)


# ---------------------------------------------------------------------------
# _check
# ---------------------------------------------------------------------------


def test_check_returns_json_on_success(client):
    r = _response(200, {"id": 1})
    assert client._check(r) == {"id": 1}


def test_check_raises_api_error_on_4xx(client):
    r = _error_response(404, "Category not found")
    with pytest.raises(APIError) as exc_info:
        client._check(r)
    assert exc_info.value.status_code == 404
    assert "Category not found" in exc_info.value.detail


def test_check_raises_api_error_on_5xx(client):
    r = _error_response(500, "Internal server error")
    with pytest.raises(APIError) as exc_info:
        client._check(r)
    assert exc_info.value.status_code == 500


def test_check_handles_non_json_error_body(client):
    r = httpx.Response(
        503,
        content=b"Service Unavailable",
        request=httpx.Request("GET", "http://localhost:8000"),
    )
    with pytest.raises(APIError) as exc_info:
        client._check(r)
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# get_categories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_categories_returns_list(client):
    categories = [{"id": 1, "name": "Groceries", "is_budget_category": True}]
    client._client.get = AsyncMock(return_value=_response(200, categories))

    result = await client.get_categories()

    client._client.get.assert_called_once_with("/api/categories")
    assert result == categories


@pytest.mark.asyncio
async def test_get_categories_raises_on_error(client):
    client._client.get = AsyncMock(return_value=_error_response(401, "Unauthorised"))

    with pytest.raises(APIError) as exc_info:
        await client.get_categories()
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# get_sinking_funds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sinking_funds_returns_list(client):
    funds = [{"id": 1, "name": "Holiday", "current_balance": 500.0}]
    client._client.get = AsyncMock(return_value=_response(200, funds))

    result = await client.get_sinking_funds()

    client._client.get.assert_called_once_with("/api/sinking-funds")
    assert result == funds


@pytest.mark.asyncio
async def test_get_sinking_funds_raises_on_error(client):
    client._client.get = AsyncMock(return_value=_error_response(500, "Server error"))

    with pytest.raises(APIError):
        await client.get_sinking_funds()


# ---------------------------------------------------------------------------
# get_sinking_fund (single)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sinking_fund_returns_dict(client):
    fund = {"id": 7, "name": "Car", "current_balance": 1200.0}
    client._client.get = AsyncMock(return_value=_response(200, fund))

    result = await client.get_sinking_fund(7)

    client._client.get.assert_called_once_with("/api/sinking-funds/7")
    assert result == fund


@pytest.mark.asyncio
async def test_get_sinking_fund_raises_on_404(client):
    client._client.get = AsyncMock(return_value=_error_response(404, "Sinking fund not found"))

    with pytest.raises(APIError) as exc_info:
        await client.get_sinking_fund(999)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# get_bills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_bills_returns_list(client):
    bills = [{"id": 1, "name": "Netflix", "bill_type": "variable"}]
    client._client.get = AsyncMock(return_value=_response(200, bills))

    result = await client.get_bills()

    client._client.get.assert_called_once_with("/api/bills")
    assert result == bills


@pytest.mark.asyncio
async def test_get_bills_raises_on_error(client):
    client._client.get = AsyncMock(return_value=_error_response(403, "Forbidden"))

    with pytest.raises(APIError) as exc_info:
        await client.get_bills()
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# get_budgets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_budgets_filters_by_category_id(client):
    all_budgets = [
        {"id": 1, "category_id": 10, "month": 3, "year": 2026, "allocated_amount": 400},
        {"id": 2, "category_id": 20, "month": 3, "year": 2026, "allocated_amount": 200},
        {"id": 3, "category_id": 10, "month": 3, "year": 2026, "allocated_amount": 150},
    ]
    client._client.get = AsyncMock(return_value=_response(200, all_budgets))

    result = await client.get_budgets(category_id=10, month=3, year=2026)

    client._client.get.assert_called_once_with("/api/budgets", params={"month": 3, "year": 2026})
    assert len(result) == 2
    assert all(b["category_id"] == 10 for b in result)


@pytest.mark.asyncio
async def test_get_budgets_returns_empty_when_no_match(client):
    client._client.get = AsyncMock(
        return_value=_response(
            200,
            [{"id": 1, "category_id": 99, "month": 3, "year": 2026}],
        )
    )

    result = await client.get_budgets(category_id=42, month=3, year=2026)

    assert result == []


@pytest.mark.asyncio
async def test_get_budgets_raises_on_error(client):
    client._client.get = AsyncMock(return_value=_error_response(500, "DB error"))

    with pytest.raises(APIError):
        await client.get_budgets(category_id=1, month=3, year=2026)


# ---------------------------------------------------------------------------
# create_transaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_transaction_posts_payload(client):
    payload = {
        "date": "2026-03-07",
        "amount": 45.50,
        "category_id": 3,
        "type": "expense",
        "transaction_type": "budget_expense",
        "budget_id": 1,
    }
    created = {"id": 101, **payload}
    client._client.post = AsyncMock(return_value=_response(201, created))

    await client.create_transaction(payload)

    client._client.post.assert_called_once_with("/api/transactions", json=payload)


@pytest.mark.asyncio
async def test_create_transaction_raises_on_422(client):
    client._client.post = AsyncMock(return_value=_error_response(422, "Validation error"))

    with pytest.raises(APIError) as exc_info:
        await client.create_transaction({"bad": "data"})
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_create_transaction_raises_on_network_like_5xx(client):
    client._client.post = AsyncMock(return_value=_error_response(503, "Unavailable"))

    with pytest.raises(APIError) as exc_info:
        await client.create_transaction({})
    assert exc_info.value.status_code == 503
