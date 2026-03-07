from dataclasses import dataclass, field
from datetime import date

import bot.cache as cache
from bot.client import GlowWormClient


@dataclass
class ResolveResult:
    transaction_type: str = ""
    category_id: int | None = None
    budget_id: int | None = None
    sinking_fund_id: int | None = None
    bill_id: int | None = None
    description: str | None = None
    error: str | None = None
    error_message: str | None = None
    ambiguous_names: list[str] = field(default_factory=list)


def match_name(tokens: list[str], candidates: list[dict]) -> list[dict]:
    """Three-tier case-insensitive name matching: exact → starts-with → contains."""
    query = " ".join(tokens).lower()

    exact = [c for c in candidates if c["name"].lower() == query]
    if exact:
        return exact

    starts = [c for c in candidates if c["name"].lower().startswith(query)]
    if starts:
        return starts

    return [c for c in candidates if query in c["name"].lower()]


def _find_in_tokens(
    tokens: list[str],
    candidates: list[dict],
    longest_first: bool = True,
) -> tuple[list[dict], int]:
    """
    Find the best-matching prefix of tokens against candidates.

    Tries exact → starts-with → contains across all prefix lengths.
    Returns (matches, prefix_length). Returns ([], 0) if nothing found.

    When longest_first=True, prefers longer prefixes within the same tier
    (good for categories where longer match = less description ambiguity).
    When longest_first=False, prefers shorter prefixes within the same tier
    (greedy minimum for sinking fund name identification).
    """
    # Collect (tier, length, matches) for all single-result prefix lengths
    # tier: 3=exact, 2=starts-with, 1=contains
    good: list[tuple[int, int, list[dict]]] = []

    for i in range(1, len(tokens) + 1):
        query = " ".join(tokens[:i]).lower()

        exact = [c for c in candidates if c["name"].lower() == query]
        if len(exact) == 1:
            good.append((3, i, exact))
            continue

        starts = [c for c in candidates if c["name"].lower().startswith(query)]
        if len(starts) == 1:
            good.append((2, i, starts))
            continue

        contains = [c for c in candidates if query in c["name"].lower()]
        if len(contains) == 1:
            good.append((1, i, contains))

    if not good:
        return [], 0

    # Sort: highest tier first; within same tier, prefer longer or shorter prefix
    good.sort(key=lambda x: (x[0], x[1] if longest_first else -x[1]), reverse=True)
    _, length, matches = good[0]
    return matches, length


def _find_ambiguous(tokens: list[str], candidates: list[dict]) -> list[dict]:
    """Return ambiguous matches if any prefix gives multiple results at the highest tier."""
    for i in range(len(tokens), 0, -1):
        query = " ".join(tokens[:i]).lower()
        exact = [c for c in candidates if c["name"].lower() == query]
        if len(exact) > 1:
            return exact
        starts = [c for c in candidates if c["name"].lower().startswith(query)]
        if len(starts) > 1:
            return starts
        contains = [c for c in candidates if query in c["name"].lower()]
        if len(contains) > 1:
            return contains
    return []


async def resolve_expense(
    raw_tokens: list[str], client: GlowWormClient, today: date
) -> ResolveResult:
    """
    Resolve expense tokens: try categories first (longest-first prefix),
    then sinking funds (best-match prefix).
    """
    categories = cache.get_categories()

    cat_matches, name_len = _find_in_tokens(raw_tokens, categories, longest_first=True)

    if len(cat_matches) == 1:
        cat = cat_matches[0]
        description = " ".join(raw_tokens[name_len:]) or None

        if cat.get("is_budget_category"):
            budgets = await client.get_budgets(cat["id"], today.month, today.year)
            if not budgets:
                return ResolveResult(
                    transaction_type="budget_expense",
                    error="no_budget",
                    error_message=(
                        f"Found category '{cat['name']}' but no budget for "
                        f"{today.strftime('%B %Y')}. Create a budget for it in "
                        "glow-worm first."
                    ),
                )
            return ResolveResult(
                transaction_type="budget_expense",
                category_id=cat["id"],
                budget_id=budgets[0]["id"],
                description=description,
            )
        return ResolveResult(
            transaction_type="regular",
            category_id=cat["id"],
            description=description,
        )

    # Check for ambiguous category match before falling through to funds
    if len(cat_matches) > 1:
        query = " ".join(raw_tokens[:name_len])
        return ResolveResult(
            error="ambiguous",
            error_message=(
                f"I found multiple matches for '{query}': "
                f"{', '.join(m['name'] for m in cat_matches)}. Which did you mean?"
            ),
            ambiguous_names=[m["name"] for m in cat_matches],
        )

    # No category match — try sinking funds
    sinking_funds = cache.get_sinking_funds()
    fund_matches, fund_len = _find_in_tokens(raw_tokens, sinking_funds, longest_first=True)

    if len(fund_matches) == 1:
        return _build_fund_result(
            fund_matches[0], fund_len, raw_tokens, categories, "withdrawal"
        )

    if len(fund_matches) > 1:
        query = " ".join(raw_tokens[:fund_len])
        return ResolveResult(
            error="ambiguous",
            error_message=(
                f"I found multiple matches for '{query}': "
                f"{', '.join(m['name'] for m in fund_matches)}. Which did you mean?"
            ),
            ambiguous_names=[m["name"] for m in fund_matches],
        )

    # Check for any ambiguous candidates
    ambig = _find_ambiguous(raw_tokens, categories) or _find_ambiguous(raw_tokens, sinking_funds)
    if ambig:
        query = " ".join(raw_tokens)
        return ResolveResult(
            error="ambiguous",
            error_message=(
                f"I found multiple matches for '{query}': "
                f"{', '.join(m['name'] for m in ambig)}. Which did you mean?"
            ),
            ambiguous_names=[m["name"] for m in ambig],
        )

    query = " ".join(raw_tokens)
    return ResolveResult(
        error="no_match",
        error_message=f"I couldn't find a category or fund matching '{query}'.",
    )


