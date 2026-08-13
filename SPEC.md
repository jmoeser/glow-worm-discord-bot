# Glow-Worm Discord Bot — Product Specification

## Overview

A Python Discord bot that monitors a designated channel and parses natural-language messages
to create transactions in the glow-worm budget API. The bot uses regex-based parsing, resolves
category/fund names via the API, and (initially) confirms before committing. Once stable, it
auto-commits and reports the remaining balance in the affected budget or sinking fund.

---

## Architecture

```
Discord Channel
      |
      v
Discord Bot (Python, discord.py)   <-- podman container A
      |
      | HTTP (Bearer token)
      v
glow-worm FastAPI                  <-- podman container B
      |
      v
SQLite / Postgres DB
```

- Both containers run on the same host; the bot calls glow-worm via a URL (e.g.
  `http://glowworm:8000` on a shared podman network, or via `localhost` with a mapped port).
- All configuration is loaded from environment variables so the bot image is portable.

---

## Configuration (Environment Variables)

| Variable              | Description                                                  |
|-----------------------|--------------------------------------------------------------|
| `DISCORD_TOKEN`       | Bot token from the Discord developer portal                  |
| `DISCORD_CHANNEL_ID`  | ID of the channel the bot monitors                           |
| `GLOWWORM_API_URL`    | Base URL of the glow-worm API (e.g. `http://glowworm:8000`)  |
| `GLOWWORM_API_KEY`    | API key (Bearer token) for authenticating with glow-worm     |
| `CONFIRM_TRANSACTIONS`| `true` (Phase 1 default) or `false` (Phase 2 auto-commit)    |
| `TIMEZONE`            | IANA timezone name for date resolution (default: `UTC`)      |

---

## Monitored Channel Behaviour

- The bot listens **only** in the channel identified by `DISCORD_CHANNEL_ID`.
- Every message posted by a non-bot user is passed through the parser.
- Messages that do not match any recognised pattern are silently ignored (no response clutter).
- The bot does **not** respond to commands prefixed with `!` or `/` — it processes plain English.

---

## Supported Message Patterns

### 1. Expense against a budget category

```
spent $20 groceries
spend 20 groceries
buy $4.50 coffee
bought 12 lunch yesterday
purchased $99 electronics last friday
```

**Fields extracted:**
- `amount` — numeric, `$` prefix optional
- `category_name` — matched against budget categories (expense type)
- `description` (optional) — any unrecognised words between amount and category, or after category
- `date` (optional) — natural-language date word/phrase; defaults to today

**Transaction produced (budget category):**
```json
{
  "date": "<resolved date>",
  "amount": <amount>,
  "description": "<description or null>",
  "category_id": <matched category id>,
  "type": "expense",
  "transaction_type": "budget_expense",
  "budget_id": <matched budget id for current month/year>
}
```

**Transaction produced (non-budget category):**
```json
{
  "date": "<resolved date>",
  "amount": <amount>,
  "description": "<description or null>",
  "category_id": <matched category id>,
  "type": "expense",
  "transaction_type": "regular"
}
```

---

### 2. Withdrawal from a sinking fund

```
spent $15 short term savings groceries
spend 50 holiday fund electronics
```

Same trigger words as expense. Name resolution falls through to sinking funds when no budget
category matches. A category name **must** follow the fund name — it is the last token(s) in
the remaining text after the fund name is consumed.

**Fields extracted:**
- `sinking_fund_name` — matched against active sinking fund names
- `category_name` — the token(s) remaining after the fund name is matched; required
- `description` — not applicable (fund name + category consume all tokens)
- `date` (optional) — defaults to today

**Transaction produced:**
```json
{
  "date": "<resolved date>",
  "amount": <amount>,
  "description": null,
  "category_id": <matched category id>,
  "type": "expense",
  "transaction_type": "withdrawal",
  "sinking_fund_id": <matched sinking fund id>
}
```

---

### 3. Contribution to a sinking fund

```
deposit $30 short term savings transfer
deposited 100 holiday fund transfer
save $50 car fund income
saved 25 emergency fund transfer
```

**Trigger words:** `deposit`, `deposited`, `save`, `saved`

A category name **must** follow the fund name, the same as withdrawals. Typically this will
be a "transfer" or "income" type category depending on your setup.

**Transaction produced:**
```json
{
  "date": "<resolved date>",
  "amount": <amount>,
  "description": null,
  "category_id": <matched category id>,
  "type": "income",
  "transaction_type": "contribution",
  "sinking_fund_id": <matched sinking fund id>
}
```

