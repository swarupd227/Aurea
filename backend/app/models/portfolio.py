"""Instruments, prices, holdings, tax lots, transactions and target models.

These carry the substance of monitoring, tax-managed rebalancing and the total-portfolio
view (spec Table 7). Every value-bearing row carries lineage + confidence (spec §6.2)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import AssetClass, MarketType


class Instrument(Base):
    """A tradable (or private) instrument. Public instruments price off the market feed."""

    __tablename__ = "instrument"
    __table_args__ = (UniqueConstraint("firm_id", "symbol", name="uq_instrument_symbol"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)  # e.g. AAPL, AIR.NZ
    name: Mapped[str] = mapped_column(String(200))
    asset_class: Mapped[AssetClass] = mapped_column(String(24))
    market_type: Mapped[MarketType] = mapped_column(String(12), default=MarketType.PUBLIC)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    # For real market data: the symbol used at the data provider (e.g. stooq).
    market_symbol: Mapped[str | None] = mapped_column(String(48))
    # Values tags for values-aligned screening, e.g. {"esg":"high","sin":false,"sectors":[...]}
    values_tags: Mapped[dict] = mapped_column(JSON, default=dict)
    # For alternatives/private: liquidity, pacing, look-through.
    private_attributes: Mapped[dict] = mapped_column(JSON, default=dict)


class Price(Base):
    """A point-in-time price for an instrument, with lineage to its source."""

    __tablename__ = "price"
    __table_args__ = (UniqueConstraint("instrument_id", "as_of", name="uq_price_point"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("instrument.id", ondelete="CASCADE"), index=True
    )
    as_of: Mapped[date] = mapped_column(Date)
    close: Mapped[float] = mapped_column(Numeric(20, 6))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    source: Mapped[str] = mapped_column(String(48), default="stooq")  # lineage
    is_real: Mapped[bool] = mapped_column(Boolean, default=False)  # real feed vs synthetic


class Holding(Base):
    """A position in an account. The golden-record holding is resolved across custodians."""

    __tablename__ = "holding"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("instrument.id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[float] = mapped_column(Numeric(24, 6))
    # Cached valuation (refreshed by the aggregation service) in account currency.
    market_value: Mapped[float] = mapped_column(Numeric(20, 2), default=0)
    cost_basis: Mapped[float] = mapped_column(Numeric(20, 2), default=0)
    # Lineage + confidence for this holding (spec §6.2/§6.4).
    lineage: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=1.0)

    account: Mapped["Account"] = relationship(back_populates="holdings")
    instrument: Mapped["Instrument"] = relationship()
    tax_lots: Mapped[list["TaxLot"]] = relationship(
        back_populates="holding", cascade="all, delete-orphan"
    )


class TaxLot(Base):
    """A tax lot underpinning a holding — drives tax-lot selection & loss harvesting."""

    __tablename__ = "tax_lot"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    holding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("holding.id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[float] = mapped_column(Numeric(24, 6))
    cost_per_unit: Mapped[float] = mapped_column(Numeric(20, 6))
    acquired_on: Mapped[date] = mapped_column(Date)

    holding: Mapped["Holding"] = relationship(back_populates="tax_lots")


class Transaction(Base):
    """A historical transaction (buy/sell/dividend/capital-call/distribution)."""

    __tablename__ = "txn"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("instrument.id", ondelete="SET NULL"), nullable=True
    )
    txn_type: Mapped[str] = mapped_column(String(24))  # buy/sell/dividend/capital_call/distribution
    quantity: Mapped[float] = mapped_column(Numeric(24, 6), default=0)
    price: Mapped[float] = mapped_column(Numeric(20, 6), default=0)
    amount: Mapped[float] = mapped_column(Numeric(20, 2), default=0)
    trade_date: Mapped[date] = mapped_column(Date)
    lineage: Mapped[dict] = mapped_column(JSON, default=dict)


class ModelPortfolio(Base):
    """A target model (house allocation) a mandate is managed against."""

    __tablename__ = "model_portfolio"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500))
    # Rebalancing tolerance band (drift threshold) as a fraction, e.g. 0.05 = 5%.
    drift_band: Mapped[float] = mapped_column(Numeric(5, 4), default=0.05)

    targets: Mapped[list["TargetAllocation"]] = relationship(
        back_populates="model", cascade="all, delete-orphan"
    )


class TargetAllocation(Base):
    """A single target weight within a model — by asset class and/or instrument."""

    __tablename__ = "target_allocation"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_portfolio.id", ondelete="CASCADE"), index=True
    )
    asset_class: Mapped[AssetClass] = mapped_column(String(24))
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("instrument.id", ondelete="SET NULL"), nullable=True
    )
    target_weight: Mapped[float] = mapped_column(Numeric(6, 5))  # fraction summing to 1.0

    model: Mapped["ModelPortfolio"] = relationship(back_populates="targets")
