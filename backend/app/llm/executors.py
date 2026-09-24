"""Tool executors — wire gateway tools to real domain logic.

Each tool in the catalogue has a corresponding executor that calls the actual
business logic (household_brain, runtime.decide, execute_order_set, etc.) and
returns a result. Executors run after the gateway validates role access and
(if needed) user confirmation — but decide_recommendation and execute_orders
still re-check the domain-level rules (decision_rights, exactly-once locking)
because those are stricter than the tool catalogue's coarse role list and are
the actual source of truth per CLAUDE.md.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.graph import household_brain
from app.models.enums import UserRole
from app.models.graph import Account, Goal, Household, LegalEntity, Mandate, Person
from app.models.portfolio import Holding, Instrument, Price


class ExecutorError(Exception):
    """Error during tool execution."""
    pass


def _parse_uuid(value: Any, field: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise ExecutorError(f"Invalid {field}: {value}")


class ToolExecutors:
    """Factory for tool executors. Each tool_key maps to an executor function."""

    def __init__(self, session: AsyncSession, user_id: uuid.UUID, role: UserRole, firm_id: uuid.UUID):
        self.session = session
        self.user_id = user_id
        self.role = role
        self.firm_id = firm_id

    async def execute(self, tool_key: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool by key and return the result."""

        executor_map = {
            "read_household": self.read_household,
            "search_households": self.search_households,
            "check_household_wash_sale": self.check_household_wash_sale,
            "read_composites": self.read_composites,
            "read_composite_report": self.read_composite_report,
            "read_firm_definition_check": self.read_firm_definition_check,
            "read_ai_governance_summary": self.read_ai_governance_summary,
            "read_account_registration": self.read_account_registration,
            "set_account_registration": self.set_account_registration,
            "add_account_beneficiary": self.add_account_beneficiary,
            "read_beneficiary_audit": self.read_beneficiary_audit,
            "read_portfolio": self.read_portfolio,
            "search_holdings": self.search_holdings,
            "decide_recommendation": self.decide_recommendation,
            "execute_orders": self.execute_orders,
            "update_goal": self.update_goal,
            "read_compliance_program": self.read_compliance_program,
            "mark_conflict_reviewed": self.mark_conflict_reviewed,
            "log_wsp_evidence": self.log_wsp_evidence,
            "read_crm_pipeline": self.read_crm_pipeline,
            "create_crm_contact": self.create_crm_contact,
            "create_crm_opportunity": self.create_crm_opportunity,
            "log_crm_activity": self.log_crm_activity,
            "update_crm_opportunity_stage": self.update_crm_opportunity_stage,
            "read_corporate_actions": self.read_corporate_actions,
            "compute_corporate_action_entitlements": self.compute_corporate_action_entitlements,
            "post_corporate_action_entitlement": self.post_corporate_action_entitlement,
            "read_sleeves": self.read_sleeves,
            "net_sleeve_intents": self.net_sleeve_intents,
        }

        executor = executor_map.get(tool_key)
        if not executor:
            raise ExecutorError(f"No executor for tool '{tool_key}'")

        try:
            return await executor(inputs)
        except ExecutorError:
            raise
        except Exception as e:
            raise ExecutorError(f"Tool '{tool_key}' failed: {e}")

    # ─────────────────────────────────────────────────────────────────────
    # Read-only tools
    # ─────────────────────────────────────────────────────────────────────

    async def read_household(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Fetch a household's complete brain."""
        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")

        brain = await household_brain(self.session, household_id, firm_id=self.firm_id)
        if not brain:
            raise ExecutorError(f"Household {household_id} not found or access denied")

        return {
            "household_id": str(household_id),
            "brain": brain,
            "message": f"Fetched household brain with {len(brain.get('accounts', []))} accounts",
        }

    async def search_households(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Search households by name, with each match's accounts inline — resolves a
        phrase like 'the Chen Family's Trust Custody account' to an account_id without a
        second read_household round trip."""
        query = inputs.get("query")
        if not query:
            raise ExecutorError("query is required")
        limit = int(inputs.get("limit") or 10)

        households = (
            await self.session.execute(
                select(Household)
                .where(Household.firm_id == self.firm_id, Household.name.ilike(f"%{query}%"))
                .limit(limit)
            )
        ).scalars().all()

        results = []
        for h in households:
            persons = (
                await self.session.execute(select(Person).where(Person.household_id == h.id))
            ).scalars().all()
            entities = (
                await self.session.execute(select(LegalEntity).where(LegalEntity.household_id == h.id))
            ).scalars().all()
            person_ids = [p.id for p in persons]
            entity_ids = [e.id for e in entities]

            conds = []
            if person_ids:
                conds.append(Mandate.person_id.in_(person_ids))
            if entity_ids:
                conds.append(Mandate.entity_id.in_(entity_ids))
            mandates = (
                await self.session.execute(select(Mandate).where(or_(*conds)))
            ).scalars().all() if conds else []

            mandate_ids = [m.id for m in mandates]
            accounts = (
                await self.session.execute(select(Account).where(Account.mandate_id.in_(mandate_ids)))
            ).scalars().all() if mandate_ids else []

            results.append({
                "household_id": str(h.id), "name": h.name, "segment": str(h.segment),
                "accounts": [{
                    "account_id": str(a.id), "name": a.name, "custodian": a.custodian,
                } for a in accounts],
            })

        return {
            "results": results,
            "message": f"Found {len(results)} household(s) matching '{query}'",
        }

    async def check_household_wash_sale(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import tax_intelligence

        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")
        result = await tax_intelligence.household_wash_sale_calendar(
            self.session, household_id, firm_id=self.firm_id)
        if result is None:
            raise ExecutorError(f"Household {household_id} not found or access denied")

        n = len(result["violations"])
        result["message"] = (
            f"No wash-sale conflicts right now — {result['lots_checked']} lot(s) checked."
            if n == 0 else
            f"{n} lot(s) would trigger a wash sale if harvested now, disallowing "
            f"{result['total_disallowed_loss']:,.0f} of loss."
        )
        return result

    async def read_composites(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.models.composites import Composite
        from app.models.portfolio import ModelPortfolio

        rows = (await self.session.execute(
            select(Composite).where(Composite.firm_id == self.firm_id).order_by(Composite.name)
        )).scalars().all()
        out = []
        for c in rows:
            model = await self.session.get(ModelPortfolio, c.model_portfolio_id)
            out.append({
                "composite_id": str(c.id), "name": c.name, "strategy": model.name if model else None,
                "inclusion_criteria": c.inclusion_criteria, "status": c.status,
            })
        return {"composites": out, "message": f"{len(out)} composite(s)."}

    async def read_composite_report(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import composites as engine
        from app.models.composites import Composite

        composite_id = _parse_uuid(inputs.get("composite_id") or "", "composite_id")
        composite = (await self.session.execute(
            select(Composite).where(Composite.id == composite_id, Composite.firm_id == self.firm_id)
        )).scalar_one_or_none()
        if composite is None:
            raise ExecutorError(f"Composite {composite_id} not found")

        report = await engine.composite_report(self.session, composite)
        std = report["ex_post_std_dev"]
        report["message"] = (
            f"{report['name']}: {report['accounts_included']} account(s), "
            f"gross return {report['gross_return']*100:.1f}%" if report["gross_return"] is not None else
            f"{report['name']}: {report['accounts_included']} account(s), no return computed yet"
        ) + (
            f"; ex-post std dev {std['value']*100:.1f}% ({std['months_used']}/{std['months_required_for_gips']} months — "
            f"{'meets' if std['meets_gips_minimum'] else 'short of'} the GIPS minimum)." if std["value"] is not None else "."
        )
        return report

    async def read_firm_definition_check(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import composites as engine

        result = await engine.firm_definition_check(self.session, self.firm_id)
        result["message"] = (
            "Every discretionary account is captured in a composite."
            if result["clean"] else
            f"{result['uncaptured_aum']:,.0f} of discretionary AUM is not captured in any composite — "
            "the firm definition may be narrowed to exclude it."
        )
        return result

    async def read_ai_governance_summary(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import ai_governance as engine

        result = await engine.inventory_summary(self.session, self.firm_id)
        n_overdue = len(result["overdue_review"])
        n_violations = result["entitlement_violations"]["total"]
        result["message"] = (
            f"{result['active']} active use case(s) ({result['by_risk_tier']['high']} high-risk). "
            f"{n_overdue} overdue for review. {n_violations} entitlement violation(s) logged."
        )
        return result

    async def read_account_registration(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import registration as engine
        from app.models.graph import Account, AccountBeneficiary

        account_id = _parse_uuid(inputs.get("account_id") or "", "account_id")
        account = (await self.session.execute(
            select(Account).where(Account.id == account_id, Account.firm_id == self.firm_id)
        )).scalar_one_or_none()
        if account is None:
            raise ExecutorError(f"Account {account_id} not found")

        beneficiaries = (await self.session.execute(
            select(AccountBeneficiary).where(AccountBeneficiary.account_id == account.id)
        )).scalars().all()
        rmd = await engine.rmd_for_account(self.session, account)

        return {
            "account_id": str(account.id), "account_name": account.name,
            "registration_type": account.registration_type,
            "rmd": rmd,
            "beneficiaries": [{
                "beneficiary_name": b.beneficiary_name, "designation_class": b.designation_class,
                "percentage": float(b.percentage), "relationship_to_owner": b.relationship_to_owner,
            } for b in beneficiaries],
            "message": (
                f"{account.name}: registered as {account.registration_type or 'unset'}, "
                f"{len(beneficiaries)} beneficiary designation(s)"
                + (f", RMD {rmd['status']}" if rmd else "") + "."
            ),
        }

    async def set_account_registration(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from datetime import date as _date

        from app.models.enums import RegistrationType
        from app.models.graph import Account

        account_id = _parse_uuid(inputs.get("account_id") or "", "account_id")
        account = (await self.session.execute(
            select(Account).where(Account.id == account_id, Account.firm_id == self.firm_id)
        )).scalar_one_or_none()
        if account is None:
            raise ExecutorError(f"Account {account_id} not found")

        reg_type = inputs.get("registration_type")
        if reg_type is not None:
            if reg_type not in {t.value for t in RegistrationType}:
                raise ExecutorError(f"Unknown registration_type: {reg_type}")
            account.registration_type = reg_type

        death_date = inputs.get("original_owner_death_date")
        if death_date:
            try:
                account.original_owner_death_date = _date.fromisoformat(str(death_date)[:10])
            except ValueError:
                raise ExecutorError(f"Invalid original_owner_death_date: {death_date}")

        election = inputs.get("rmd_election_method")
        if election is not None:
            if election not in ("10_year_rule", "life_expectancy"):
                raise ExecutorError(f"rmd_election_method must be 10_year_rule or life_expectancy, got {election}")
            account.rmd_election_method = election

        await self.session.flush()
        return {
            "account_id": str(account.id), "registration_type": account.registration_type,
            "message": f"Updated registration for {account.name}.",
        }

    async def add_account_beneficiary(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.core.db import utcnow
        from app.models.graph import Account, AccountBeneficiary

        account_id = _parse_uuid(inputs.get("account_id") or "", "account_id")
        beneficiary_name = inputs.get("beneficiary_name")
        percentage = inputs.get("percentage")
        if not beneficiary_name or percentage is None:
            raise ExecutorError("beneficiary_name and percentage are required")

        account = (await self.session.execute(
            select(Account).where(Account.id == account_id, Account.firm_id == self.firm_id)
        )).scalar_one_or_none()
        if account is None:
            raise ExecutorError(f"Account {account_id} not found")

        designation_class = inputs.get("designation_class") or "primary"
        if designation_class not in ("primary", "contingent"):
            raise ExecutorError("designation_class must be primary or contingent")

        row = AccountBeneficiary(
            firm_id=self.firm_id, account_id=account.id, beneficiary_name=beneficiary_name,
            percentage=float(percentage), designation_class=designation_class,
            relationship_to_owner=inputs.get("relationship_to_owner"),
            per_stirpes=str(inputs.get("per_stirpes", "")).lower() == "true",
            date_designated=utcnow().date(),
        )
        self.session.add(row)
        await self.session.flush()

        return {
            "beneficiary_id": str(row.id), "beneficiary_name": row.beneficiary_name,
            "percentage": float(row.percentage),
            "message": f"Added {row.beneficiary_name} as a {designation_class} beneficiary ({percentage}%) on {account.name}.",
        }

    async def read_beneficiary_audit(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import registration as engine

        household_id = inputs.get("household_id")
        result = await engine.beneficiary_audit(
            self.session, self.firm_id,
            household_id=_parse_uuid(household_id, "household_id") if household_id else None,
        )
        n = len(result["gaps"])
        result["message"] = (
            f"All {result['accounts_checked']} account(s) needing beneficiaries are covered."
            if n == 0 else
            f"{n} of {result['accounts_checked']} account(s) needing beneficiaries have a gap."
        )
        return result

    async def read_portfolio(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Fetch a mandate's holdings, cash, and total value."""
        mandate_id = _parse_uuid(inputs.get("mandate_id") or "", "mandate_id")

        mandate = await self.session.get(Mandate, mandate_id)
        if not mandate or mandate.firm_id != self.firm_id:
            raise ExecutorError(f"Mandate {mandate_id} not found or access denied")

        accounts = (
            await self.session.execute(select(Account).where(Account.mandate_id == mandate_id))
        ).scalars().all()
        account_ids = [a.id for a in accounts]

        holdings: list[dict[str, Any]] = []
        total_value = 0.0
        real_priced = synthetic_priced = 0
        if account_ids:
            rows = (
                await self.session.execute(
                    select(Holding, Instrument)
                    .join(Instrument, Holding.instrument_id == Instrument.id)
                    .where(Holding.account_id.in_(account_ids))
                )
            ).all()
            for h, inst in rows:
                mv = float(h.market_value or 0)
                total_value += mv
                # Honesty rule (CLAUDE.md): a valuation resting on a synthetic price
                # says so. The latest Price row for this instrument carries is_real —
                # whether that close came from the real market feed or seed data.
                latest_price = (
                    await self.session.execute(
                        select(Price).where(Price.instrument_id == inst.id).order_by(Price.as_of.desc()).limit(1)
                    )
                ).scalar_one_or_none()
                is_real = bool(latest_price.is_real) if latest_price else False
                if is_real:
                    real_priced += 1
                else:
                    synthetic_priced += 1
                holdings.append({
                    "symbol": inst.symbol,
                    "name": inst.name,
                    "asset_class": str(inst.asset_class),
                    "quantity": float(h.quantity),
                    "market_value": mv,
                    "price_source": "real" if is_real else "synthetic",
                })

        cash = sum(float(a.cash_balance or 0) for a in accounts)
        total_value += cash

        return {
            "mandate_id": str(mandate_id),
            "mandate_name": mandate.name,
            "holdings": holdings,
            "cash": cash,
            "total_value": total_value,
            "pricing": {"real": real_priced, "synthetic": synthetic_priced},
            "message": f"{mandate.name}: {len(holdings)} holding(s), cash {cash:,.2f}, total value {total_value:,.2f}",
        }

    async def search_holdings(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Search for instruments by symbol or name across the firm."""
        query = inputs.get("query")
        if not query:
            raise ExecutorError("query is required")
        limit = int(inputs.get("limit") or 10)

        stmt = (
            select(Instrument)
            .where(
                Instrument.firm_id == self.firm_id,
                or_(Instrument.symbol.ilike(f"%{query}%"), Instrument.name.ilike(f"%{query}%")),
            )
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()

        results = [
            {
                "instrument_id": str(i.id),
                "symbol": i.symbol,
                "name": i.name,
                "asset_class": str(i.asset_class),
            }
            for i in rows
        ]
        return {
            "query": query,
            "results": results,
            "message": f"Found {len(results)} holding(s) matching '{query}'",
        }

    # ─────────────────────────────────────────────────────────────────────
    # State-changing tools
    # ─────────────────────────────────────────────────────────────────────

    async def decide_recommendation(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Approve, modify, or dismiss a recommendation via the same exactly-once,
        role-checked path the Studio review page uses (app.atlas.runtime.decide)."""
        from app.atlas.runtime import AlreadyDecidedError, decide
        from app.core import decision_rights
        from app.models.enums import HumanAction, RecommendationStatus
        from app.models.governance import Recommendation
        from app.models.identity import User
        from app.models.tenant import Firm

        recommendation_id = _parse_uuid(inputs.get("recommendation_id") or "", "recommendation_id")
        action_str = inputs.get("action")

        if action_str == "revise":
            raise ExecutorError(
                "Revise isn't available through conversation yet — it re-runs the agent with "
                "new constraints (CGT budget, drift band, protected holdings). Use the "
                "Recommendations page in Studio for that, or approve/dismiss here."
            )
        if action_str not in ("approve", "modify", "dismiss"):
            raise ExecutorError(f"Invalid action: {action_str}. Must be approve, modify, or dismiss")

        rec = await self.session.get(Recommendation, recommendation_id)
        if not rec or rec.firm_id != self.firm_id:
            raise ExecutorError(f"Recommendation {recommendation_id} not found or access denied")

        try:
            decision_rights.check(self.role, rec.agent_key, action_str)
        except decision_rights.DecisionForbidden as exc:
            from app.aurea_core.ai_governance import record_violation
            await record_violation(
                self.session, self.firm_id, actor_user_id=self.user_id, actor_role=str(self.role),
                violation_type="decision_rights_forbidden", subject_key=str(rec.agent_key), detail=str(exc),
            )
            raise ExecutorError(str(exc))

        if str(rec.status) != str(RecommendationStatus.PROPOSED):
            raise ExecutorError(f"Already {rec.status}")

        firm = await self.session.get(Firm, self.firm_id)
        user = await self.session.get(User, self.user_id)
        note = inputs.get("note")

        try:
            rec = await decide(
                self.session, firm=firm, recommendation=rec, action=HumanAction(action_str),
                actor_id=self.user_id, actor_label=f"{user.full_name} ({user.role})",
                note=note,
            )
        except AlreadyDecidedError as exc:
            raise ExecutorError(str(exc))

        past_tense = {"approve": "Approved", "modify": "Modified", "dismiss": "Dismissed"}[action_str]
        return {
            "recommendation_id": str(rec.id),
            "action": action_str,
            "status": str(rec.status),
            "message": f"{past_tense} '{rec.title}'. Status: {rec.status}.",
        }

    async def execute_orders(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Submit buy/sell orders on a mandate's first account, via the same order
        engine (draft -> staged -> placed -> filled -> settled) the drift-rebalancing
        agent uses — real prices, real book entries, honest about a paper venue."""
        from app.conduit.execution import execute_order_set
        from app.models.tenant import Firm

        mandate_id = _parse_uuid(inputs.get("mandate_id") or "", "mandate_id")
        orders_in = inputs.get("orders") or []
        reason = inputs.get("reason") or ""

        if not orders_in:
            raise ExecutorError("orders is required and must be a non-empty list")

        mandate = await self.session.get(Mandate, mandate_id)
        if not mandate or mandate.firm_id != self.firm_id:
            raise ExecutorError(f"Mandate {mandate_id} not found or access denied")

        accounts = (
            await self.session.execute(select(Account).where(Account.mandate_id == mandate_id))
        ).scalars().all()
        if not accounts:
            raise ExecutorError(f"Mandate {mandate_id} has no account to trade in")
        # Simplification: trade against the mandate's first account. Mandates with
        # multiple accounts (rare) need an account_id per order — not yet supported
        # from conversation.
        account = accounts[0]

        order_set = []
        for o in orders_in:
            symbol = o.get("symbol")
            side = o.get("side", "buy")
            quantity = o.get("quantity")
            if not symbol or not quantity:
                raise ExecutorError("Each order needs a symbol and quantity")

            inst = (
                await self.session.execute(
                    select(Instrument).where(Instrument.firm_id == self.firm_id, Instrument.symbol == symbol)
                )
            ).scalar_one_or_none()
            if not inst:
                raise ExecutorError(f"Unknown instrument symbol: {symbol}")

            price_row = (
                await self.session.execute(
                    select(Price).where(Price.instrument_id == inst.id).order_by(Price.as_of.desc()).limit(1)
                )
            ).scalar_one_or_none()
            est_price = float(price_row.close) if price_row else 0.0
            est_value = est_price * float(quantity)

            order_set.append({
                "account_id": str(account.id),
                "instrument_id": str(inst.id),
                "side": side,
                "quantity": quantity,
                "est_price": est_price,
                "est_value": est_value,
                "symbol": symbol,
                "asset_class": str(inst.asset_class),
                "reason": reason,
            })

        firm = await self.session.get(Firm, self.firm_id)
        result = await execute_order_set(
            self.session, firm=firm, order_set=order_set, mandate_id=mandate_id, actor=str(self.role),
        )

        note = (
            f"{result['orders_settled']} of {result['orders_created']} order(s) filled and "
            f"settled via '{result['venue']}'"
            + ("" if result["reaches_market"] else " (paper venue — real prices and real book "
                                                   "entries, but no market was reached)")
            + (f". {result['orders_failed']} could not be executed." if result["orders_failed"] else ".")
        )

        return {
            "mandate_id": str(mandate_id),
            "orders_count": len(order_set),
            "orders_settled": result["orders_settled"],
            "orders_failed": result["orders_failed"],
            "venue": result["venue"],
            "fills": result["settled"],
            "failed": result["failed"],
            "message": note,
        }

    async def update_goal(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Update a household goal's target, timeline, or priority."""
        goal_id = _parse_uuid(inputs.get("goal_id") or "", "goal_id")
        target = inputs.get("target")
        timeline = inputs.get("timeline")
        priority = inputs.get("priority")

        goal = await self.session.get(Goal, goal_id)
        if not goal or goal.firm_id != self.firm_id:
            raise ExecutorError(f"Goal {goal_id} not found or access denied")

        updates: dict[str, Any] = {}
        if target is not None:
            goal.target_amount = float(target)
            updates["target_amount"] = float(target)
        if timeline:
            # Only a bare 4-digit year is unambiguous from conversation; anything
            # else (e.g. "5 years") is left for the goal-planning UI to resolve.
            text = str(timeline).strip()
            if text.isdigit() and len(text) == 4:
                from datetime import date
                goal.target_date = date(int(text), 12, 31)
                updates["target_date"] = goal.target_date.isoformat()
            else:
                updates["timeline_note"] = f"'{timeline}' wasn't a specific year — target date unchanged"
        if priority:
            priority_map = {"high": 1, "medium": 2, "low": 3}
            mapped = priority_map.get(str(priority).lower())
            if mapped:
                goal.priority = mapped
                updates["priority"] = str(priority).lower()

        await self.session.flush()

        return {
            "goal_id": str(goal_id),
            "updates": updates,
            "message": f"Updated goal '{goal.name}'.",
        }

    # ─────────────────────────────────────────────────────────────────────
    # Compliance program: conflicts inventory + WSP grid
    # ─────────────────────────────────────────────────────────────────────

    async def read_compliance_program(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import compliance_program as engine
        from app.models.compliance_program import ConflictInventoryItem, WSPRule

        conflicts = (await self.session.execute(
            select(ConflictInventoryItem).where(ConflictInventoryItem.firm_id == self.firm_id)
            .order_by(ConflictInventoryItem.title)
        )).scalars().all()
        wsp_rows = (await self.session.execute(
            select(WSPRule).where(WSPRule.firm_id == self.firm_id).order_by(WSPRule.rule_key)
        )).scalars().all()

        summary = {
            "conflicts": await engine.conflicts_summary(self.session, self.firm_id),
            "evidence_coverage": await engine.evidence_coverage(self.session, self.firm_id),
        }

        return {
            "conflicts": [{
                "conflict_key": c.conflict_key, "title": c.title, "status": c.status,
                "owner": c.owner, "last_reviewed_at": c.last_reviewed_at.isoformat() if c.last_reviewed_at else None,
            } for c in conflicts],
            "wsp_rules": [{
                "rule_key": r.rule_key, "obligation": r.obligation, "frequency": r.frequency,
                "evidence_automated": r.evidence_automated,
                "last_evidence_at": r.last_evidence_at.isoformat() if r.last_evidence_at else None,
            } for r in wsp_rows],
            "summary": summary,
            "message": (
                f"{summary['conflicts']['active']} active conflict(s), "
                f"{len(summary['conflicts']['overdue_review'])} overdue for review. "
                f"WSP evidence coverage {summary['evidence_coverage']['coverage_pct']}%, "
                f"{len(summary['evidence_coverage']['stale_evidence'])} rule(s) stale."
            ),
        }

    async def mark_conflict_reviewed(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.core.db import utcnow
        from app.models.compliance_program import ConflictInventoryItem
        from app.models.identity import User

        conflict_key = inputs.get("conflict_key")
        if not conflict_key:
            raise ExecutorError("conflict_key is required")

        row = (await self.session.execute(
            select(ConflictInventoryItem).where(
                ConflictInventoryItem.firm_id == self.firm_id,
                ConflictInventoryItem.conflict_key == conflict_key,
            )
        )).scalar_one_or_none()
        if row is None:
            raise ExecutorError(f"No conflict-inventory item with key '{conflict_key}'")

        user = await self.session.get(User, self.user_id)
        row.last_reviewed_at = utcnow()
        row.last_reviewed_by = user.email if user else str(self.role)
        if inputs.get("notes"):
            row.notes = inputs["notes"]
        await self.session.flush()

        return {
            "conflict_key": row.conflict_key, "title": row.title,
            "last_reviewed_at": row.last_reviewed_at.isoformat(),
            "message": f"Marked '{row.title}' reviewed as of today.",
        }

    async def log_wsp_evidence(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.core.db import utcnow
        from app.models.compliance_program import WSPRule

        rule_key = inputs.get("rule_key")
        if not rule_key:
            raise ExecutorError("rule_key is required")

        row = (await self.session.execute(
            select(WSPRule).where(WSPRule.firm_id == self.firm_id, WSPRule.rule_key == rule_key)
        )).scalar_one_or_none()
        if row is None:
            raise ExecutorError(f"No WSP rule with key '{rule_key}'")

        row.last_evidence_at = utcnow()
        await self.session.flush()

        return {
            "rule_key": row.rule_key, "obligation": row.obligation,
            "last_evidence_at": row.last_evidence_at.isoformat(),
            "message": f"Logged evidence for '{row.obligation}' as of today.",
        }

    # ─────────────────────────────────────────────────────────────────────
    # CRM pipeline
    # ─────────────────────────────────────────────────────────────────────

    async def read_crm_pipeline(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import crm as engine
        from app.models.crm import CrmContact, CrmOpportunity

        contacts = (await self.session.execute(
            select(CrmContact).where(CrmContact.firm_id == self.firm_id).order_by(CrmContact.full_name)
        )).scalars().all()
        opportunities = (await self.session.execute(
            select(CrmOpportunity).where(CrmOpportunity.firm_id == self.firm_id)
            .order_by(CrmOpportunity.opened_at.desc())
        )).scalars().all()
        summary = await engine.pipeline_summary(self.session, self.firm_id)

        contacts_by_id = {c.id: c for c in contacts}
        return {
            "contacts": [{
                "id": str(c.id), "full_name": c.full_name, "contact_type": c.contact_type,
                "source": c.source,
            } for c in contacts],
            "opportunities": [{
                "id": str(o.id), "contact_name": contacts_by_id[o.contact_id].full_name
                    if o.contact_id in contacts_by_id else None,
                "title": o.title, "stage": o.stage,
                "estimated_aum": float(o.estimated_aum) if o.estimated_aum is not None else None,
                "probability_pct": o.probability_pct,
            } for o in opportunities],
            "summary": summary,
            "message": (
                f"{len(contacts)} contact(s), {summary['open_pipeline_count']} open opportunity(ies) "
                f"worth {summary['weighted_open_value']:,.0f} weighted."
            ),
        }

    async def create_crm_contact(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.models.crm import CONTACT_TYPES, CrmContact

        full_name = inputs.get("full_name")
        if not full_name:
            raise ExecutorError("full_name is required")
        contact_type = inputs.get("contact_type") or "prospect"
        if contact_type not in CONTACT_TYPES:
            raise ExecutorError(f"contact_type must be one of {CONTACT_TYPES}")

        row = CrmContact(
            firm_id=self.firm_id, full_name=full_name, contact_type=contact_type,
            email=inputs.get("email"), source=inputs.get("source"), notes=inputs.get("notes"),
            owner_user_id=self.user_id, is_active=True,
        )
        self.session.add(row)
        await self.session.flush()

        return {
            "contact_id": str(row.id), "full_name": row.full_name, "contact_type": row.contact_type,
            "message": f"Added '{row.full_name}' as a {row.contact_type.replace('_', ' ')}.",
        }

    async def create_crm_opportunity(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.core.db import utcnow
        from app.models.crm import CrmContact, CrmOpportunity

        contact_id = _parse_uuid(inputs.get("contact_id") or "", "contact_id")
        title = inputs.get("title")
        if not title:
            raise ExecutorError("title is required")

        contact = (await self.session.execute(
            select(CrmContact).where(CrmContact.id == contact_id, CrmContact.firm_id == self.firm_id)
        )).scalar_one_or_none()
        if contact is None:
            raise ExecutorError(f"Contact {contact_id} not found")

        row = CrmOpportunity(
            firm_id=self.firm_id, contact_id=contact_id, title=title, stage="lead",
            estimated_aum=inputs.get("estimated_aum"), probability_pct=int(inputs.get("probability_pct") or 10),
            opened_at=utcnow(),
        )
        self.session.add(row)
        await self.session.flush()

        return {
            "opportunity_id": str(row.id), "contact_name": contact.full_name, "title": row.title,
            "stage": row.stage,
            "message": f"Opened '{row.title}' against {contact.full_name}, stage lead.",
        }

    async def log_crm_activity(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.core.db import utcnow
        from app.models.crm import ACTIVITY_TYPES, CrmActivity, CrmContact

        contact_id = _parse_uuid(inputs.get("contact_id") or "", "contact_id")
        detail = inputs.get("detail")
        if not detail:
            raise ExecutorError("detail is required")
        activity_type = inputs.get("activity_type") or "note"
        if activity_type not in ACTIVITY_TYPES:
            raise ExecutorError(f"activity_type must be one of {ACTIVITY_TYPES}")

        contact = (await self.session.execute(
            select(CrmContact).where(CrmContact.id == contact_id, CrmContact.firm_id == self.firm_id)
        )).scalar_one_or_none()
        if contact is None:
            raise ExecutorError(f"Contact {contact_id} not found")

        opportunity_id = inputs.get("opportunity_id")
        row = CrmActivity(
            firm_id=self.firm_id, contact_id=contact_id,
            opportunity_id=_parse_uuid(opportunity_id, "opportunity_id") if opportunity_id else None,
            activity_type=activity_type, occurred_at=utcnow(), detail=detail,
            logged_by_user_id=self.user_id,
        )
        self.session.add(row)
        await self.session.flush()

        return {
            "activity_id": str(row.id), "contact_name": contact.full_name, "activity_type": activity_type,
            "message": f"Logged a {activity_type} with {contact.full_name}.",
        }

    async def update_crm_opportunity_stage(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.core.db import utcnow
        from app.models.crm import PIPELINE_STAGES, CrmOpportunity

        opportunity_id = _parse_uuid(inputs.get("opportunity_id") or "", "opportunity_id")
        stage = inputs.get("stage")
        if stage not in PIPELINE_STAGES:
            raise ExecutorError(f"stage must be one of {PIPELINE_STAGES}")

        row = (await self.session.execute(
            select(CrmOpportunity).where(CrmOpportunity.id == opportunity_id, CrmOpportunity.firm_id == self.firm_id)
        )).scalar_one_or_none()
        if row is None:
            raise ExecutorError(f"Opportunity {opportunity_id} not found")

        row.stage = stage
        if stage in ("won", "lost") and row.closed_at is None:
            row.closed_at = utcnow()
        if stage == "lost" and inputs.get("lost_reason"):
            row.lost_reason = inputs["lost_reason"]
        await self.session.flush()

        return {
            "opportunity_id": str(row.id), "title": row.title, "stage": row.stage,
            "message": f"Moved '{row.title}' to {stage}.",
        }

    # ─────────────────────────────────────────────────────────────────────
    # Corporate actions
    # ─────────────────────────────────────────────────────────────────────

    async def read_corporate_actions(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.models.corporate_actions import CorporateAction, CorporateActionEntitlement
        from app.models.portfolio import Instrument

        query = select(CorporateAction).where(CorporateAction.firm_id == self.firm_id)
        status = inputs.get("status")
        if status:
            query = query.where(CorporateAction.status == status)
        actions = (await self.session.execute(query.order_by(CorporateAction.ex_date))).scalars().all()

        out = []
        for a in actions:
            inst = await self.session.get(Instrument, a.instrument_id)
            entitlements = (await self.session.execute(
                select(CorporateActionEntitlement).where(CorporateActionEntitlement.corporate_action_id == a.id)
            )).scalars().all()
            out.append({
                "id": str(a.id), "symbol": inst.symbol if inst else None, "action_type": a.action_type,
                "status": a.status, "is_voluntary": a.is_voluntary,
                "payable_date": a.payable_date.isoformat() if a.payable_date else None,
                "entitlement_count": len(entitlements),
                "entitlements_posted": sum(1 for e in entitlements if e.status == "posted"),
            })

        return {
            "actions": out,
            "message": f"{len(out)} corporate action(s)" + (f" with status '{status}'" if status else "") + ".",
        }

    async def compute_corporate_action_entitlements(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import corporate_actions as engine
        from app.aurea_core.corporate_actions import CorporateActionError
        from app.models.corporate_actions import CorporateAction

        action_id = _parse_uuid(inputs.get("corporate_action_id") or "", "corporate_action_id")
        action = (await self.session.execute(
            select(CorporateAction).where(CorporateAction.id == action_id, CorporateAction.firm_id == self.firm_id)
        )).scalar_one_or_none()
        if action is None:
            raise ExecutorError(f"Corporate action {action_id} not found")

        try:
            created = await engine.compute_entitlements(self.session, action_id)
        except CorporateActionError as exc:
            raise ExecutorError(str(exc))

        return {
            "corporate_action_id": str(action_id), "created_count": len(created),
            "message": f"Computed {len(created)} new entitlement(s) for {action.action_type}.",
        }

    async def post_corporate_action_entitlement(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import corporate_actions as engine
        from app.aurea_core.corporate_actions import CorporateActionError

        from app.models.corporate_actions import CorporateActionEntitlement

        entitlement_id = _parse_uuid(inputs.get("entitlement_id") or "", "entitlement_id")
        row = await self.session.get(CorporateActionEntitlement, entitlement_id)
        if row is None or row.firm_id != self.firm_id:
            raise ExecutorError(f"Entitlement {entitlement_id} not found")

        try:
            posted = await engine.post_entitlement(self.session, entitlement_id)
        except CorporateActionError as exc:
            raise ExecutorError(str(exc))

        return {
            "entitlement_id": str(posted.id), "status": posted.status,
            "cash_amount": float(posted.cash_amount) if posted.cash_amount is not None else None,
            "message": f"Posted entitlement — status {posted.status}.",
        }

    # ─────────────────────────────────────────────────────────────────────
    # UMA sleeves
    # ─────────────────────────────────────────────────────────────────────

    async def read_sleeves(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import sleeves as engine
        from app.models.sleeves import Sleeve

        account_id = _parse_uuid(inputs.get("account_id") or "", "account_id")
        sleeves = (await self.session.execute(
            select(Sleeve).where(Sleeve.account_id == account_id, Sleeve.firm_id == self.firm_id)
        )).scalars().all()
        if not sleeves:
            return {"account_id": str(account_id), "sleeves": [], "reconciliation": None,
                    "message": "This account has no sleeves — it runs a single model."}

        reconciliation = await engine.reconcile(self.session, account_id)

        return {
            "account_id": str(account_id),
            "sleeves": [{
                "id": str(s.id), "name": s.name, "target_weight": float(s.target_weight),
                "status": s.status,
            } for s in sleeves],
            "reconciliation": reconciliation,
            "message": (
                f"{len(sleeves)} sleeve(s). Reconciliation "
                + ("clean." if reconciliation["clean"] else
                   f"found {len(reconciliation['breaks'])} break(s) and "
                   f"{len(reconciliation['orphaned_attributions'])} orphaned attribution(s).")
            ),
        }

    async def net_sleeve_intents(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core.sleeves import SleeveIntent, net_intents

        intents_in = inputs.get("intents") or []
        if not intents_in:
            raise ExecutorError("intents is required and must be a non-empty list")

        try:
            intents = [
                SleeveIntent(
                    sleeve_id=str(i["sleeve_id"]), account_id=str(i["account_id"]),
                    instrument_id=str(i["instrument_id"]), symbol=i["symbol"], side=i["side"],
                    quantity=float(i["quantity"]),
                )
                for i in intents_in
            ]
            orders = net_intents(intents)
        except (KeyError, ValueError) as exc:
            raise ExecutorError(f"Invalid intent: {exc}")

        return {
            "net_orders": [{
                "account_id": o.account_id, "instrument_id": o.instrument_id, "symbol": o.symbol,
                "side": o.side, "quantity": o.quantity, "crossed_quantity": o.crossed_quantity,
                "gross_buy_quantity": o.gross_buy_quantity, "gross_sell_quantity": o.gross_sell_quantity,
                "sleeve_allocations": o.sleeve_allocations,
            } for o in orders],
            "message": (
                f"{len(orders)} net order(s) from {len(intents)} sleeve intent(s); "
                f"{sum(o.crossed_quantity for o in orders):,.0f} share(s) crossed internally."
            ),
        }
