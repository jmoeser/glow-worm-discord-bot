import asyncio
import logging
from datetime import datetime

import discord
import httpx

import bot.cache as cache
from bot import config
from bot.client import APIError, GlowWormClient
from bot.parser import ParseResult, parse, resolve_date
from bot.resolver import ResolveResult, resolve_bill, resolve_deposit, resolve_expense

logger = logging.getLogger(__name__)

CHECK = "\u2705"
CROSS = "\u274c"


async def handle(
    message: discord.Message,
    http_client: GlowWormClient,
    bot: discord.Client,
) -> None:
    """Main entry point: parse message, resolve names, confirm or auto-commit."""
    result = parse(message.content)
    if result is None:
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


def _build_payload(result: ParseResult, resolved: ResolveResult, today) -> dict:
    if resolved.transaction_type == "contribution":
        tx_type = "income"
    else:
        tx_type = "expense"

    payload: dict = {
        "date": today.isoformat(),
        "amount": result.amount,
        "description": resolved.description,
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
        msg += f" If it's a new category, create it in glow-worm first at {config.GLOWWORM_API_URL}."
    return msg


# ---------------------------------------------------------------------------
# Confirmation flow (Phase 1)
# ---------------------------------------------------------------------------


async def _confirm_flow(
    message: discord.Message,
    bot: discord.Client,
    http_client: GlowWormClient,
    payload: dict,
    result: ParseResult,
    resolved: ResolveResult,
) -> None:
    embed = _build_preview_embed(result, resolved, payload)
    preview_msg = await message.channel.send(embed=embed)
    await preview_msg.add_reaction(CHECK)
    await preview_msg.add_reaction(CROSS)

    def check(reaction, user):
        return (
            user == message.author
            and reaction.message.id == preview_msg.id
            and str(reaction.emoji) in (CHECK, CROSS)
        )

    try:
        reaction, _ = await bot.wait_for("reaction_add", check=check, timeout=60.0)
    except asyncio.TimeoutError:
        await message.channel.send("Cancelled.")
        return

    if str(reaction.emoji) == CROSS:
        await message.channel.send("Cancelled.")
        return

    await _commit(message, http_client, payload, result, resolved)


# ---------------------------------------------------------------------------
# Auto-commit flow (Phase 2)
# ---------------------------------------------------------------------------


async def _auto_commit(
    message: discord.Message,
    http_client: GlowWormClient,
    payload: dict,
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
    payload: dict,
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
    result: ParseResult, resolved: ResolveResult, payload: dict
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
    embed.add_field(name="Amount", value=f"${result.amount:.2f}", inline=False)

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
    payload: dict,
) -> discord.Embed:
    embed = discord.Embed(color=discord.Color.green())
    tx_type = resolved.transaction_type
    amount_str = f"${result.amount:.2f}"
    date_str = payload["date"]

    categories = cache.get_categories()
    cat = next((c for c in categories if c["id"] == resolved.category_id), None)
    cat_name = cat["name"] if cat else "Unknown"

    if tx_type == "budget_expense":
        d = datetime.fromisoformat(date_str).date()
        budgets = await http_client.get_budgets(resolved.category_id, d.month, d.year)
        if budgets:
            budget = budgets[0]
            remaining = budget.get("allocated_amount", 0) - budget.get("spent_amount", 0)
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
        fund_data = await http_client.get_sinking_fund(resolved.sinking_fund_id)
        balance = fund_data.get("current_balance", 0)
        embed.description = (
            f"{CHECK} Withdrew {amount_str} from {fund_name} \u2014 {cat_name}\n"
            f"   Fund balance: ${balance:.2f}"
        )

    elif tx_type == "contribution":
        funds = cache.get_sinking_funds()
        fund = next((f for f in funds if f["id"] == resolved.sinking_fund_id), None)
        fund_name = fund["name"] if fund else "Unknown"
        fund_data = await http_client.get_sinking_fund(resolved.sinking_fund_id)
        balance = fund_data.get("current_balance", 0)
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
