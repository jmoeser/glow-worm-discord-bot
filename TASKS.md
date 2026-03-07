# Build Tasks

Tasks are ordered by dependency. Complete each before moving to the next.

---

## 1. Project scaffolding

- [x] Create `pyproject.toml` with dependencies: `discord.py`, `httpx`, `python-dotenv`
- [x] Create the `bot/` package with empty `__init__.py` files
- [x] Create `tests/` directory with empty `__init__.py`
- [x] Create `.env.example` with all required env var keys (no values)
- [x] Create `Containerfile` based on `python:3.14-slim`, installs deps, runs `python -m bot.main`

---

## 2. Config (`bot/config.py`)

- [x] Load all env vars: `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID`, `GLOWWORM_API_URL`,
      `GLOWWORM_API_KEY`, `CONFIRM_TRANSACTIONS`
- [x] Validate that required vars are present; raise a clear error at startup if any are missing
- [x] Parse `CONFIRM_TRANSACTIONS` as a boolean (default `true`)
- [x] Parse `DISCORD_CHANNEL_ID` as an integer
- [x] Parse `TIMEZONE` as an IANA timezone string (default `UTC`); validate it is a recognised
      timezone at startup

---

## 3. API client (`bot/client.py`)

- [x] Create an async `httpx.AsyncClient` configured with `GLOWWORM_API_URL` as base URL
      and `Authorization: Bearer <GLOWWORM_API_KEY>` header on every request
- [x] Implement `get_categories()` → `GET /api/categories`
- [x] Implement `get_sinking_funds()` → `GET /api/sinking-funds`
- [x] Implement `get_bills()` → `GET /api/bills`
- [x] Implement `get_budgets(category_id, month, year)` → `GET /api/budgets` filtered by params
- [x] Implement `create_transaction(payload: dict)` → `POST /api/transactions`
- [x] Implement `get_sinking_fund(fund_id)` → `GET /api/sinking-funds/{id}` (for balance readback)
- [x] All methods should raise a typed exception on non-2xx responses, carrying the status code

---

## 4. Cache (`bot/cache.py`)

- [x] On startup, fetch and store categories, sinking funds, and active variable bills
      using the API client
- [x] Expose the cached data as simple module-level accessors
- [x] Schedule an hourly background task (using `discord.ext.tasks`) to refresh all three
- [x] Log a message on each refresh so it's visible in container logs

---

## 5. Parser (`bot/parser.py`)

- [x] Define the three compiled regex patterns: `EXPENSE_RE`, `DEPOSIT_RE`, `BILL_RE`
      (see SPEC for patterns)
- [x] Implement `parse(message: str) -> ParseResult | None` that returns `None` for
      non-matching messages and a structured result otherwise
- [x] `ParseResult` should carry: `intent` (expense/deposit/bill), `amount`, `raw_name_tokens`,
      `raw_date_hint`, `raw_description`
- [x] Implement date resolution using the configured `TIMEZONE`:
  - `"today"` or absent → today in configured timezone
  - `"yesterday"` → today - 1 day in configured timezone
  - `"last <weekday>"` → most recent past occurrence of that weekday
- [x] Write `tests/test_parser.py` covering:
  - All trigger word variants
  - `$` prefix present and absent
  - Decimal amounts
  - Date hints: none, `yesterday`, `today`, `last friday`
  - Word order variants (description before/after name tokens)
  - Messages that should not match (return `None`)

---

## 6. Resolver (`bot/resolver.py`)

- [x] Implement `match_name(tokens: list[str], candidates: list[dict]) -> list[dict]` using
      the three-tier strategy: exact → starts-with → contains (case-insensitive)
- [x] Implement `resolve_expense(raw_tokens: list[str]) -> ResolveResult`:
  - Try matching all tokens against categories first
  - If a category matches:
    - If `is_budget_category = true`: fetch the budget for current month/year, attach `budget_id`
    - If `is_budget_category = false`: no budget lookup
    - Remaining tokens (after name) become description
  - If no category match: try sinking funds using greedy token splitting
    - Try progressively longer token prefixes until a fund matches
    - Remaining tokens after the fund name must match a category (required)
    - Return the fund + category together
  - Return a `ResolveResult` with matched entity type, ids, description, and any ambiguity info
- [x] Implement `resolve_deposit(raw_tokens: list[str]) -> ResolveResult`:
  - Match against sinking funds first (greedy), then remaining tokens against a category
  - Same structure as expense sinking fund path
- [x] Implement `resolve_bill(raw_tokens: list[str]) -> ResolveResult`:
  - Match against variable recurring bills only (`bill_type = "variable"`)
  - Return the bill's own `category_id` for use in the transaction
- [x] Write `tests/test_resolver.py` covering:
  - Exact, partial, and ambiguous category name matches
  - Exact, partial, and ambiguous sinking fund name matches
  - Sinking fund + category split (multi-word fund names)
  - No match returns appropriate error type
  - Budget category vs non-budget category branch
  - Missing category after sinking fund name

---

## 7. Handler (`bot/handler.py`)

- [ ] Implement `handle(message_content: str, channel) -> None` as the main entry point
- [ ] Call `parse()` — return immediately (silently) if `None`
- [ ] Call the appropriate resolver based on `ParseResult.intent`
- [ ] On resolver error, send the appropriate error message to the channel (see SPEC error table)
- [ ] On successful resolve, branch on `CONFIRM_TRANSACTIONS`:
  - **True (Phase 1):** build and send a confirmation embed, wait up to 60 seconds for a
    ✅ or ❌ reaction from the original message author; on ✅ commit, on ❌/timeout reply "Cancelled."
  - **False (Phase 2):** immediately call `create_transaction()`, then send a success embed
    showing the remaining budget or fund balance
- [ ] Success embed for budget expense:
  ```
  ✅ Added $<amount> to <Category> (<Month Year>)
     Budget remaining: $<allocated - spent>
  ```
- [ ] Success embed for sinking fund withdrawal:
  ```
  ✅ Withdrew $<amount> from <Fund> — <Category>
     Fund balance: $<current_balance>
  ```
- [ ] Success embed for sinking fund contribution:
  ```
  ✅ Deposited $<amount> to <Fund> — <Category>
     Fund balance: $<current_balance>
  ```
- [ ] Success embed for variable bill payment:
  ```
  ✅ Paid $<amount> for <Bill name>
  ```
- [ ] On API error, send the error message with the HTTP status code
- [ ] On network error, send the network error message

---

## 8. Bot entry point (`bot/main.py`)

- [ ] Create the `discord.Client` with the required `message_content` intent enabled
- [ ] In `on_ready`, initialise the cache (fetch all reference data) and start the hourly
      refresh task; log bot name and guilds to stdout
- [ ] In `on_message`:
  - Ignore messages from bots (including self)
  - Ignore messages not in `DISCORD_CHANNEL_ID`
  - Pass `message.content` and the channel to `handle()`
- [ ] Start the bot with `client.run(config.DISCORD_TOKEN)`

---

## 9. Integration smoke test

- [ ] With glow-worm running locally, start the bot against a test Discord server
- [ ] Manually verify each message pattern end-to-end:
  - Budget expense (budget category)
  - Non-budget expense
  - Sinking fund withdrawal
  - Sinking fund contribution
  - Variable bill payment
  - Ambiguous name → clarification reply
  - Unknown name → create-in-glow-worm suggestion
  - Non-matching message → silence
- [ ] Verify the hourly cache refresh fires and logs correctly
- [ ] Verify the Containerfile builds and the bot starts cleanly in a container