---

### 4. Variable recurring bill payment

```
paid electricity $120
paid electricity 98.50
```

**Trigger word:** `paid`

**Resolution:** Match the name against active recurring bills with `bill_type = "variable"`.
Fixed bills are automatically paid by the scheduler and are not handled by the bot.

**Transaction produced:**
```json
{
  "date": "<resolved date>",
  "amount": <amount>,
  "description": null,
  "category_id": <bill's category_id>,
  "type": "expense",
  "transaction_type": "regular",
  "recurring_bill_id": <matched bill id>
}
```

---

## Trigger Word Reference

| Intent                | Accepted words                                          |
|-----------------------|---------------------------------------------------------|
| Expense / withdrawal  | `spent`, `spend`, `buy`, `bought`, `purchase`, `purchased` |
| Contribution          | `deposit`, `deposited`, `save`, `saved`                 |
| Variable bill payment | `paid`                                                  |

---

## Parsing Algorithm

```
1. Lowercase and strip the message.
2. Match against the regex patterns (trigger word, amount, remaining tokens).
3. Extract: trigger, amount, remaining text, date hint (if any).
4. Resolve date:
     - "yesterday"        → today - 1 day
     - "today" / absent  → today (in the configured TIMEZONE)
     - "last <weekday>"  → most recent past occurrence of that weekday
     (expand as needed)
5. Remove date hint from remaining text.
6. For expense/contribution triggers:
     a. Search active budget categories by name (case-insensitive, partial match allowed).
     b. If a category matches:
          - Remaining tokens become the description (if any).
          - If category.is_budget_category = true:
               - transaction_type = "budget_expense"
               - Look up the budget for that category in the current month/year; attach budget_id.
               - If no budget exists for this month → error (see Ambiguity section).
          - If category.is_budget_category = false:
               - transaction_type = "regular"
               - No budget_id attached.
     c. If no budget category matches, search active sinking funds by name (greedy,
        consuming as many tokens as needed to find a match).
     d. If a sinking fund matches:
          - Tokens remaining after the fund name are matched against categories (required).
          - If no category tokens remain → error: "Please specify a category after the fund name."
          - If remaining tokens don't match a category → error with clarification prompt.
          - transaction_type = "withdrawal" (expense trigger) or "contribution" (deposit trigger).
     e. If no match in either pass → ask for clarification.
     f. If multiple matches at any step → ask for clarification.
7. For "paid" trigger:
     a. Extract amount from message (required).
     b. Match remaining tokens against variable recurring bill names.
     c. If no match or multiple matches → ask for clarification.
8. Build the transaction payload and proceed to confirmation or auto-commit.
```

---

## Regex Patterns (initial implementation)

```python
# Shared amount pattern: optional $, digits, optional decimal
AMOUNT_PAT = r"\$?(\d+(?:\.\d{1,2})?)"

# Date hint (at end of message)
DATE_PAT = r"(?:\s+(yesterday|today|last\s+\w+))?$"

# Expense / withdrawal
EXPENSE_RE = re.compile(
    r"^(spent|spend|buy|bought|purchase|purchased)\s+" + AMOUNT_PAT + r"\s+(.+?)" + DATE_PAT,
    re.IGNORECASE,
)

# Contribution
DEPOSIT_RE = re.compile(
    r"^(deposit|deposited|save|saved)\s+" + AMOUNT_PAT + r"\s+(.+?)" + DATE_PAT,
    re.IGNORECASE,
)

# Variable bill payment
BILL_RE = re.compile(
    r"^paid\s+(.+?)\s+" + AMOUNT_PAT + DATE_PAT,
    re.IGNORECASE,
)
```

---

## Name Resolution

### Step 1 — Fetch reference data

On startup (and refreshed every 60 minutes), the bot fetches and caches:
- `GET /api/categories` — all active categories
- `GET /api/sinking-funds` — all active sinking funds (not deleted)
- `GET /api/bills` — all active recurring bills

### Step 2 — Matching

Matching is **case-insensitive**. The algorithm tries, in order:
1. Exact name match
2. Starts-with match
3. Contains match (substring)

If step 2 or 3 yields multiple results, treat it as ambiguous.

### Step 3 — Budget ID resolution

When a budget category is matched, the bot looks up the budget for the current month/year:
`GET /api/budgets?category_id=<id>&month=<m>&year=<y>`

If no budget exists for that category/month, the bot warns the user and does not create
the transaction (budget must exist first).

---

