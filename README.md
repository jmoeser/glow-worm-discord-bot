# glow-worm-discord-bot

A Discord bot that monitors a designated channel and parses natural-language messages to create transactions in the [glow-worm](https://github.com/jmoeser/glow-worm) budget API.

## How it works

Post plain English messages in the monitored channel — no commands or prefixes needed. The bot parses them, resolves category/fund names against the API, and either asks for confirmation or auto-commits depending on configuration.

Messages that don't match any pattern are silently ignored.

## Supported message patterns

### Expenses

```
spent $20 groceries
spend 20 groceries
buy $4.50 coffee
bought 12 lunch yesterday
purchased $99 electronics last friday
```

### Sinking fund withdrawals

```
spent $15 short term savings groceries
spend 50 holiday fund electronics
```

### Sinking fund contributions

```
deposit $30 short term savings transfer
save $50 car fund income
saved 25 emergency fund transfer
```

### Variable bill payments

```
paid electricity $120
paid electricity 98.50
```

### Date keywords

Append `yesterday`, `today`, or `last <weekday>` to any message. Defaults to today if omitted.

## Configuration

Copy `.env.example` to `.env` and fill in the values:

| Variable               | Description                                                         |
|------------------------|---------------------------------------------------------------------|
| `DISCORD_TOKEN`        | Bot token from the Discord developer portal                         |
| `DISCORD_CHANNEL_ID`   | ID of the channel the bot monitors                                  |
| `GLOWWORM_API_URL`     | Base URL of the glow-worm API (e.g. `http://glowworm:8000`)         |
| `GLOWWORM_API_KEY`     | API key (Bearer token) for authenticating with glow-worm            |
| `CONFIRM_TRANSACTIONS` | `true` — show confirmation embed before committing (default); `false` — auto-commit |
| `TIMEZONE`             | IANA timezone for date resolution (e.g. `Australia/Brisbane`; default: `UTC`) |
| `ALLOWED_USER_IDS`     | Comma-separated Discord user IDs permitted to create transactions (e.g. `123456789,987654321`); if unset, all users in the channel are allowed |
| `MAX_TRANSACTION_AMOUNT` | Maximum amount (in dollars) allowed for a single transaction; transactions over this limit are rejected (e.g. `500`); if unset, no limit is enforced |

## Running locally

Requires Python 3.14+ and [uv](https://github.com/astral-sh/uv).

```bash
# Install dependencies
uv sync

# Run the bot
uv run python -m bot.main
```

## Running with Podman / Docker

The bot is designed to run alongside glow-worm on a shared container network.

```bash
# Build
podman build -t glow-worm-discord-bot -f Containerfile .

# Run (pass env vars directly or via --env-file)
podman run --env-file .env --network glowworm-net glow-worm-discord-bot
```

## Development

```bash
# Install dev dependencies
uv sync

# Run tests
uv run pytest

# Run a specific test file
uv run pytest tests/test_parser.py -v

# Lint and type-check
uv run ruff check bot/ tests/
uv run mypy bot/
```

## Project structure

```
bot/
  main.py       # discord.Client setup, on_ready, on_message
  config.py     # env var loading/validation
  client.py     # async httpx wrapper for glow-worm API calls
  cache.py      # hourly refresh of categories, sinking funds, bills
  parser.py     # regex patterns + ParseResult + date resolution
  resolver.py   # name matching, budget/fund/bill resolution
  handler.py    # orchestrates parse -> resolve -> confirm/commit
tests/
  test_parser.py
  test_resolver.py
```

## Related

- [SPEC.md](SPEC.md) — full product specification (parsing rules, API contract, flows, error handling)
- [glow-worm](https://github.com/jmoeser/glow-worm) — the budget API this bot talks to
