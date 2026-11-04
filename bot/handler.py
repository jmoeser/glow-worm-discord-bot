import logging
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import discord
import httpx

import bot.cache as cache
from bot import config
from bot.client import APIError, GlowWormClient
from bot.config import MAX_TRANSACTION_AMOUNT
from bot.parser import ParseResult, parse, resolve_date
from bot.resolver import ResolveResult, match_name, resolve_bill, resolve_deposit, resolve_expense
from bot.types import DuePayment, TransactionPayload

logger = logging.getLogger(__name__)

CHECK = "\u2705"
CROSS = "\u274c"

# Matches Transaction.description column length in glow-worm (models.py String(500)).
_MAX_DESCRIPTION_LEN = 500

# Tracks user IDs with an active confirmation flow to prevent concurrent duplicates.
_pending_confirmations: set[int] = set()


async def handle(
    message: discord.Message,
    http_client: GlowWormClient,
    bot: discord.Client,
) -> None:
    """Main entry point: parse message, resolve names, confirm or auto-commit."""
    result = parse(message.content)
    if result is None:
        return

    if result.intent == "due":
        await _list_due(message, http_client)
        return

    if result.intent == "skip":
        await _skip_due(message, http_client, result)
        return

    if result.amount is not None and result.amount > MAX_TRANSACTION_AMOUNT:
        await message.channel.send(_over_limit_text(result.amount))
        return

    # A due payment is confirmed through glow-worm. Anything else keeps the
    # variable-bill transaction path below.
    if result.intent == "bill" and await _confirm_due_if_matched(message, http_client, result):
        return

    today = resolve_date(result.raw_date_hint)

    if result.intent == "expense":
        resolved = await resolve_expense(result.raw_name_tokens, http_client, today)
    elif result.intent == "deposit":
        resolved = await resolve_deposit(result.raw_name_tokens, http_client, today)
    else:
        resolved = resolve_bill(result.raw_name_tokens)

    if resolved.error:
        await message.channel.send(_error_text(resolved, result))
        return

    payload = _build_payload(result, resolved, today)

    if config.CONFIRM_TRANSACTIONS:
        await _confirm_flow(message, bot, http_client, payload, result, resolved)
    else:
        await _auto_commit(message, http_client, payload, result, resolved)


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------


def _build_payload(result: ParseResult, resolved: ResolveResult, today: date) -> TransactionPayload:
    if resolved.transaction_type == "contribution":
        tx_type = "income"
    else:
        tx_type = "expense"

    description = resolved.description[:_MAX_DESCRIPTION_LEN] if resolved.description else None

    payload: TransactionPayload = {
        "date": today.isoformat(),
        "amount": _require_amount(result),
        "description": description,
        "category_id": resolved.category_id,
        "type": tx_type,
        "transaction_type": resolved.transaction_type,
    }

    if resolved.budget_id is not None:
        payload["budget_id"] = resolved.budget_id
    if resolved.sinking_fund_id is not None:
        payload["sinking_fund_id"] = resolved.sinking_fund_id
    if resolved.bill_id is not None:
        payload["recurring_bill_id"] = resolved.bill_id

    return payload


# ---------------------------------------------------------------------------
# Error messages
# ---------------------------------------------------------------------------


def _error_text(resolved: ResolveResult, result: ParseResult) -> str:
    msg = resolved.error_message or "An error occurred."
    if resolved.error == "no_match" and result.intent in ("expense", "deposit"):
        msg += (
            f" If it's a new category, create it in glow-worm first at {config.GLOWWORM_API_URL}."  # noqa: E501
        )
    return msg


# ---------------------------------------------------------------------------
# Confirmation flow (Phase 1)
# ---------------------------------------------------------------------------


async def _confirm_flow(
    message: discord.Message,
    bot: discord.Client,
    http_client: GlowWormClient,
    payload: TransactionPayload,
    result: ParseResult,
    resolved: ResolveResult,
) -> None:
    user_id = message.author.id
    if user_id in _pending_confirmations:
        await message.channel.send(
            "You already have a transaction waiting for confirmation. "
            "Please react to the existing preview first."
        )
        return

    _pending_confirmations.add(user_id)
    try:
        embed = _build_preview_embed(result, resolved, payload)
        preview_msg = await message.channel.send(embed=embed)
        await preview_msg.add_reaction(CHECK)
        await preview_msg.add_reaction(CROSS)

        def check(reaction: discord.Reaction, user: discord.abc.User) -> bool:
            return (
                user == message.author
                and reaction.message.id == preview_msg.id
                and str(reaction.emoji) in (CHECK, CROSS)
            )

        try:
            reaction, _ = await bot.wait_for("reaction_add", check=check, timeout=60.0)
        except TimeoutError:
            await message.channel.send("Cancelled.")
            return

        if str(reaction.emoji) == CROSS:
            await message.channel.send("Cancelled.")
            return

        await _commit(message, http_client, payload, result, resolved)
    finally:
        _pending_confirmations.discard(user_id)


