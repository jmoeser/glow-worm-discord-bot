import logging

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
    await handle(message, http_client, client)


def main() -> None:
    client.run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
