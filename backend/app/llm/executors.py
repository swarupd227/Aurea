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

from sqlalchemy import func, or_, select
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

    def __init__(self, session: AsyncSession, user_id: uuid.UUID, role: UserRole, firm_id: uuid.UUID,
                 on_card=None):
        self.session = session
        self.user_id = user_id
        self.role = role
        self.firm_id = firm_id
        # Called with the run's card each time its beats move, so the gateway can publish them.
        self.on_card = on_card

    async def execute(self, tool_key: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool by key and return the result."""

        executor_map = {
            "read_household": self.read_household,
            "search_households": self.search_households,
            "check_household_wash_sale": self.check_household_wash_sale,
            "read_performance": self.read_performance,
            "read_billing": self.read_billing,
            "read_private_valuations": self.read_private_valuations,
            "record_valuation": self.record_valuation,
            "read_price_freshness": self.read_price_freshness,
            "refresh_prices": self.refresh_prices,
            "read_protection": self.read_protection,
            "record_protection_policy": self.record_protection_policy,
            "end_protection_policy": self.end_protection_policy,
            "record_income": self.record_income,
            "read_reconciliation": self.read_reconciliation,
            "import_custody_statement": self.import_custody_statement,
            "resolve_recon_break": self.resolve_recon_break,
            "explain_invoice": self.explain_invoice,
            "run_billing": self.run_billing,
            "credit_invoice": self.credit_invoice,
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
            "read_recommendations": self.read_recommendations,
            "read_agents": self.read_agents,
            "open_view": self.open_view,  # outside the gateway's turn no id is grounded, so only id-free views open
            "run_agent": self.run_agent,
            "read_agent_history": self.read_agent_history,
            "read_capacity": self.read_capacity,
            "read_analytics": self.read_analytics,
            "read_onboarding_cases": self.read_onboarding_cases,
            "read_meetings": self.read_meetings,
            "read_tasks": self.read_tasks,
            "complete_task": self.complete_task,
            "assign_task": self.assign_task,
            "create_task": self.create_task,
            "read_provenance": self.read_provenance,
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

    async def read_performance(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import performance

        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")
        result = await performance.household_performance(self.session, household_id, firm_id=self.firm_id)
        if result is None:
            raise ExecutorError(f"Household {household_id} not found or access denied")

        d = result["disclosures"]
        if result["twr"] is None:
            result["message"] = (
                "Performance could not be measured - there is not enough price history on file "
                "for this household's holdings."
            )
            return result

        notes = ["securities only", "gross of fees", "no deposits or withdrawals recorded"]
        if d["synthetic_price_symbols"]:
            notes.append("synthetic prices: " + ", ".join(d["synthetic_price_symbols"]))
        if d["unpriced_flat_symbols"]:
            notes.append(
                "no price history, so counted in the total at its current value with zero return "
                "(not excluded): " + ", ".join(d["unpriced_flat_symbols"])
            )
        if d["stale_days"] and d["stale_days"] > 45:
            notes.append(f"latest price is {d['stale_days']} days old")
        mwr = f", money-weighted {result['mwr']['period']:+.1%}" if result["mwr"] else ""
        result["message"] = (
            f"Time-weighted return {result['twr']:+.1%}{mwr} over {result['months']} month(s) "
            f"to {result['end']} (" + "; ".join(notes) + ")."
        )
        return result

    async def read_private_valuations(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import valuations

        rows = await valuations.private_valuations(self.session, self.firm_id)
        overdue = [r for r in rows if r["overdue"]]
        return {
            "instruments": rows,
            "message": (f"{len(rows)} private holding(s); {len(overdue)} without a current stated valuation."
                        if rows else "There are no private holdings."),
        }

    async def record_valuation(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from datetime import date as _date

        from app.aurea_core import valuations
        from app.models.identity import User

        # The gateway checks the catalogue's roles; this re-checks, as for the other tools that move money.
        if self.role not in (UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM):
            raise ExecutorError("Recording a valuation changes client balances and fees, so only "
                                "operations or the portfolio team may do it.")
        symbol = (inputs.get("symbol") or "").strip()
        inst = (await self.session.execute(
            select(Instrument).where(Instrument.firm_id == self.firm_id, Instrument.symbol == symbol)
        )).scalar_one_or_none()
        if inst is None:
            raise ExecutorError(f"No instrument with symbol '{symbol}'.")
        try:
            as_of = _date.fromisoformat(str(inputs.get("as_of") or ""))
        except ValueError:
            raise ExecutorError("as_of must be a date, YYYY-MM-DD.")
        try:
            unit_value = float(inputs.get("unit_value"))
        except (TypeError, ValueError):
            raise ExecutorError("unit_value must be a number.")
        user = await self.session.get(User, self.user_id)
        try:
            result = await valuations.record_valuation(
                self.session, self.firm_id, instrument_id=inst.id, as_of=as_of, unit_value=unit_value,
                source_document=inputs.get("source_document") or "", notes=inputs.get("notes"),
                recorded_by=user.email if user else "unknown")
        except valuations.ValuationError as exc:
            raise ExecutorError(str(exc))
        move = f" ({result['change_pct']:+.1%} against {result['previous']['value']:,.4f})" if result["change_pct"] is not None else ""
        result["message"] = (
            f"Recorded {inst.symbol} at {result['unit_value']:,.4f} as at {result['as_of']}{move}, from "
            f"{result['source_document']}. {result['holdings_revalued']} holding(s) revalued, "
            f"{result['value_before']:,.2f} to {result['value_after']:,.2f}.")
        return result

    async def read_price_freshness(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import price_freshness

        result = await price_freshness.price_freshness(self.session, self.firm_id)
        s = result["summary"]
        bad = s["missing"] + s["synthetic"] + s["stale"]
        feed = result["feed"]
        parts = [f"{s['total']} held instrument(s): {s['current']} current, {s['ageing']} ageing, {bad} not trustworthy"]
        if s["share_not_current"]:
            parts.append(f"{s['share_not_current']:.0%} of the book's value rests on those")
        if not feed["live"]:
            parts.append("the live feed is switched off")
        elif feed["last_run"] and feed["last_run"]["failed"]:
            parts.append(f"the last run failed for {len(feed['last_run']['failed'])} symbol(s)")
        result["message"] = "; ".join(parts) + "."
        return result

    async def refresh_prices(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import price_freshness

        # The gateway checks the catalogue's roles; this re-checks, as for the other tools that
        # revalue holdings.
        if self.role not in (UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM, UserRole.ADMIN):
            raise ExecutorError("Fetching prices revalues holdings, which changes client balances and fees, "
                                "so only operations, the portfolio team or an administrator may do it.")
        try:
            result = await price_freshness.refresh_prices(self.session, self.firm_id)
        except price_freshness.RefreshRefused as exc:
            raise ExecutorError(str(exc))
        if result["status"] == "error":
            result["message"] = f"The fetch failed: {result.get('error') or 'no prices could be fetched'}."
        else:
            fail = f"; {len(result['failed'])} symbol(s) failed" if result["failed"] else ""
            rv = (f"; {result['revalued']['holdings']} holding(s) revalued" if result["revalued"]
                  else "; nothing needed revaluing")
            result["message"] = (f"Fetched {result['resolved']} of {result['instruments']} instrument(s): "
                                 f"{result['inserted']} new price(s), {result['updated']} updated{fail}{rv}.")
        return result

    async def read_protection(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import protection

        if inputs.get("household_id"):
            household_id = _parse_uuid(inputs["household_id"], "household_id")
            result = await protection.for_household(self.session, household_id, firm_id=self.firm_id)
            if result is None:
                raise ExecutorError(f"Household {household_id} not found or access denied")
            s = result["summary"]
            result["message"] = (f"{result['household']}: {s['people']} earner(s), {s['gaps']} gap(s), "
                                 f"{s['not_assessed']} not assessed, {s['policies_counted']} polic(ies) counted.")
            return result
        result = await protection.for_firm(self.session, self.firm_id)
        s = result["summary"]
        result["message"] = (f"{s['households']} household(s): {s['with_gap']} with a gap, {s['clear']} clear, "
                             f"{s['not_assessed']} not assessed, {s['no_people']} with no one to assess.")
        return result

    async def _protection_write(self, action) -> dict[str, Any]:
        from app.aurea_core import protection
        from app.models.identity import User

        # The gateway checks the catalogue's roles; this re-checks, as for the other record-keeping tools.
        if self.role not in (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS):
            raise ExecutorError("Recording protection details is for advisers, paraplanners and operations.")
        user = await self.session.get(User, self.user_id)
        try:
            return await action(protection, user.email if user else "unknown")
        except protection.ProtectionError as exc:
            raise ExecutorError(str(exc))

    async def record_protection_policy(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from datetime import date as _date

        expires = None
        if inputs.get("expires_on"):
            try:
                expires = _date.fromisoformat(str(inputs["expires_on"]))
            except ValueError:
                raise ExecutorError("expires_on must be a date, YYYY-MM-DD.")
        person_id = _parse_uuid(inputs.get("person_id"), "person_id")
        result = await self._protection_write(lambda p, by: p.record_policy(
            self.session, self.firm_id, person_id=person_id, kind=str(inputs.get("kind") or "").strip().lower(),
            provider=inputs.get("provider") or "", source_document=inputs.get("source_document") or "", recorded_by=by,
            sum_insured=inputs.get("sum_insured"), annual_benefit=inputs.get("annual_benefit"),
            benefit_to_age=inputs.get("benefit_to_age"), expires_on=expires))
        result["message"] = f"Recorded {result['kind']} cover with {result['provider']} for {result['person']}."
        return result

    async def end_protection_policy(self, inputs: dict[str, Any]) -> dict[str, Any]:
        policy_id = _parse_uuid(inputs.get("policy_id"), "policy_id")
        result = await self._protection_write(lambda p, by: p.end_policy(
            self.session, self.firm_id, policy_id, reason=inputs.get("reason") or "", by=by))
        result["message"] = f"The {result['kind']} policy with {result['provider']} has ended and no longer counts."
        return result

    async def record_income(self, inputs: dict[str, Any]) -> dict[str, Any]:
        person_id = _parse_uuid(inputs.get("person_id"), "person_id")
        result = await self._protection_write(lambda p, by: p.record_income(
            self.session, self.firm_id, person_id=person_id, annual_income=inputs.get("annual_income"),
            source_document=inputs.get("source_document") or "", recorded_by=by))
        result["message"] = f"Recorded an income of {result['annual_income']:,.0f} for {result['person']}."
        return result

    async def read_reconciliation(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import reconciliation

        result = await reconciliation.overview(self.session, self.firm_id)
        s = result["summary"]
        parts = [f"{s['accounts']} account(s): {s['reconciled']} reconciled, {s['breaks']} with breaks, "
                 f"{s['stale_statement']} on a statement older than {s['fresh_within_days']} days, "
                 f"{s['no_statement']} with no custodian statement on file"]
        if s["open_breaks"]:
            parts.append(f"{s['open_breaks']} open break(s)")
        if s["set_aside_breaks"]:
            parts.append(f"{s['set_aside_breaks']} difference(s) set aside by a person, still on the record")
        result["message"] = "; ".join(parts) + "."
        return result

    async def import_custody_statement(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from datetime import date as _date

        from app.aurea_core import reconciliation
        from app.models.identity import User

        if self.role not in (UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM):
            raise ExecutorError("Importing a custodian statement is for operations and the portfolio team.")
        try:
            as_of = _date.fromisoformat(str(inputs.get("as_of") or ""))
        except ValueError:
            raise ExecutorError("as_of must be a date, YYYY-MM-DD.")
        user = await self.session.get(User, self.user_id)
        try:
            result = await reconciliation.import_statement(
                self.session, self.firm_id, account_id=_parse_uuid(inputs.get("account_id"), "account_id"),
                as_of=as_of, positions=inputs.get("positions"), cash_balance=inputs.get("cash_balance"),
                source_document=inputs.get("source_document") or "",
                imported_by=user.email if user else "unknown")
        except reconciliation.ReconciliationError as exc:
            raise ExecutorError(str(exc))
        b = result["breaks"]
        result["message"] = (
            f"Compared {result['positions_compared']} position(s) on "
            f"{result['household'] + ' · ' if result.get('household') else ''}{result['account']} as at {result['as_of']}: "
            f"{result['positions_matched']} matched; cash {result['cash'].replace('_', ' ')}. "
            f"{b['opened']} break(s) opened, {b['refreshed']} refreshed, {b['cleared']} cleared; "
            f"{b['open']} open on this account.")
        return result

    async def resolve_recon_break(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import reconciliation
        from app.models.identity import User

        if self.role not in (UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM):
            raise ExecutorError("Acting on a reconciliation break is for operations and the portfolio team.")
        user = await self.session.get(User, self.user_id)
        try:
            result = await reconciliation.resolve_break(
                self.session, self.firm_id, _parse_uuid(inputs.get("break_id"), "break_id"),
                action=str(inputs.get("action") or "").strip().lower(), note=inputs.get("note"),
                by=user.email if user else "unknown")
        except reconciliation.ReconciliationError as exc:
            raise ExecutorError(str(exc))
        result["message"] = f"The break is now {result['status']} (it was {result['was']})."
        return result

    async def read_billing(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import billing_views
        from app.models.graph import Household

        if inputs.get("household_id"):
            household_id = _parse_uuid(inputs["household_id"], "household_id")
            household = await self.session.get(Household, household_id)
            if household is None or household.firm_id != self.firm_id:
                raise ExecutorError(f"Household {household_id} not found or access denied")
            result = await billing_views.household_invoices(self.session, self.firm_id, household_id)
            result.update({"scope": "household", "household_name": household.name,
                           "open_run": None, "exceptions": None})
            n = len(result["invoices"])
            result["message"] = (f"{household.name} has no invoices yet." if n == 0 else
                                 f"{n} invoice(s)/credit note(s) for {household.name}.")
            return result

        result = await billing_views.overview(self.session, self.firm_id)
        ex = result["exceptions"]
        result.update({"scope": "firm", "invoices": result["open_run"]["invoices"] if result["open_run"] else []})
        result["message"] = (
            ("A billing run is awaiting approval. " if result["open_run"] else "No billing run is waiting. ")
            + f"{len(ex['no_agreement'])} mandate(s) have no fee agreement on file, "
              f"{len(ex['held'])} bill(s) are held by a block, {len(ex['unpaid'])} issued bill(s) are unpaid "
              "for lack of cash."
        )
        return result

    async def explain_invoice(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import billing_views
        from app.models.billing import Invoice

        if inputs.get("invoice_id"):
            invoice_id = _parse_uuid(inputs["invoice_id"], "invoice_id")
        elif inputs.get("household_id"):
            household_id = _parse_uuid(inputs["household_id"], "household_id")
            latest = (await self.session.execute(
                select(Invoice).where(Invoice.firm_id == self.firm_id, Invoice.household_id == household_id,
                                      Invoice.kind == "invoice", Invoice.status != "void")
                .order_by(Invoice.period_end.desc(), Invoice.created_at.desc()))).scalars().first()
            if latest is None:
                raise ExecutorError("That household has no invoices to explain.")
            invoice_id = latest.id
        else:
            raise ExecutorError("Give an invoice_id or a household_id.")

        view = await billing_views.invoice_detail(self.session, self.firm_id, invoice_id)
        if view is None:
            raise ExecutorError(f"Invoice {invoice_id} not found or access denied")
        calc, val = view.get("calculation") or {}, (view.get("calculation") or {}).get("valuation") or {}
        view["message"] = (
            f"{view['number'] or 'Draft'} for {view['household_name'] or view['mandate_name']}: "
            f"{view['currency']} {view['amount']:,.2f} for {view['period_start']} to {view['period_end']}, "
            f"on a balance of {calc.get('billable_value', 0):,.2f} valued on {val.get('basis_date')} "
            f"(oldest price {val.get('oldest_price_date') or 'not recorded'}; a single snapshot, not an "
            f"average balance)."
        )
        return view

    async def run_billing(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.atlas.base import Subject
        from app.atlas.runtime import AgentPausedError, run_agent
        from app.aurea_core import billing_run, billing_views
        from app.models.billing import BillingRun
        from app.models.enums import AgentKey
        from app.models.tenant import Firm

        firm = await self.session.get(Firm, self.firm_id)
        try:
            run = await run_agent(self.session, firm=firm, agent_key=AgentKey.BILLING,
                                  subject=Subject("firm", firm.id, firm.name), trigger="conversation")
        except AgentPausedError as exc:
            raise ExecutorError(str(exc))
        proposed = (await self.session.execute(
            select(BillingRun).where(BillingRun.agent_run_id == run.id))).scalars().first()
        if proposed is None:
            waiting = await billing_run.open_run(self.session, self.firm_id)
            reason = ("A billing run is already awaiting approval." if waiting else "Nothing is due to be billed.")
            view = await billing_views.run_view(self.session, waiting, with_invoices=True) if waiting else None
            return {"proposed": False, "reason": reason, "run": view, "message": reason,
                    "invoices": view["invoices"] if view else []}
        view = await billing_views.run_view(self.session, proposed, with_invoices=True)
        s = view["summary"]
        view_out = {"proposed": True, "run": view, "invoices": view["invoices"], "scope": "firm",
                    "open_run": view, "exceptions": {"no_agreement": s.get("no_agreement", []), "held": [], "unpaid": []},
                    "message": (f"Prepared a billing run: {s['invoices']} bill(s), {s['blocked']} held by a block, "
                                f"{s['amount_by_currency']}. Nothing is issued until operations or a branch leader "
                                "approves it.")}
        return view_out

    async def credit_invoice(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import billing_run, billing_views
        from app.core import decision_rights
        from app.models.billing import Invoice
        from app.models.enums import AgentKey

        invoice_id = _parse_uuid(inputs.get("invoice_id") or "", "invoice_id")
        reason = (inputs.get("reason") or "").strip()
        if not reason:
            raise ExecutorError("A reason is required to credit an invoice.")
        try:
            decision_rights.check(self.role, AgentKey.BILLING, "approve")
        except decision_rights.DecisionForbidden:
            raise ExecutorError("Crediting an invoice returns money that has already moved, so only "
                                "operations or a branch leader may do it.")
        inv = await self.session.get(Invoice, invoice_id)
        if inv is None or inv.firm_id != self.firm_id:
            raise ExecutorError(f"Invoice {invoice_id} not found or access denied")
        from app.models.identity import User
        user = await self.session.get(User, self.user_id)
        try:
            note = await billing_run.credit_invoice(self.session, inv, reason=reason, by=user.email if user else "unknown")
        except billing_run.BillingError as exc:
            raise ExecutorError(str(exc))
        view = billing_views.invoice_view(note, detail=True, credit_of_number=inv.invoice_number)
        view["scope"] = "household"
        view["invoices"] = [view]
        view["message"] = (f"Credited {inv.invoice_number} in full with {note.invoice_number}; the invoice is kept "
                           "and any fee already taken from cash was returned as its own entry.")
        return view

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
        real_priced = synthetic_priced = declared_priced = 0
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
                from app.aurea_core.pricing import price_basis
                basis = price_basis(latest_price.source, bool(latest_price.is_real)) if latest_price else "synthetic"
                if basis == "market":
                    real_priced += 1
                elif basis == "declared":
                    declared_priced += 1     # a stated valuation: not a market price, not invented
                else:
                    synthetic_priced += 1
                holdings.append({
                    "symbol": inst.symbol,
                    "name": inst.name,
                    "asset_class": str(inst.asset_class),
                    "quantity": float(h.quantity),
                    "market_value": mv,
                    "price_source": {"market": "real", "declared": "declared", "synthetic": "synthetic"}[basis],
                })

        cash = sum(float(a.cash_balance or 0) for a in accounts)
        total_value += cash

        return {
            "mandate_id": str(mandate_id),
            "mandate_name": mandate.name,
            "holdings": holdings,
            "cash": cash,
            "total_value": total_value,
            "pricing": {"real": real_priced, "declared": declared_priced, "synthetic": synthetic_priced},
            "message": f"{mandate.name}: {len(holdings)} holding(s), cash {cash:,.2f}, total value {total_value:,.2f}",
        }

    async def read_recommendations(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The proposals waiting for a decision, firm-scoped, with whether this role may decide
        each one. The answer comes from decision_rights, the same source decide_recommendation
        re-checks, so the card never offers a decision the server would refuse."""
        from app.core import decision_rights
        from app.models.enums import RecommendationStatus
        from app.models.governance import Recommendation

        status = str(inputs.get("status") or "open").lower()
        try:
            limit = max(1, min(int(inputs.get("limit") or 20), 50))
        except (TypeError, ValueError):
            limit = 20
        subject = str(inputs.get("subject") or "").strip()

        stmt = select(Recommendation).where(Recommendation.firm_id == self.firm_id)
        if status != "all":
            stmt = stmt.where(Recommendation.status == RecommendationStatus.PROPOSED)
        if subject:
            stmt = stmt.where(Recommendation.subject_label.ilike(f"%{subject}%"))
        total = (await self.session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        recs = (await self.session.execute(
            stmt.order_by(Recommendation.priority.asc(), Recommendation.created_at.desc()).limit(limit)
        )).scalars().all()

        role = UserRole(self.role)
        rows = [
            {
                "id": str(r.id),
                "title": r.title,
                "agent_key": str(r.agent_key),
                "subject_label": r.subject_label,
                "status": str(r.status),
                "priority": r.priority,
                "confidence": r.confidence,
                "tier": str(r.tier),
                "can_decide": role in decision_rights.roles_for(r.agent_key, "approve"),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in recs
        ]
        waiting = "waiting for a decision" if status != "all" else "on file"
        shown = f" (showing {len(rows)})" if total > len(rows) else ""
        return {
            "status": "all" if status == "all" else "open",
            "total": total,
            "recommendations": rows,
            "message": f"{total} proposal(s) {waiting}{shown}.",
        }

    async def read_agents(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The firm's agents with the caller's rights over each. The same roster the Workforce page
        shows (atlas.roster), plus whether this role may start the agent from a conversation."""
        from app.atlas import roster
        from app.core import agent_runs
        from app.models.enums import AgentKey

        only_runnable = str(inputs.get("runnable_only") or "").lower() == "true"
        rows = []
        for r in await roster.workforce(self.session, self.firm_id):
            key = AgentKey(str(r["agent_key"]))
            role_ok = agent_runs.may_run(self.role, key)
            if only_runnable and not role_ok:
                continue
            if key in agent_runs.NOT_FROM_CONVERSATION:
                why_not = "page-first: " + agent_runs.NOT_FROM_CONVERSATION[key]
            elif not role_ok:
                why_not = "your role does not start this agent"
            elif r["paused"]:
                why_not = "paused" + (f": {r['paused_reason']}" if r.get("paused_reason") else "")
            elif not r["enabled"]:
                why_not = "disabled for this firm"
            else:
                why_not = None
            rows.append({
                "agent_key": key.value, "name": r["name"], "stage": r["stage"],
                "tier": str(r["tier"]), "tier_label": agent_runs.TIER_LABEL.get(str(r["tier"]), str(r["tier"])),
                "status": r["status"], "paused": r["paused"], "enabled": r["enabled"],
                "runs": r["runs"], "proposals": r["contributions"],
                "last_action": r["last_action"], "last_at": r["last_at"],
                "can_run": why_not is None, "why_not": why_not,
            })
        startable = sum(1 for r in rows if r["can_run"])
        paused = sum(1 for r in rows if r["paused"])
        return {
            "agents": rows, "total": len(rows), "startable": startable, "paused": paused,
            "message": f"{len(rows)} agent(s); you may start {startable} from a conversation; {paused} paused.",
        }

    # ── run_agent: starting a run from a sentence ─────────────────────────────────────────────

    async def _resolve_run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Check that this role may start this agent on this subject, now, and say what it would
        resolve to. Nothing is written. Raises ExecutorError with the reason fit to show a user.

        Every id is read back scoped to the caller's firm: ids come from a language model, so
        another firm's household or mandate reads as not found."""
        from app.agents.catalogue import CATALOGUE
        from app.atlas.base import Subject
        from app.atlas.delegate import managed_mandates
        from app.atlas.registry import all_agents
        from app.core import agent_runs
        from app.models.enums import AgentKey, MandateType
        from app.models.tenant import Firm
        from app.provenance import policy

        raw = str(inputs.get("agent_key") or "").strip()
        try:
            key = AgentKey(raw)
        except ValueError:
            raise ExecutorError(f"There is no agent called '{raw}'. Ask which agents are available.")
        name = CATALOGUE[key]["name"] if key in CATALOGUE else key.value
        if key in agent_runs.NOT_FROM_CONVERSATION:
            raise ExecutorError(f"{name} cannot be started from a conversation: {agent_runs.NOT_FROM_CONVERSATION[key]}.")
        if key not in all_agents():
            raise ExecutorError(f"{name} is not available.")
        if not agent_runs.may_run(self.role, key):
            raise ExecutorError(f"Your role may not start {name}.")

        paused, reason = await policy.agent_paused(self.session, self.firm_id, key)
        if paused:
            raise ExecutorError(f"{name} is paused and will not run: {reason}")

        firm = await self.session.get(Firm, self.firm_id)
        if firm is None:
            raise ExecutorError("Firm not found.")

        need = agent_runs.SUBJECT_REQUIRED.get(key)
        household_in, mandate_in, case_in = inputs.get("household_id"), inputs.get("mandate_id"), inputs.get("case_id")
        household = mandate = None
        meeting_in = inputs.get("meeting_id")
        if need == "meeting" or meeting_in:
            from app.models.engagement import Meeting

            if key not in agent_runs.MEETING_AGENTS:
                raise ExecutorError(f"{name} does not work on a meeting.")
            if not meeting_in:
                raise ExecutorError(f"{name} works on one meeting. Give a meeting_id (read_meetings).")
            mtid = _parse_uuid(meeting_in, "meeting_id")
            meeting = (await self.session.execute(
                select(Meeting).where(Meeting.id == mtid, Meeting.firm_id == self.firm_id)
            )).scalar_one_or_none()
            if meeting is None:
                raise ExecutorError(f"Meeting {mtid} not found or access denied.")
            if key == AgentKey.MEETING_COMPANION and not (meeting.transcript or "").strip():
                raise ExecutorError(f"{meeting.title} has no transcript yet - add one on the meeting first.")
            hh = await self.session.get(Household, meeting.household_id)
            tier, guardrails = await policy.resolve_tier(self.session, self.firm_id, key, None)
            return {"key": key, "name": name, "firm": firm, "mandate_type": None, "tier": str(tier),
                    "guardrails": guardrails,
                    # A title that already names the client is not given the client's name twice.
                    "subject": Subject("meeting", meeting.id, f"{meeting.title} · {hh.name}"
                                       if hh and hh.name not in meeting.title else meeting.title)}
        if need == "onboarding_case" or case_in:
            from app.models.onboarding import OnboardingCase

            if need != "onboarding_case":
                raise ExecutorError(f"{name} does not work on an onboarding case.")
            if not case_in:
                raise ExecutorError(f"{name} works on one onboarding case. Give a case_id (read_onboarding_cases).")
            cid = _parse_uuid(case_in, "case_id")
            case = (await self.session.execute(
                select(OnboardingCase).where(OnboardingCase.id == cid, OnboardingCase.firm_id == self.firm_id)
            )).scalar_one_or_none()
            if case is None:
                raise ExecutorError(f"Onboarding case {cid} not found or access denied.")
            if case.status in ("approved", "rejected"):
                raise ExecutorError(f"{case.prospect_name}'s case is closed ({case.status}).")
            tier, guardrails = await policy.resolve_tier(self.session, self.firm_id, key, None)
            return {"key": key, "name": name, "firm": firm, "mandate_type": None, "tier": str(tier),
                    "guardrails": guardrails, "subject": Subject("onboarding_case", case.id, case.prospect_name)}
        if household_in:
            hid = _parse_uuid(household_in, "household_id")
            household = (await self.session.execute(
                select(Household).where(Household.id == hid, Household.firm_id == self.firm_id)
            )).scalar_one_or_none()
            if household is None:
                raise ExecutorError(f"Household {hid} not found or access denied.")
        if mandate_in:
            mid = _parse_uuid(mandate_in, "mandate_id")
            mandate = (await self.session.execute(
                select(Mandate).where(Mandate.id == mid, Mandate.firm_id == self.firm_id)
            )).scalar_one_or_none()
            if mandate is None:
                raise ExecutorError(f"Mandate {mid} not found or access denied.")

        mandate_type = None
        if need == "mandate":
            if mandate is None and household is not None:
                # "Rebalance the Chen Family" means the household's managed mandate - the same choice
                # the Delegate bar makes.
                managed = await managed_mandates(self.session, self.firm_id, household.id)
                if not managed:
                    raise ExecutorError(f"{household.name} has no managed mandate for {name} to work on.")
                mandate = managed[0]
            if mandate is None:
                raise ExecutorError(f"{name} works on one mandate. Give a mandate_id, or a household_id to use its managed mandate.")
            subject = Subject("mandate", mandate.id, mandate.name)
            mandate_type = MandateType(mandate.mandate_type)
        elif need == "household":
            if household is None:
                raise ExecutorError(f"{name} works on one household. Give a household_id.")
            subject = Subject("household", household.id, household.name)
        elif household is not None:
            subject = Subject("household", household.id, household.name)
        elif mandate is not None:
            raise ExecutorError(f"{name} works on a household or across the book, not on a mandate. Give a household_id, or neither.")
        else:
            subject = Subject("firm", firm.id, firm.name)

        tier, guardrails = await policy.resolve_tier(self.session, self.firm_id, key, mandate_type)
        return {"key": key, "name": name, "firm": firm, "subject": subject,
                "mandate_type": mandate_type, "tier": str(tier), "guardrails": guardrails}

    async def preview_run_agent(self, inputs: dict[str, Any], *, record_refusal: bool = False) -> dict[str, Any]:
        """What confirming a run would start - read from live policy, never typed by the model.

        The three controls are kept apart on purpose: `confirms` is what the Confirm button does,
        `autonomy` is what the agent may then do on its own, `decision` is who may approve what it
        proposes. A run can sit at Tier 3 and still stop at a proposal; confirming it never
        approves anything."""
        try:
            r = await self._resolve_run(inputs)
        except ExecutorError as exc:
            if record_refusal and str(exc).startswith("Your role"):
                from app.aurea_core.ai_governance import record_violation
                await record_violation(
                    self.session, self.firm_id, actor_user_id=self.user_id, actor_role=str(self.role),
                    violation_type="run_agent_forbidden", subject_key=str(inputs.get("agent_key")), detail=str(exc))
            raise
        return self._preview_of(r)

    def _preview_of(self, r: dict[str, Any]) -> dict[str, Any]:
        from app.agents.catalogue import CATALOGUE
        from app.core import agent_runs, decision_rights
        from app.models.enums import UserRole

        key, tier = r["key"], r["tier"]
        meta = CATALOGUE.get(key, {})
        approvers = decision_rights.roles_for(key, "approve")
        held = agent_runs.forced_to_proposal(key)
        return {
            "agent_key": key.value, "agent_name": r["name"], "stage": meta.get("stage"),
            "does": meta.get("acts"), "checkpoint": meta.get("checkpoint"),
            "subject_type": r["subject"].type, "subject_label": r["subject"].label,
            "confirms": "start_only",
            "autonomy": {
                "tier": tier, "label": agent_runs.TIER_LABEL.get(tier, tier),
                "acts_on_its_own": agent_runs.acts_on_its_own(key, tier),
                "held_to_proposal": held,
            },
            "decision": {
                "roles": sorted(x.value for x in approvers),
                "any_staff": approvers == decision_rights.STAFF,
                "you_may_decide": UserRole(self.role) in approvers,
            },
        }

    async def run_agent(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Start the run through the same domain path as the Cockpit's Delegate bar: its streaming
        run (atlas.streaming.stream_run), with its kill-switch check, tier resolution, ledger and
        conduct surveillance. The beats are folded into a card as they arrive.

        The run proposes; it never approves. At a tier that would act on its own it still acts
        only where the agent does not move a client's book or cash. Nothing here calls
        runtime.decide."""
        from app.atlas import narration, streaming
        from app.core import agent_runs
        from app.models.enums import RecommendationStatus
        from app.models.governance import Recommendation

        r = await self._resolve_run(inputs)       # re-checked at execution, not just at confirmation
        key = r["key"]
        card = narration.new_card(self._preview_of(r))
        if self.on_card:
            await self.on_card(card)

        rationale: list[str] = []
        rec_ids: list[str] = []
        outcome: dict[str, Any] = {}
        async for ev in streaming.stream_run(
            self.session, firm=r["firm"], agent_key=key, subject=r["subject"],
            mandate_type=r["mandate_type"], require_human=agent_runs.forced_to_proposal(key),
        ):
            phase = ev.get("phase")
            if phase == "error":
                raise ExecutorError(str(ev.get("message") or "The run could not start."))
            if phase == "rationale" and ev.get("chunk"):
                rationale.append(ev["chunk"])
            if phase in ("empty", "done"):
                outcome = ev
                rec_ids = list(ev.get("recommendation_ids") or [])
            if narration.fold(card, ev) and self.on_card:
                await self.on_card(card)

        proposals: list[dict[str, Any]] = []
        if rec_ids:
            rows = (await self.session.execute(
                select(Recommendation).where(
                    Recommendation.firm_id == self.firm_id,
                    Recommendation.id.in_([uuid.UUID(x) for x in rec_ids]))
                .order_by(Recommendation.priority.asc(), Recommendation.created_at.asc())
            )).scalars().all()
            proposals = [{"id": str(p.id), "title": p.title, "priority": p.priority,
                          "confidence": p.confidence, "status": str(p.status)} for p in rows]
        if not proposals:
            status = "nothing"
        elif any(p["status"] == str(RecommendationStatus.PROPOSED) for p in proposals):
            status = "proposed"
        else:
            status = "acted"
        narration.settle(card, status=status, proposals=proposals)
        text = narration.sentence(card, rationale="".join(rationale))
        return {
            "scope": "run", "status": status, "agent_key": key.value,
            "run_id": card["run_id"], "recommendation_ids": rec_ids,
            "autonomous": bool(outcome.get("autonomous")) and status == "acted",
            "card": card, "message": text, "suggestions": narration.follow_ups(card),
        }

    # ── Reads that stand in for pages: each wraps the function the page's route calls ─────────

    @staticmethod
    def _limit(value: Any, default: int, top: int) -> int:
        try:
            return max(1, min(int(value or default), top))
        except (TypeError, ValueError):
            return default

    async def _firm(self):
        from app.models.tenant import Firm

        firm = await self.session.get(Firm, self.firm_id)
        if firm is None:
            raise ExecutorError("Firm not found.")
        return firm

    async def _today(self):
        """Today on the firm's calendar, which is the day a person means (aurea_core.firm_time)."""
        from app.aurea_core import firm_time

        return firm_time.today(await self._firm())

    async def open_view(self, inputs: dict[str, Any], turn: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Name a view for the workspace to open beside the conversation. It reads no figures and
        returns none: the view loads its own data through its page's routes. An id it is given must
        appear in a result of a tool called earlier in the same turn (`turn`, which the gateway
        passes; any other caller passes none, so every id is refused) and must be the caller's
        firm's - an id from the model's memory is not trusted on its own."""
        import json

        from app.core.tools import OPEN_VIEWS
        from app.models.billing import Invoice
        from app.models.engagement import Meeting
        from app.models.governance import Recommendation
        from app.models.onboarding import OnboardingCase

        view = str(inputs.get("view") or "").strip().lower()
        spec = OPEN_VIEWS.get(view)
        if spec is None:
            raise ExecutorError(f"There is no view called '{view}'. Views: {', '.join(OPEN_VIEWS)}.")
        route, label, roles = spec
        if self.role != UserRole.ADMIN and self.role not in roles:
            raise ExecutorError(f"The {label} view is not one this role opens.")

        seen = json.dumps([t.get("result") for t in (turn or [])], default=str)

        finder = {Household: "search_households", Recommendation: "read_recommendations", Invoice: "read_billing",
                  OnboardingCase: "read_onboarding_cases", Meeting: "read_meetings"}

        async def grounded(field: str, model) -> uuid.UUID:
            value = _parse_uuid(inputs.get(field) or "", field)
            if str(value) not in seen:
                raise ExecutorError(
                    f"{field} {value} did not come from a tool called this turn - look it up first ({finder[model]}).")
            owned = (await self.session.execute(
                select(model.id).where(model.id == value, model.firm_id == self.firm_id))).scalar_one_or_none()
            if owned is None:
                raise ExecutorError(f"{field} {value} not found or access denied")
            return value

        if view == "client":
            route = f"{route}/{await grounded('household_id', Household)}"
        elif view == "recommendations" and inputs.get("recommendation_id"):
            route = f"{route}?rec={await grounded('recommendation_id', Recommendation)}"
        elif view == "billing" and inputs.get("invoice_id"):
            route = f"{route}?invoice={await grounded('invoice_id', Invoice)}"
        elif view == "onboarding_case":
            route = f"{route}/{await grounded('case_id', OnboardingCase)}"
        elif view == "meeting":
            route = f"{route}/{await grounded('meeting_id', Meeting)}"

        return {"view": route, "kind": view, "message": f"The {label} view is open beside the conversation."}

    async def read_agent_history(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Past agent runs, newest first - the Agent history page's read (atlas.history)."""
        from collections import Counter

        from app.agents.catalogue import CATALOGUE
        from app.atlas import history
        from app.models.enums import AgentKey

        agent_key = str(inputs.get("agent_key") or "").strip() or None
        if agent_key:
            try:
                agent_key = AgentKey(agent_key).value
            except ValueError:
                raise ExecutorError(f"There is no agent called '{agent_key}'.")
        status = str(inputs.get("status") or "").strip() or None
        rows = await history.run_history(
            self.session, self.firm_id, agent_key=agent_key, status=status,
            limit=self._limit(inputs.get("limit"), 20, 50))
        for r in rows:
            meta = CATALOGUE.get(r["agent_key"]) or {}
            r["agent_name"] = meta.get("name") or str(r["agent_key"]).replace("_", " ").title()
        by_status = dict(Counter(str(r["status"]) for r in rows))
        scope = f" for {rows[0]['agent_name']}" if agent_key and rows else ""
        return {
            "runs": rows, "shown": len(rows), "by_status": by_status,
            "filters": {"agent_key": agent_key, "status": status},
            "message": f"{len(rows)} most recent run(s){scope}." if rows else "No agent runs match.",
        }

    async def read_capacity(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Capacity & outcomes - the Capacity page's read (atlas.history). The hours figure is an
        estimate and is returned with its basis."""
        from app.atlas import history

        out = await history.capacity_outcomes(self.session, self.firm_id)
        out["estimate"] = {"minutes_per_item": history.MINUTES_PER_ITEM, "measured": False}
        out["message"] = (
            f"{out['total_agent_runs']} agent run(s) and {out['decisions_made']} decision(s); "
            f"{out['open_items']} proposal(s) still open. About {out['estimated_hours_reclaimed']} hours "
            f"reclaimed - an estimate at {history.MINUTES_PER_ITEM} minutes per run or decision, not a measurement."
        )
        return out

    async def read_analytics(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The Analytics page's headline figures (aurea_core.analytics.overview)."""
        from app.aurea_core.analytics import overview

        firm = await self._firm()
        full = await overview.compute_all(self.session, self.firm_id)
        h = full["headline"]
        bits = []
        if h.get("firm_aum") is not None:
            bits.append(f"AUM {h['firm_aum']:,.0f} {firm.base_currency}")
        bits.append(f"{h.get('clients', 0)} client household(s)")
        if h.get("data_quality_score") is not None:
            bits.append(f"data quality {h['data_quality_score']}")
        if h.get("drift_breaches"):
            bits.append(f"{h['drift_breaches']} mandate(s) outside tolerance")
        # What each figure rests on, so no surface shows an estimate or a client's own declaration bare.
        fee_basis = (full["practice"].get("fee_margin") or {}).get("fee_revenue_basis") or "estimate"
        bases = {"fee_revenue": fee_basis, "firm_margin": "estimate (built on estimated revenue)",
                 "firm_wallet_share": "declared", "consolidation_opportunity": "declared"}
        return {"headline": h, "bases": bases, "currency": firm.base_currency,
                "message": "Headline figures: " + "; ".join(bits) + "."}

    async def read_onboarding_cases(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The onboarding board - cases with their four derived tracks (api.onboarding.cases_overview)."""
        from collections import Counter

        from app.api.onboarding import cases_overview

        firm = await self._firm()
        want = str(inputs.get("status") or "open").lower()
        cases = await cases_overview(self.session, firm)
        rows = []
        for c in cases:
            t = c["tracks"]
            rows.append({
                "id": c["id"], "prospect_name": c["prospect_name"], "status": c["status"],
                "segment": c["segment"], "registration_type": c["registration_type"],
                "sla_status": c["sla_status"], "nigo_flag": bool(c["nigo_flag"]),
                "readiness_score": c["readiness_score"], "aml_risk_tier": c["aml_risk_tier"],
                "edd_status": c["edd_status"], "cip_status": c["cip_status"],
                "position": t["position"], "owner": t["owner"], "ready_to_activate": t["ready_to_activate"],
                "blocked_tracks": t["blocked_tracks"],
                "tracks": [{"code": x["code"], "label": x["label"], "state": x["state"]} for x in t["tracks"]],
            })
        total = len(rows)
        is_open = lambda r: r["status"] not in ("approved", "rejected")  # noqa: E731
        shown = [r for r in rows if is_open(r)] if want != "all" else rows
        limit = self._limit(inputs.get("limit"), 25, 50)
        summary = {
            "total": total, "open": sum(1 for r in rows if is_open(r)),
            "by_status": dict(Counter(r["status"] for r in rows)),
            "sla_breached": sum(1 for r in rows if is_open(r) and r["sla_status"] == "breached"),
            "blocked": sum(1 for r in rows if is_open(r) and r["blocked_tracks"]),
            "ready_to_activate": sum(1 for r in rows if is_open(r) and r["ready_to_activate"]),
        }
        return {
            "cases": shown[:limit], "shown": min(len(shown), limit), "scope": "all" if want == "all" else "open",
            "summary": summary,
            "message": (f"{summary['open']} open onboarding case(s) of {total}; {summary['blocked']} blocked, "
                        f"{summary['sla_breached']} past their SLA, {summary['ready_to_activate']} ready to activate."),
        }

    async def read_meetings(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The Meetings page's list, with each meeting's brief, transcript and captured notes
        (api.engage.list_meetings / get_meeting). Only this firm's meetings, and a household is
        read back inside the firm before it filters anything."""
        from app.core.db import utcnow
        from app.models.engagement import Meeting

        stmt = select(Meeting).where(Meeting.firm_id == self.firm_id)
        if inputs.get("household_id"):
            hid = _parse_uuid(inputs["household_id"], "household_id")
            hh = await self.session.get(Household, hid)
            if hh is None or hh.firm_id != self.firm_id:
                raise ExecutorError(f"Household {hid} not found or access denied")
            stmt = stmt.where(Meeting.household_id == hid)
        rows = (await self.session.execute(stmt.order_by(Meeting.created_at.desc()))).scalars().all()
        names = {h.id: h.name for h in (await self.session.execute(
            select(Household).where(Household.firm_id == self.firm_id))).scalars().all()}
        limit = self._limit(inputs.get("limit"), 20, 50)
        now = utcnow()
        meetings = []
        for m in rows[:limit]:
            brief, notes = m.brief or {}, m.notes or {}
            meetings.append({
                "id": str(m.id), "title": m.title, "household_id": str(m.household_id),
                "household_name": names.get(m.household_id), "status": str(m.status),
                "scheduled_at": m.scheduled_at.isoformat() if m.scheduled_at else None,
                # Not held, and its time has gone by - so not upcoming, whatever the status says.
                "date_passed": m.date_passed(now),
                "brief_prepared": bool(brief.get("agenda")), "agenda": brief.get("agenda") or [],
                "watch_items": [w.get("title") for w in brief.get("watch_items") or [] if isinstance(w, dict)],
                "has_transcript": bool((m.transcript or "").strip()),
                "notes_captured": bool(notes.get("summary")), "sentiment": notes.get("sentiment"),
                "action_items": notes.get("action_items") or [],
            })
        by_status: dict[str, int] = {}
        for m in rows:
            by_status[str(m.status)] = by_status.get(str(m.status), 0) + 1
        passed = sum(1 for m in rows if m.date_passed(now))
        upcoming = sum(1 for m in rows if m.scheduled_at is not None and str(m.status) != "completed"
                       and not m.date_passed(now))
        return {
            "meetings": meetings, "shown": len(meetings), "total": len(rows), "by_status": by_status,
            "upcoming": upcoming, "date_passed": passed, "as_of": (await self._today()).isoformat(),
            "message": (f"{len(rows)} meeting(s)" + (f"; showing {len(meetings)}" if len(meetings) < len(rows) else "")
                        + (f" - {', '.join(f'{n} {s}' for s, n in sorted(by_status.items()))}" if rows else "")
                        + f". {upcoming} upcoming"
                        + (f"; {passed} not held although the date has passed" if passed else "") + "."),
        }

    async def read_tasks(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The Tasks page's list (api.engage.list_tasks). Only this firm's tasks, and a household
        is read back inside the firm before it filters anything."""
        from app.aurea_core import tasks

        status = str(inputs.get("status") or "open").strip().lower()
        if status not in tasks.STATUSES:
            raise ExecutorError(f"There is no task status '{status}'. Use {', '.join(tasks.STATUSES)}.")
        household_id = None
        if inputs.get("household_id"):
            household_id = (await self._task_household(inputs["household_id"])).id
        rows = await tasks.for_firm(self.session, self.firm_id, status=status, household_id=household_id,
                                    assigned_to=self.user_id if inputs.get("assigned_to_me") else None)
        today = await self._today()
        all_views = await tasks.views(self.session, rows, today)
        shown = all_views[:self._limit(inputs.get("limit"), 20, 50)]
        overdue = sum(1 for t in all_views if t["overdue"])
        unassigned = sum(1 for t in all_views if t["status"] == "open" and not t["assigned_to"])
        return {
            "tasks": shown, "shown": len(shown), "total": len(all_views), "status": status,
            "overdue": overdue, "unassigned": unassigned, "as_of": today.isoformat(),
            "message": (f"{len(all_views)} {'' if status == 'all' else status + ' '}task(s)"
                        + (f"; showing {len(shown)}" if len(shown) < len(all_views) else "")
                        + f". {overdue} overdue, {unassigned} unassigned."),
        }

    async def _task_household(self, value: Any) -> Household:
        hid = _parse_uuid(value, "household_id")
        hh = await self.session.get(Household, hid)
        if hh is None or hh.firm_id != self.firm_id:
            raise ExecutorError(f"Household {hid} not found or access denied")
        return hh

    async def _task(self, inputs: dict[str, Any]):
        from app.aurea_core import tasks

        try:
            return await tasks.get(self.session, self.firm_id, _parse_uuid(inputs.get("task_id") or "", "task_id"))
        except tasks.TaskError as exc:
            raise ExecutorError(str(exc))

    async def _task_assignee(self, name: Any):
        """'me', 'nobody' (None) or a colleague's name, inside this firm."""
        from app.aurea_core import tasks

        wanted = str(name or "").strip()
        if not wanted:
            raise ExecutorError("Say who should hold the task: a colleague's name, 'me' or 'nobody'.")
        if wanted.lower() == "nobody":
            return None
        try:
            if wanted.lower() == "me":
                return await tasks.staff_member(self.session, self.firm_id, self.user_id)
            return await tasks.staff_named(self.session, self.firm_id, wanted)
        except tasks.TaskError as exc:
            raise ExecutorError(str(exc))

    async def _task_due(self, value: Any):
        """A due date for a new task. One already gone is refused with today's date in the words,
        because it almost always means a day like 'Friday' was worked out wrongly."""
        from datetime import date

        from app.aurea_core import firm_time

        text = str(value or "").strip()
        if not text:
            return None
        try:
            due = date.fromisoformat(text)
        except ValueError:
            raise ExecutorError(f"'{text}' is not a date. Give it as YYYY-MM-DD.")
        today = await self._today()
        if due < today:
            raise ExecutorError(f"{firm_time.spoken(due)} has already passed: today is {firm_time.spoken(today)}.")
        return due

    async def _task_result(self, task, message: str) -> dict[str, Any]:
        from app.aurea_core import tasks

        await self.session.flush()
        today = await self._today()
        views = await tasks.views(self.session, [task], today)
        return {"tasks": views, "shown": 1, "total": 1, "status": str(task.status), "overdue": sum(v["overdue"] for v in views),
                "unassigned": 0 if task.assigned_to else 1, "as_of": today.isoformat(), "message": message}

    async def complete_task(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import tasks

        task = await self._task(inputs)
        try:
            tasks.complete(task)
        except tasks.TaskError as exc:
            raise ExecutorError(str(exc))
        return await self._task_result(task, f"'{task.title}' is done.")

    async def assign_task(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import tasks

        task = await self._task(inputs)
        to = await self._task_assignee(inputs.get("assignee_name"))
        try:
            tasks.assign(task, to, self.user_id)
        except tasks.TaskError as exc:
            raise ExecutorError(str(exc))
        return await self._task_result(task, f"'{task.title}' is with {to.full_name}." if to else f"'{task.title}' is unassigned.")

    async def _new_task(self, inputs: dict[str, Any]):
        from app.aurea_core import tasks

        household = await self._task_household(inputs["household_id"]) if inputs.get("household_id") else None
        assignee = await self._task_assignee(inputs.get("assignee_name") or "me")
        due = await self._task_due(inputs.get("due_date"))
        try:
            return tasks.new(self.firm_id, str(inputs.get("title") or ""), household=household,
                             due_date=due, assignee=assignee, by=self.user_id), assignee
        except tasks.TaskError as exc:
            raise ExecutorError(str(exc))

    async def create_task(self, inputs: dict[str, Any]) -> dict[str, Any]:
        task, _ = await self._new_task(inputs)
        self.session.add(task)
        return await self._task_result(task, f"Added '{task.title}'.")

    async def preview_change(self, tool_key: str, inputs: dict[str, Any]) -> dict[str, Any] | None:
        """What a Confirm card names, so a person confirms this task and not "a task": the record
        and the change, read now, as label and value rows. Raises what executing would raise, so
        a change that would only fail is refused instead of offered. None for a tool without one."""
        def rows(*pairs: tuple[str, Any]) -> dict[str, Any]:
            return {"rows": [{"label": k, "value": str(v)} for k, v in pairs if v]}

        if tool_key not in ("complete_task", "assign_task", "create_task"):
            return None
        from app.aurea_core import firm_time, tasks

        def due(task) -> str | None:
            return firm_time.spoken(task.due_date) if task.due_date else None

        if tool_key == "create_task":
            task, assignee = await self._new_task(inputs)
            return rows(("Task", task.title), ("Client", task.subject_label), ("Due", due(task)),
                        ("Assign to", assignee.full_name if assignee else "Nobody"))
        task = await self._task(inputs)
        if str(task.status) != "open":
            raise ExecutorError(f"'{task.title}' is already {task.status}.")
        common = (("Task", task.title), ("Client", task.subject_label), ("Due", due(task)))
        if tool_key == "complete_task":
            return rows(*common)
        to = await self._task_assignee(inputs.get("assignee_name"))
        held = (await tasks.views(self.session, [task], await self._today()))[0]["assigned_to_name"]
        return rows(*common, ("Held by", held), ("Assign to", to.full_name if to else "Nobody"))

    async def read_provenance(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The Provenance page in brief: ledger integrity, open surveillance flags, agent quality and
        recent autonomy changes (provenance.ledger / evaluation / views)."""
        from app.agents.catalogue import CATALOGUE
        from app.provenance import ledger, views
        from app.provenance.evaluation import latest_evaluations

        def name(key: str) -> str:
            return (CATALOGUE.get(key) or {}).get("name") or str(key).replace("_", " ").title()

        chain = await ledger.verify_chain(self.session, self.firm_id)
        recent = [
            {"seq": e["seq"], "event_type": e["event_type"], "agent_name": name(e["agent_key"]) if e["agent_key"] else None,
             "actor": e["actor"], "created_at": e["created_at"]}
            for e in await views.recent_ledger(self.session, self.firm_id, 8)
        ]
        flags = await views.surveillance_flags(self.session, self.firm_id)
        open_flags = [f for f in flags if not f["resolved"]]
        by_severity: dict[str, int] = {}
        for f in open_flags:
            by_severity[str(f["severity"])] = by_severity.get(str(f["severity"]), 0) + 1
        evaluations = [
            {"agent_key": e["agent_key"], "agent_name": name(e["agent_key"]), "quality_score": e["quality_score"],
             "grade": e["grade"], "sample_size": e["sample_size"]}
            for e in await latest_evaluations(self.session, self.firm_id)
        ]
        changes = [{**c, "agent_name": name(c["agent_key"])} for c in await views.autonomy_changes(self.session, self.firm_id, 5)]
        integrity = (f"The ledger's hash chain is intact over {chain['count']} entries."
                     if chain["valid"] else f"The ledger's hash chain is BROKEN at entry {chain['broken_at_seq']}.")
        return {
            "ledger": {**chain, "recent": recent},
            "surveillance": {
                "open": len(open_flags), "by_severity": by_severity,
                "flags": [{"id": f["id"], "severity": f["severity"], "category": f["category"],
                           "finding": f["finding"], "target_agent_name": name(f["target_agent_key"]) if f["target_agent_key"] else None,
                           "auto_paused_agent": f["auto_paused_agent"], "escalated": f["escalated"],
                           "created_at": f["created_at"]} for f in open_flags[:6]],
            },
            "evaluations": evaluations,
            "autonomy_changes": changes,
            "message": f"{integrity} {len(open_flags)} open surveillance flag(s). {len(evaluations)} agent(s) scored.",
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
