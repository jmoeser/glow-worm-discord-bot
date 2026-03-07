import logging

from discord.ext import tasks

from bot.client import GlowWormClient

logger = logging.getLogger(__name__)

_categories: list[dict] = []
_sinking_funds: list[dict] = []
_bills: list[dict] = []

_client: GlowWormClient | None = None


def get_categories() -> list[dict]:
    return _categories


def get_sinking_funds() -> list[dict]:
    return _sinking_funds


def get_bills() -> list[dict]:
    """Returns only active variable bills."""
    return _bills


async def initialise(client: GlowWormClient) -> None:
    """Fetch all reference data on startup and start the hourly refresh task."""
    global _client
    _client = client
    await _refresh()
    if not hourly_refresh.is_running():
        hourly_refresh.start()


async def _refresh() -> None:
    global _categories, _sinking_funds, _bills
    assert _client is not None, "Cache not initialised — call initialise() first"
    _categories = await _client.get_categories()
    _sinking_funds = await _client.get_sinking_funds()
    all_bills = await _client.get_bills()
    _bills = [b for b in all_bills if b.get("bill_type") == "variable"]
    logger.info(
        "Cache refreshed: %d categories, %d sinking funds, %d variable bills",
        len(_categories),
        len(_sinking_funds),
        len(_bills),
    )


@tasks.loop(hours=1)
async def hourly_refresh() -> None:
    await _refresh()
