"""Unit tests for bot/cache.py."""

import os

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DISCORD_CHANNEL_ID", "123456")
os.environ.setdefault("GLOWWORM_API_URL", "http://localhost:8000")
os.environ.setdefault("GLOWWORM_API_KEY", "test-api-key")

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.cache as cache
from bot.client import GlowWormClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CATEGORIES = [
    {"id": 1, "name": "Groceries", "is_budget_category": True},
    {"id": 2, "name": "Transport", "is_budget_category": False},
]

SINKING_FUNDS = [
    {"id": 1, "name": "Holiday", "current_balance": 500.0},
    {"id": 2, "name": "Car", "current_balance": 1200.0},
]

BILLS_RAW = [
    {"id": 1, "name": "Electricity", "bill_type": "variable"},
    {"id": 2, "name": "Internet", "bill_type": "fixed"},
    {"id": 3, "name": "Water", "bill_type": "variable"},
]


def _mock_client() -> GlowWormClient:
    client = MagicMock(spec=GlowWormClient)
    client.get_categories = AsyncMock(return_value=CATEGORIES)
    client.get_sinking_funds = AsyncMock(return_value=SINKING_FUNDS)
    client.get_bills = AsyncMock(return_value=BILLS_RAW)
    return client


# ---------------------------------------------------------------------------
# initialise + accessors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialise_populates_categories():
    with patch.object(cache.hourly_refresh, "is_running", return_value=False), \
         patch.object(cache.hourly_refresh, "start"):
        await cache.initialise(_mock_client())

    assert cache.get_categories() == CATEGORIES


@pytest.mark.asyncio
async def test_initialise_populates_sinking_funds():
    with patch.object(cache.hourly_refresh, "is_running", return_value=False), \
         patch.object(cache.hourly_refresh, "start"):
        await cache.initialise(_mock_client())

    assert cache.get_sinking_funds() == SINKING_FUNDS


@pytest.mark.asyncio
async def test_initialise_populates_only_variable_bills():
    with patch.object(cache.hourly_refresh, "is_running", return_value=False), \
         patch.object(cache.hourly_refresh, "start"):
        await cache.initialise(_mock_client())

    bills = cache.get_bills()
    assert len(bills) == 2
    assert all(b["bill_type"] == "variable" for b in bills)
    assert {b["name"] for b in bills} == {"Electricity", "Water"}


@pytest.mark.asyncio
async def test_initialise_starts_hourly_task_when_not_running():
    with patch.object(cache.hourly_refresh, "is_running", return_value=False) as mock_running, \
         patch.object(cache.hourly_refresh, "start") as mock_start:
        await cache.initialise(_mock_client())

    mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_initialise_does_not_restart_task_when_already_running():
    with patch.object(cache.hourly_refresh, "is_running", return_value=True), \
         patch.object(cache.hourly_refresh, "start") as mock_start:
        await cache.initialise(_mock_client())

    mock_start.assert_not_called()


# ---------------------------------------------------------------------------
# hourly_refresh task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hourly_refresh_updates_data():
    # Initialise with first client
    with patch.object(cache.hourly_refresh, "is_running", return_value=False), \
         patch.object(cache.hourly_refresh, "start"):
        await cache.initialise(_mock_client())

    # New data for second refresh
    new_categories = [{"id": 99, "name": "New Category", "is_budget_category": False}]
    new_client = MagicMock(spec=GlowWormClient)
    new_client.get_categories = AsyncMock(return_value=new_categories)
    new_client.get_sinking_funds = AsyncMock(return_value=[])
    new_client.get_bills = AsyncMock(return_value=[])

    # Swap out the client and call refresh directly
    cache._client = new_client
    await cache._refresh()

    assert cache.get_categories() == new_categories
    assert cache.get_sinking_funds() == []
    assert cache.get_bills() == []


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_logs_counts(caplog):
    import logging

    with patch.object(cache.hourly_refresh, "is_running", return_value=False), \
         patch.object(cache.hourly_refresh, "start"):
        with caplog.at_level(logging.INFO, logger="bot.cache"):
            await cache.initialise(_mock_client())

    assert any(
        "categories" in record.message and "sinking funds" in record.message
        for record in caplog.records
    )