# ---------------------------------------------------------------------------
# Auto-commit flow (Phase 2)
# ---------------------------------------------------------------------------


async def _auto_commit(
    message: discord.Message,
    http_client: GlowWormClient,
    payload: TransactionPayload,
    result: ParseResult,
    resolved: ResolveResult,
) -> None:
    await _commit(message, http_client, payload, result, resolved)


# ---------------------------------------------------------------------------
# Shared commit + success embed
# ---------------------------------------------------------------------------


async def _commit(
    message: discord.Message,
    http_client: GlowWormClient,
    payload: TransactionPayload,
    result: ParseResult,
    resolved: ResolveResult,
) -> None:
    try:
        await http_client.create_transaction(payload)
        embed = await _build_success_embed(http_client, result, resolved, payload)
        await message.channel.send(embed=embed)
    except APIError as e:
        await message.channel.send(
            f"Something went wrong talking to glow-worm (HTTP {e.status_code}). Try again."
        )
    except httpx.RequestError:
        await message.channel.send("I couldn't reach glow-worm. Is it running?")


# ---------------------------------------------------------------------------
# Preview embed
# ---------------------------------------------------------------------------


def _build_preview_embed(
    result: ParseResult, resolved: ResolveResult, payload: TransactionPayload
) -> discord.Embed:
    tx_type = resolved.transaction_type
    if tx_type == "budget_expense":
        type_label = "Expense (Budget)"
    elif tx_type == "withdrawal":
        type_label = "Sinking Fund Withdrawal"
    elif tx_type == "contribution":
        type_label = "Sinking Fund Contribution"
    else:
        type_label = "Expense"

    embed = discord.Embed(title="Transaction Preview", color=discord.Color.blue())
    embed.add_field(name="Type", value=type_label, inline=False)
    embed.add_field(name="Date", value=payload["date"], inline=False)
    embed.add_field(name="Amount", value=f"${_require_amount(result):.2f}", inline=False)

    categories = cache.get_categories()
    cat = next((c for c in categories if c["id"] == resolved.category_id), None)
    if cat:
        embed.add_field(name="Category", value=cat["name"], inline=False)

    if resolved.budget_id is not None:
        d = datetime.fromisoformat(payload["date"])
        budget_label = f"{cat['name'] if cat else ''} ({d.strftime('%B %Y')})"
        embed.add_field(name="Budget", value=budget_label, inline=False)

    if resolved.sinking_fund_id is not None:
        funds = cache.get_sinking_funds()
        fund = next((f for f in funds if f["id"] == resolved.sinking_fund_id), None)
        if fund:
            embed.add_field(name="Fund", value=fund["name"], inline=False)

    if resolved.bill_id is not None:
        bills = cache.get_bills()
        bill = next((b for b in bills if b["id"] == resolved.bill_id), None)
        if bill:
            embed.add_field(name="Bill", value=bill["name"], inline=False)

    if resolved.description:
        embed.add_field(name="Description", value=resolved.description, inline=False)

    embed.set_footer(text=f"React with {CHECK} to confirm or {CROSS} to cancel.")
    return embed


# ---------------------------------------------------------------------------
# Success embed
# ---------------------------------------------------------------------------


