import httpx

from bot import config


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

    def _check(self, response: httpx.Response) -> dict | list:
        if response.is_success:
            return response.json()
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise APIError(response.status_code, str(detail))

    async def get_categories(self) -> list[dict]:
        r = await self._client.get("/api/categories")
        return self._check(r)  # type: ignore[return-value]

    async def get_sinking_funds(self) -> list[dict]:
        r = await self._client.get("/api/sinking-funds")
        return self._check(r)  # type: ignore[return-value]

    async def get_sinking_fund(self, fund_id: int) -> dict:
        r = await self._client.get(f"/api/sinking-funds/{fund_id}")
        return self._check(r)  # type: ignore[return-value]

    async def get_bills(self) -> list[dict]:
        r = await self._client.get("/api/bills")
        return self._check(r)  # type: ignore[return-value]

    async def get_budgets(
        self, category_id: int, month: int, year: int
    ) -> list[dict]:
        r = await self._client.get(
            "/api/budgets", params={"month": month, "year": year}
        )
        budgets: list[dict] = self._check(r)  # type: ignore[assignment]
        return [b for b in budgets if b.get("category_id") == category_id]

    async def create_transaction(self, payload: dict) -> dict:
        r = await self._client.post("/api/transactions", json=payload)
        return self._check(r)  # type: ignore[return-value]
