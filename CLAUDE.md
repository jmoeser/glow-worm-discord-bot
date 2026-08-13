# Claude Context — glow-worm-discord-bot

## What this is

A Python Discord bot that monitors a designated channel and parses natural-language messages
to create transactions in the glow-worm budget FastAPI application.

## Key documents

- `SPEC.md` — full product specification (parsing rules, API contract, flows, error handling)
- `TASKS.md` — ordered build tasks; work top-to-bottom

## Related project

The glow-worm API lives at `/Users/jordan/git/glow-worm`. Refer to it for:
- Models: `app/models.py`
- Schemas / request-response shapes: `app/schemas.py`
- Transaction API: `app/routes/transactions.py`
- Auth: API key via `Authorization: Bearer <key>` header (`app/middleware.py`)

## Stack

- Python 3.14+
- `uv` — package manager and virtualenv tool (use instead of pip/venv)
- `discord.py` — bot framework
- `httpx` — async HTTP client for glow-worm API calls
- `python-dotenv` — local env var loading
- Runs in a Podman container; config entirely via environment variables

## Project structure (target)

```
bot/
  main.py       # discord.Client setup, on_ready, on_message
  config.py     # env var loading/validation
  client.py     # async httpx wrapper for glow-worm API
  cache.py      # hourly refresh of categories, sinking funds, bills
  parser.py     # regex patterns + ParseResult + date resolution
  resolver.py   # name matching, budget/fund/bill resolution
  handler.py    # orchestrates parse → resolve → confirm/commit
tests/
  test_parser.py
  test_resolver.py
```

## Important behaviour rules

- Bot only responds in the channel set by `DISCORD_CHANNEL_ID`
- Messages that don't match any pattern are **silently ignored**
- `CONFIRM_TRANSACTIONS=true` → show confirmation embed before committing (Phase 1)
- `CONFIRM_TRANSACTIONS=false` → auto-commit and show remaining balance (Phase 2)
- Cache refreshes hourly via `discord.ext.tasks`
- `ALLOWED_USER_IDS` — comma-separated Discord user IDs; if set, only those users can create
  transactions; messages from other users are silently ignored
- `MAX_TRANSACTION_AMOUNT` — if set, any parsed amount exceeding this value is rejected with
  an error message before resolution or commit

## glow-worm transaction logic

- Category with `is_budget_category=true` → `transaction_type: "budget_expense"`, requires a
  budget to exist for the current month/year, attach `budget_id`
- Category with `is_budget_category=false` → `transaction_type: "regular"`, no budget lookup
- Sinking fund withdrawal → `transaction_type: "withdrawal"`, requires a category specified
  after the fund name in the message
- Sinking fund contribution → `transaction_type: "contribution"`, same category requirement
- Variable bill payment → `transaction_type: "regular"`, uses the bill's own `category_id`

## Testing

Unit tests must be written for all implementation work. Every new module or non-trivial
function should have a corresponding test file in `tests/`. Tests use `pytest` and
`pytest-asyncio` (with `asyncio_mode = "auto"`). Do not consider a task complete until
tests are written and passing.

### Running tests

```bash
# Install dependencies (first time or after pyproject.toml changes)
uv sync

# Run all tests
uv run pytest

# Run a specific test file
uv run pytest tests/test_cache.py -v
```

Dependencies are managed via `uv`. Never use `pip install` directly — add packages to
`pyproject.toml` and run `uv sync`.

## Dependency updates

Hosted [Mend Renovate](https://github.com/apps/renovate); config is `renovate.json5`.
Dependabot version updates are not used; keep the GitHub dependency graph and Dependabot alerts.

- One non-major PR on the 1st and 15th 00:00–06:59 (`Australia/Brisbane`). Majors are separate PRs in the same window. Nothing automerges.
- Python dependencies are exact pins (`==`) so `uv lock` cannot jump past the version Renovate selected (`rangeStrategy: "pin"`).
- uv toolchain (`aqua.yaml` `astral-sh/uv`, GHCR `ghcr.io/astral-sh/uv` image, CI `setup-uv` `version:`) is always one isolated `uv` PR (all update types, including 0.x minors and 1.x).
- Python `3.14` → `3.15` is a **minor**, isolated as `python runtime` and Dashboard-gated. Do not merge until `requires-python` and `[tool.mypy] python_version` move in the same commit.
- `minimumReleaseAge: "7 days"` where the registry publishes timestamps. The GitHub release of `astral-sh/uv` is the clock for the uv group (GHCR is timestamp-optional on that docker member only).
- Pending updates live on the Dependency Dashboard issue. Dashboard “Run now” starts a job; it does **not** skip the schedule for new PRs.

## Timezone

All "today/yesterday" date resolution uses the timezone set by the `TIMEZONE` env var
(e.g. `Australia/Brisbane`). Defaults to `UTC` if not set.