async def resolve_deposit(
    raw_tokens: list[str], client: GlowWormClient, today: date
) -> ResolveResult:
    """Resolve deposit: match sinking fund (best prefix), then remaining tokens as category."""
    sinking_funds = cache.get_sinking_funds()
    categories = cache.get_categories()

    fund_matches, fund_len = _find_in_tokens(raw_tokens, sinking_funds, longest_first=True)

    if len(fund_matches) == 1:
        return _build_fund_result(
            fund_matches[0], fund_len, raw_tokens, categories, "contribution"
        )

    if len(fund_matches) > 1:
        query = " ".join(raw_tokens[:fund_len])
        return ResolveResult(
            error="ambiguous",
            error_message=(
                f"I found multiple matches for '{query}': "
                f"{', '.join(m['name'] for m in fund_matches)}. Which did you mean?"
            ),
            ambiguous_names=[m["name"] for m in fund_matches],
        )

    ambig = _find_ambiguous(raw_tokens, sinking_funds)
    if ambig:
        query = " ".join(raw_tokens)
        return ResolveResult(
            error="ambiguous",
            error_message=(
                f"I found multiple matches for '{query}': "
                f"{', '.join(m['name'] for m in ambig)}. Which did you mean?"
            ),
            ambiguous_names=[m["name"] for m in ambig],
        )

    query = " ".join(raw_tokens)
    return ResolveResult(
        error="no_match",
        error_message=f"I couldn't find a sinking fund matching '{query}'.",
    )


def resolve_bill(raw_tokens: list[str]) -> ResolveResult:
    """Resolve bill: match against variable bills only."""
    bills = cache.get_bills()
    matches = match_name(raw_tokens, bills)

    if len(matches) == 1:
        bill = matches[0]
        return ResolveResult(
            transaction_type="regular",
            bill_id=bill["id"],
            category_id=bill.get("category_id"),
        )

    if len(matches) > 1:
        query = " ".join(raw_tokens)
        return ResolveResult(
            error="ambiguous",
            error_message=(
                f"I found multiple matches for '{query}': "
                f"{', '.join(m['name'] for m in matches)}. Which did you mean?"
            ),
            ambiguous_names=[m["name"] for m in matches],
        )

    query = " ".join(raw_tokens)
    return ResolveResult(
        error="no_match",
        error_message=f"I couldn't find a variable bill matching '{query}'.",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_fund_result(
    fund: dict,
    fund_len: int,
    raw_tokens: list[str],
    categories: list[dict],
    tx_type: str,
) -> ResolveResult:
    remaining = raw_tokens[fund_len:]

    if not remaining:
        example_cat = "groceries" if tx_type == "withdrawal" else "transfer"
        trigger = "spent" if tx_type == "withdrawal" else "deposit"
        return ResolveResult(
            transaction_type=tx_type,
            error="no_category",
            error_message=(
                f"Please specify a category after the fund name. "
                f"E.g. `{trigger} $15 {fund['name'].lower()} {example_cat}`"
            ),
        )

    cat_matches = match_name(remaining, categories)

    if len(cat_matches) == 1:
        return ResolveResult(
            transaction_type=tx_type,
            sinking_fund_id=fund["id"],
            category_id=cat_matches[0]["id"],
        )

    if len(cat_matches) > 1:
        return ResolveResult(
            transaction_type=tx_type,
            error="ambiguous",
            error_message=(
                f"I found multiple matches for '{' '.join(remaining)}': "
                f"{', '.join(m['name'] for m in cat_matches)}. Which did you mean?"
            ),
            ambiguous_names=[m["name"] for m in cat_matches],
        )

    return ResolveResult(
        transaction_type=tx_type,
        error="no_match_category",
        error_message=(
            f"I matched fund '{fund['name']}' but couldn't find a category "
            f"matching '{' '.join(remaining)}'."
        ),
    )
