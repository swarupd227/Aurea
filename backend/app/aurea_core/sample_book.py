"""A synthetic acquired-book feed for the book-integration scenario.

Deliberately includes real-world messiness: a client that already exists in the brain (to test
merge + a holding conflict), securities not in the instrument master (to test creation), and a
capital-call notice (to test document intelligence)."""
from __future__ import annotations

from app.aurea_core import sample_docs


def sample_feed(source_firm: str = "Northbridge Advisory") -> dict:
    return {
        "source_firm": source_firm,
        "clients": [
            {"name": "Patel Household", "email": "patel@northbridge.example"},   # → merges with existing
            {"name": "Rangi & Aroha Williams", "email": "williams@northbridge.example"},
            {"name": "Henderson Family", "email": "henderson@northbridge.example"},
        ],
        "securities": [
            {"symbol": "AAPL", "name": "Apple Inc.", "asset_class": "equity", "last_price": 297.0, "currency": "USD"},
            {"symbol": "AGG", "name": "iShares Core US Aggregate Bond ETF", "asset_class": "fixed_income", "last_price": 98.0, "currency": "USD"},
            {"symbol": "TSLA", "name": "Tesla Inc.", "asset_class": "equity", "last_price": 250.0, "currency": "USD"},   # unmapped → create
            {"symbol": "NVDA", "name": "NVIDIA Corp.", "asset_class": "equity", "last_price": 140.0, "currency": "USD"}, # unmapped → create
        ],
        "holdings": [
            {"client": "Patel Household", "symbol": "AAPL", "quantity": 250, "cost_basis": 40000},  # conflict (brain has 200)
            {"client": "Rangi & Aroha Williams", "symbol": "TSLA", "quantity": 300, "cost_basis": 75000},
            {"client": "Rangi & Aroha Williams", "symbol": "AGG", "quantity": 500, "cost_basis": 49000},
            {"client": "Henderson Family", "symbol": "NVDA", "quantity": 400, "cost_basis": 52000},
            {"client": "Henderson Family", "symbol": "AAPL", "quantity": 150, "cost_basis": 28000},
        ],
        "capital_calls": [
            {"filename": "ppef2_call_henderson.pdf",
             "raw_text": sample_docs.capital_call_notice(
                 fund="Pacific Private Equity Fund II", investor="Henderson Family",
                 amount="USD 150,000", due="2026-09-30")},
        ],
    }
