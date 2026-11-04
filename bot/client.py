from typing import Any, cast

import httpx

from bot import config
from bot.types import Bill, Budget, Category, DuePayment, SinkingFund, TransactionPayload


class APIError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API error {status_code}: {detail}")


class GlowWormClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=config.GLOWWORM_API_URL,
            headers={"Authorization": f"Bearer {config.GLOWWORM_API_KEY}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _check(self, response: httpx.Response) -> dict[str, Any] | list[Any]:
        if response.is_success:
            return cast(dict[str, Any] | list[Any], response.json())
        raise APIError(response.status_code, _error_detail(response))

    async def get_categories(self) -> list[Category]:
        r = await self._client.get("/api/categories")
        return self._check(r)  # type: ignore[return-value]

    async def get_sinking_funds(self) -> list[SinkingFund]:
        r = await self._client.get("/api/sinking-funds")
        return self._check(r)  # type: ignore[return-value]

    async def get_sinking_fund(self, fund_id: int) -> SinkingFund:
        r = await self._client.get(f"/api/sinking-funds/{fund_id}")
        return self._check(r)  # type: ignore[return-value]

    async def get_bills(self) -> list[Bill]:
        r = await self._client.get("/api/bills")
        return self._check(r)  # type: ignore[return-value]

    async def get_due_payments(self) -> list[DuePayment]:
        r = await self._client.get("/api/bills/due")
        return self._check(r)  # type: ignore[return-value]

    async def confirm_bill(self, bill_id: int, amount: str, due_date: str) -> dict[str, Any]:
        """Confirm one due occurrence. Amount is a 2-decimal string."""
        r = await self._client.post(
            f"/api/bills/{bill_id}/confirm",
            json={"amount": amount, "due_date": due_date},
        )
        return self._check(r)  # type: ignore[return-value]

    async def skip_bill(self, bill_id: int, due_date: str) -> dict[str, Any]:
        r = await self._client.post(
            f"/api/bills/{bill_id}/skip",
            json={"due_date": due_date},
        )
        return self._check(r)  # type: ignore[return-value]

    async def get_budgets(self, category_id: int, month: int, year: int) -> list[Budget]:
        r = await self._client.get("/api/budgets", params={"month": month, "year": year})
        budgets: list[Budget] = self._check(r)  # type: ignore[assignment]
        return [b for b in budgets if b["category_id"] == category_id]

    async def create_transaction(self, payload: TransactionPayload) -> None:
        r = await self._client.post("/api/transactions", json=payload)
        self._check(r)


def _error_detail(response: httpx.Response) -> str:
    """Plain sentence from a glow-worm error body.

    Confirm and skip return ``{"detail": "<sentence>", "code": "<code>"}``.
    Validation errors return ``detail`` as a list of ``{msg: ...}`` objects.
    """
    try:
        body = response.json()
    except Exception:
        return response.text
    detail = body.get("detail", response.text) if isinstance(body, dict) else response.text
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts: list[str] = []
        for item in detail:
            if isinstance(item, dict) and item.get("msg"):
                parts.append(str(item["msg"]))
            else:
                parts.append(str(item))
        return "; ".join(parts) if parts else response.text
    return str(detail)