## Confirmation Flow (Phase 1 — `CONFIRM_TRANSACTIONS=true`)

After successful parsing and name resolution, the bot replies with an embed:

```
Transaction Preview
-------------------
Type        : Expense (Budget)
Date        : 2026-03-07
Amount      : $20.00
Category    : Groceries
Budget      : Groceries (March 2026)
Description : chemist

React with ✅ to confirm or ❌ to cancel.
```

- The bot waits up to 60 seconds for a reaction from the message author.
- ✅ → calls `POST /api/transactions`, replies with a success message.
- ❌ or timeout → replies "Cancelled." and removes the preview.

---

## Auto-Commit Flow (Phase 2 — `CONFIRM_TRANSACTIONS=false`)

The bot immediately calls `POST /api/transactions` and replies with a compact success embed:

```
✅ Added $20.00 expense to Groceries
   Budget remaining: $145.00
   (March 2026)
```

For sinking funds:

```
✅ Deposited $30.00 to Short Term Savings
   Fund balance: $430.00
```

The remaining budget amount is derived from: `allocated_amount - spent_amount` on the budget
record returned by the API after the transaction is created. The fund balance comes from the
sinking fund record.

---

## Ambiguity & Error Handling

| Situation                               | Bot response                                                         |
|-----------------------------------------|----------------------------------------------------------------------|
| No name match (budget expense/deposit)  | "I couldn't find a category or fund matching '<name>'. If it's a new category, create it in glow-worm first at <GLOWWORM_API_URL>." |
| Multiple name matches                   | "I found multiple matches for '<name>': <list>. Which did you mean?" |
| Sinking fund matched but no category    | "Please specify a category after the fund name. E.g. `spent $15 short term savings groceries`" |
| Sinking fund matched, category tokens don't match | "I matched fund '<fund>' but couldn't find a category matching '<tokens>'. Create it in glow-worm first, or did you mean one of these: <list of close matches>?" |
| Budget category matched but no budget for this month | "Found category '<category>' but no budget for <Month Year>. Create a budget for it in glow-worm first at <GLOWWORM_API_URL>." |
| Amount missing or invalid               | "I couldn't read the amount. Try: `spent $20 groceries`"            |
| Variable bill amount missing            | "Please include the amount: `paid electricity $120`"                |
| API error (non-2xx)                     | "Something went wrong talking to glow-worm (HTTP <status>). Try again." |
| Network error                           | "I couldn't reach glow-worm. Is it running?"                        |
| Message doesn't match any pattern       | (silently ignored)                                                   |

---

## Project Structure

```
glow-worm-discord-bot/
├── SPEC.md
├── Containerfile           # Podman/Docker image definition
├── .env.example            # Template for environment variables
├── pyproject.toml          # Dependencies (discord.py, httpx, python-dotenv)
├── bot/
│   ├── __init__.py
│   ├── main.py             # Entry point: bot setup, event loop
│   ├── config.py           # Loads and validates env vars
│   ├── client.py           # Async HTTP client wrapping glow-worm API calls
│   ├── parser.py           # Regex patterns + parsing logic
│   ├── resolver.py         # Name resolution against cached API data
│   ├── handler.py          # Orchestrates parse → resolve → confirm → commit
│   └── cache.py            # Periodic refresh of categories/funds/bills
└── tests/
    ├── test_parser.py
    └── test_resolver.py
```

---

## Dependencies

| Package          | Purpose                              |
|------------------|--------------------------------------|
| `discord.py`     | Discord gateway and REST client      |
| `httpx`          | Async HTTP client for glow-worm API  |
| `python-dotenv`  | Load `.env` file locally             |

Python version: **3.12+**

---

## Containerfile (outline)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install .
COPY bot/ bot/
CMD ["python", "-m", "bot.main"]
```

Run alongside glow-worm on a shared podman network so the bot can reach it by service name.

---

## Out of Scope (v1)

- Querying transactions (planned for v2)
- Editing or deleting existing transactions
- Marking fixed recurring bills as paid (handled by glow-worm scheduler)
- Income allocation workflow
- Multi-user support (all transactions are created under the single API key owner)
- Slash commands / command prefix interactions

---

## Phase Roadmap

| Phase | Feature                                              |
|-------|------------------------------------------------------|
| 1     | Regex parser, confirm-before-commit, expense/deposit/bill patterns |
| 2     | Auto-commit with remaining balance display, cache refresh |
| 3     | Query support ("how much left in groceries?", "show last 5 transactions") |
| 4     | Consider upgrading NLP to Claude API for freeform input |
