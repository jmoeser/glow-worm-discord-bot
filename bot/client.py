from typing import Any, cast

import httpx

from bot import config
from bot.types import Bill, Budget, Category, SinkingFund, TransactionPayload


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
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise APIError(response.status_code, str(detail))

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

    async def get_budgets(self, category_id: int, month: int, year: int) -> list[Budget]:
        r = await self._client.get("/api/budgets", params={"month": month, "year": year})
        budgets: list[Budget] = self._check(r)  # type: ignore[assignment]
        return [b for b in budgets if b["category_id"] == category_id]

    async def create_transaction(self, payload: TransactionPayload) -> None:
        r = await self._client.post("/api/transactions", json=payload)
        self._check(r)