async def _build_success_embed(
    http_client: GlowWormClient,
    result: ParseResult,
    resolved: ResolveResult,
    payload: TransactionPayload,
) -> discord.Embed:
    embed = discord.Embed(color=discord.Color.green())
    tx_type = resolved.transaction_type
    amount_str = f"${_require_amount(result):.2f}"
    date_str = payload["date"]

    categories = cache.get_categories()
    cat = next((c for c in categories if c["id"] == resolved.category_id), None)
    cat_name = cat["name"] if cat else "Unknown"

    if tx_type == "budget_expense":
        d = datetime.fromisoformat(date_str).date()
        if resolved.category_id is None:
            logger.error("budget_expense transaction missing category_id in success embed")
            embed.description = f"{CHECK} Added {amount_str}"
            return embed
        budgets = await http_client.get_budgets(resolved.category_id, d.month, d.year)
        if budgets:
            budget = budgets[0]
            remaining = budget["allocated_amount"] - budget["spent_amount"]
            month_label = d.strftime("%B %Y")
            embed.description = (
                f"{CHECK} Added {amount_str} to {cat_name} ({month_label})\n"
                f"   Budget remaining: ${remaining:.2f}"
            )
        else:
            embed.description = f"{CHECK} Added {amount_str} to {cat_name}"

    elif tx_type == "withdrawal":
        funds = cache.get_sinking_funds()
        fund = next((f for f in funds if f["id"] == resolved.sinking_fund_id), None)
        fund_name = fund["name"] if fund else "Unknown"
        if resolved.sinking_fund_id is None:
            logger.error("withdrawal transaction missing sinking_fund_id in success embed")
            embed.description = f"{CHECK} Withdrew {amount_str} \u2014 {cat_name}"
            return embed
        fund_data = await http_client.get_sinking_fund(resolved.sinking_fund_id)
        balance = fund_data["current_balance"]
        embed.description = (
            f"{CHECK} Withdrew {amount_str} from {fund_name} \u2014 {cat_name}\n"
            f"   Fund balance: ${balance:.2f}"
        )

    elif tx_type == "contribution":
        funds = cache.get_sinking_funds()
        fund = next((f for f in funds if f["id"] == resolved.sinking_fund_id), None)
        fund_name = fund["name"] if fund else "Unknown"
        if resolved.sinking_fund_id is None:
            logger.error("contribution transaction missing sinking_fund_id in success embed")
            embed.description = f"{CHECK} Deposited {amount_str} \u2014 {cat_name}"
            return embed
        fund_data = await http_client.get_sinking_fund(resolved.sinking_fund_id)
        balance = fund_data["current_balance"]
        embed.description = (
            f"{CHECK} Deposited {amount_str} to {fund_name} \u2014 {cat_name}\n"
            f"   Fund balance: ${balance:.2f}"
        )

    elif resolved.bill_id is not None:
        bills = cache.get_bills()
        bill = next((b for b in bills if b["id"] == resolved.bill_id), None)
        bill_name = bill["name"] if bill else "Unknown"
        embed.description = f"{CHECK} Paid {amount_str} for {bill_name}"

    else:
        embed.description = f"{CHECK} Added {amount_str} to {cat_name}"

    return embed


# ---------------------------------------------------------------------------
# Due payments: confirm, skip, list
# ---------------------------------------------------------------------------


def _require_amount(result: ParseResult) -> float:
    if result.amount is None:
        raise RuntimeError("Cannot log a transaction without an amount.")
    return result.amount


def _over_limit_text(amount: float) -> str:
    return (
        f"${amount:.2f} exceeds the maximum allowed amount of "
        f"${MAX_TRANSACTION_AMOUNT:.2f}. Update MAX_TRANSACTION_AMOUNT to raise the limit."
    )


def _plain_api_error(exc: APIError) -> str:
    detail = exc.detail.strip()
    if detail:
        return detail
    return f"Something went wrong talking to glow-worm (HTTP {exc.status_code}). Try again."


def _amount_str(value: object) -> str | None:
    """Two-decimal amount string for RecurringBillConfirm, or None if unusable."""
    try:
        dec = Decimal(str(value))
    except InvalidOperation, ValueError:
        return None
    if not dec.is_finite() or dec <= 0:
        return None
    quantized = dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(quantized, "f")


def _format_due_date(iso: str) -> str:
    try:
        parsed = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{parsed.day} {parsed.strftime('%b %Y')}"


def _problem_detail(row: DuePayment) -> str | None:
    problem = row["problem"]
    if not problem:
        return None
    detail = problem.get("detail", "").strip()
    return detail or None


def _format_due_line(row: DuePayment) -> str:
    amount = _amount_str(row["expected_amount"]) or str(row["expected_amount"])
    extra = f" — +{row['more_due']} more" if row["more_due"] else ""
    line = (
        f"• {row['name']} — ${amount} — {row['paid_from_label']} — "
        f"due {_format_due_date(row['due_date'])}{extra}"
    )
    problem = _problem_detail(row)
    if problem:
        line += f"\n  {problem}"
    return line


def _ambiguous_due_text(query: str, matches: list[DuePayment]) -> str:
    labels = ", ".join(
        f"{row['name']} ({row['paid_from_label']}, due {_format_due_date(row['due_date'])})"
        for row in matches
    )
    return f"I found multiple matches for '{query}': {labels}. Which did you mean?"


