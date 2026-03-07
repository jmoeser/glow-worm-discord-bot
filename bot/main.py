import logging
import time

import discord

import bot.cache as cache
from bot import config
from bot.client import GlowWormClient
from bot.handler import handle

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
http_client = GlowWormClient()

# Rate limiting: map user_id → monotonic timestamp of last accepted message.
_last_message_time: dict[int, float] = {}
_RATE_LIMIT_SECONDS = 60.0

# Refuse to run the parser on messages beyond this length (ReDoS guard).
_MAX_MESSAGE_LENGTH = 500


@client.event
async def on_ready() -> None:
    await cache.initialise(http_client)
    guild_names = [g.name for g in client.guilds]
    logger.info("Logged in as %s | Guilds: %s", client.user, guild_names)


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    if message.channel.id != config.DISCORD_CHANNEL_ID:
        return

    # Authorisation: only permit explicitly listed users (when a list is configured).
    if config.ALLOWED_USER_IDS and message.author.id not in config.ALLOWED_USER_IDS:
        return

    # Discard oversized messages before running any regex (ReDoS prevention).
    if len(message.content) > _MAX_MESSAGE_LENGTH:
        return

    # Per-user rate limit: one accepted message per minute.
    user_id = message.author.id
    now = time.monotonic()
    if now - _last_message_time.get(user_id, 0.0) < _RATE_LIMIT_SECONDS:
        await message.channel.send("Please wait a minute before sending another transaction.")
        return
    _last_message_time[user_id] = now

    await handle(message, http_client, client)


def main() -> None:
    client.run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
