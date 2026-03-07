"""TypedDicts mirroring the glow-worm API response shapes used by this bot."""

from typing import TypedDict


class Category(TypedDict):
    id: int
    name: str
    is_budget_category: bool


class SinkingFund(TypedDict):
    id: int
    name: str
    current_balance: float


class Bill(TypedDict):
    id: int
    name: str
    bill_type: str
    category_id: int


class Budget(TypedDict):
    id: int
    category_id: int
    allocated_amount: float
    spent_amount: float


class _TransactionPayloadRequired(TypedDict):
    date: str
    amount: float
    description: str | None
    category_id: int | None
    type: str
    transaction_type: str


class TransactionPayload(_TransactionPayloadRequired, total=False):
    budget_id: int
    sinking_fund_id: int
    recurring_bill_id: int