def _reply_message(body: object, fallback: str) -> str:
    if isinstance(body, dict):
        text = body.get("message")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return fallback


async def _load_due(
    message: discord.Message, http_client: GlowWormClient
) -> list[DuePayment] | None:
    """Fetch the due list. Sends an error and returns None when the call fails."""
    try:
        return await http_client.get_due_payments()
    except APIError as exc:
        await message.channel.send(_plain_api_error(exc))
        return None
    except httpx.RequestError:
        await message.channel.send("I couldn't reach glow-worm. Is it running?")
        return None


def _match_due(tokens: list[str], due: list[DuePayment]) -> tuple[str, list[DuePayment]]:
    query = " ".join(tokens)
    if not tokens:
        return query, []
    return query, match_name(tokens, due)


async def _list_due(message: discord.Message, http_client: GlowWormClient) -> None:
    due = await _load_due(message, http_client)
    if due is None:
        return
    if not due:
        await message.channel.send("Nothing is due to confirm.")
        return
    lines = ["Due to confirm:", *(_format_due_line(row) for row in due)]
    await message.channel.send("\n".join(lines))


async def _skip_due(
    message: discord.Message, http_client: GlowWormClient, result: ParseResult
) -> None:
    due = await _load_due(message, http_client)
    if due is None:
        return

    query, matches = _match_due(result.raw_name_tokens, due)
    if len(matches) > 1:
        await message.channel.send(_ambiguous_due_text(query, matches))
        return
    if len(matches) == 0:
        await message.channel.send(f"I couldn't find a due payment matching '{query}'.")
        return

    row = matches[0]
    logger.info("Skipping due payment bill_id=%s due_date=%s", row["bill_id"], row["due_date"])
    try:
        body = await http_client.skip_bill(row["bill_id"], row["due_date"])
    except APIError as exc:
        logger.info(
            "glow-worm rejected skip for bill %s: HTTP %s %s",
            row["bill_id"],
            exc.status_code,
            exc.detail,
        )
        await message.channel.send(_plain_api_error(exc))
        return
    except httpx.RequestError:
        await message.channel.send("I couldn't reach glow-worm. Is it running?")
        return

    await message.channel.send(_reply_message(body, "Skipped."))


async def _confirm_due_if_matched(
    message: discord.Message, http_client: GlowWormClient, result: ParseResult
) -> bool:
    """Confirm a due payment when the name matches.

    Returns True when this message has been answered. Returns False when
    nothing due matches and an amount was given, so the caller can log a
    variable-bill transaction the way it always has.
    """
    due = await _load_due(message, http_client)
    if due is None:
        return True

    query, matches = _match_due(result.raw_name_tokens, due)
    if len(matches) > 1:
        await message.channel.send(_ambiguous_due_text(query, matches))
        return True
    if len(matches) == 1:
        await _confirm_one_due(message, http_client, matches[0], result)
        return True

    if result.amount is None:
        await message.channel.send(
            f"I couldn't find a due payment matching '{query}'. "
            f"To log a variable bill, include the amount: `paid {query} $120`"
        )
        return True
    return False


async def _confirm_one_due(
    message: discord.Message,
    http_client: GlowWormClient,
    row: DuePayment,
    result: ParseResult,
) -> None:
    problem = _problem_detail(row)
    if problem:
        await message.channel.send(problem)
        return

    raw_amount: object = result.amount if result.amount is not None else row["expected_amount"]
    amount = _amount_str(raw_amount)
    if amount is None:
        await message.channel.send(f"Please include the amount: `paid {row['name']} $120`")
        return

    if float(amount) > MAX_TRANSACTION_AMOUNT:
        await message.channel.send(_over_limit_text(float(amount)))
        return

    logger.info(
        "Confirming due payment bill_id=%s due_date=%s amount=%s",
        row["bill_id"],
        row["due_date"],
        amount,
    )
    try:
        body = await http_client.confirm_bill(row["bill_id"], amount, row["due_date"])
    except APIError as exc:
        logger.info(
            "glow-worm rejected confirm for bill %s: HTTP %s %s",
            row["bill_id"],
            exc.status_code,
            exc.detail,
        )
        await message.channel.send(_plain_api_error(exc))
        return
    except httpx.RequestError:
        await message.channel.send("I couldn't reach glow-worm. Is it running?")
        return

    await message.channel.send(_reply_message(body, "Confirmed."))
