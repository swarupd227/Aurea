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

import re
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


# Words a person says about a proposal that no proposal's title carries.
_PROPOSAL_NOISE = {"the", "a", "an", "of", "for", "and", "on", "proposal", "proposals", "recommendation", "recommendations"}


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
            "read_tax_position": self.read_tax_position,
            "read_licences": self.read_licences,
            "record_client_consent": self.record_client_consent,
            "read_protection": self.read_protection,
            "record_protection_policy": self.record_protection_policy,
            "end_protection_policy": self.end_protection_policy,
            "set_care_assumptions": self.set_care_assumptions,
            "record_income": self.record_income,
            "read_risk_profiles": self.read_risk_profiles,
            "decide_risk_profile": self.decide_risk_profile,
            "read_client_reviews": self.read_client_reviews,
            "record_client_review": self.record_client_review,
            "set_review_cadence": self.set_review_cadence,
            "book_client_review": self.book_client_review,
            "read_ips_monitoring": self.read_ips_monitoring,
            "read_client_exits": self.read_client_exits,
            "read_aml_reviews": self.read_aml_reviews,
            "rate_aml_risk": self.rate_aml_risk,
            "read_repapering": self.read_repapering,
            "start_repapering": self.start_repapering,
            "record_repapering": self.record_repapering,
            "record_private_transfer_item": self.record_private_transfer_item,
            "read_onboarding_scoreboard": self.read_onboarding_scoreboard,
            "record_onboarding_feedback": self.record_onboarding_feedback,
            "read_account_features": self.read_account_features,
            "request_account_feature": self.request_account_feature,
            "decide_account_feature": self.decide_account_feature,
            "record_senior_hold": self.record_senior_hold,
            "read_money_in": self.read_money_in,
            "record_bank_link": self.record_bank_link,
            "record_deposit": self.record_deposit,
            "revalidate_standing_instruction": self.revalidate_standing_instruction,
            "read_transaction_monitoring": self.read_transaction_monitoring,
            "decide_monitoring_alert": self.decide_monitoring_alert,
            "record_investigation_step": self.record_investigation_step,
            "read_transfer_files": self.read_transfer_files,
            "record_transfer_event": self.record_transfer_event,
            "record_rollover_facts": self.record_rollover_facts,
            "read_document_matrix": self.read_document_matrix,
            "record_registration_detail": self.record_registration_detail,
            "set_custodian_documents": self.set_custodian_documents,
            "read_consents": self.read_consents,
            "record_consent": self.record_consent,
            "read_onboarding_queues": self.read_onboarding_queues,
            "record_screening_decision": self.record_screening_decision,
            "sign_off_edd": self.sign_off_edd,
            "read_trades": self.read_trades,
            "read_tax_documents": self.read_tax_documents,
            "record_tax_document": self.record_tax_document,
            "read_cash_movements": self.read_cash_movements,
            "request_cash_movement": self.request_cash_movement,
            "record_cash_paid": self.record_cash_paid,
            "cancel_cash_request": self.cancel_cash_request,
            "record_standing_instruction": self.record_standing_instruction,
            "end_standing_instruction": self.end_standing_instruction,
            "start_phase_in": self.start_phase_in,
            "end_phase_in": self.end_phase_in,
            "read_client_file": self.read_client_file,
            "record_trusted_contact": self.record_trusted_contact,
            "record_estate_document": self.record_estate_document,
            "record_life_event": self.record_life_event,
            "read_financial_plan": self.read_financial_plan,
            "record_income_stream": self.record_income_stream,
            "end_income_stream": self.end_income_stream,
            "record_education_goal": self.record_education_goal,
            "read_equity_comp": self.read_equity_comp,
            "record_equity_grant": self.record_equity_grant,
            "end_equity_grant": self.end_equity_grant,
            "record_concentration_plan": self.record_concentration_plan,
            "read_product_shelf": self.read_product_shelf,
            "record_product_decision": self.record_product_decision,
            "record_investor_status": self.record_investor_status,
            "set_approved_products_only": self.set_approved_products_only,
            "start_client_departure": self.start_client_departure,
            "record_client_death": self.record_client_death,
            "record_executor": self.record_executor,
            "record_account_transfer": self.record_account_transfer,
            "close_client_exit": self.close_client_exit,
            "withdraw_client_exit": self.withdraw_client_exit,
            "record_ips": self.record_ips,
            "read_balance_sheet": self.read_balance_sheet,
            "record_balance_item": self.record_balance_item,
            "end_balance_item": self.end_balance_item,
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
            "screen_book": self.screen_book,
            "read_tax_book": self.read_tax_book,
            "read_estate": self.read_estate,
            "read_family": self.read_family,
            "read_behavioural": self.read_behavioural,
            "read_regulatory_countdown": self.read_regulatory_countdown,
            "read_skills": self.read_skills,
            "create_skill": self.create_skill,
            "update_skill": self.update_skill,
            "delete_skill": self.delete_skill,
            "clone_skill": self.clone_skill,
            "run_skill": self.run_skill,
            "read_book_batches": self.read_book_batches,
            "start_book_batch": self.start_book_batch,
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
            "read_reports": self.read_reports,
            "withdraw_report": self.withdraw_report,
            "read_messages": self.read_messages,
            "send_client_message": self.send_client_message,
            "read_provenance": self.read_provenance,
            "resolve_surveillance_flag": self.resolve_surveillance_flag,
            "escalate_surveillance_flag": self.escalate_surveillance_flag,
            "evaluate_agents": self.evaluate_agents,
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

        # A client is searched for by their own name as often as by the household's.
        named = f"%{query}%"
        households = (
            await self.session.execute(
                select(Household)
                .where(Household.firm_id == self.firm_id, or_(
                    Household.name.ilike(named),
                    Household.id.in_(select(Person.household_id).where(Person.firm_id == self.firm_id, Person.full_name.ilike(named))),
                    Household.id.in_(select(LegalEntity.household_id).where(LegalEntity.firm_id == self.firm_id, LegalEntity.name.ilike(named)))))
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
                "jurisdiction": (h.values or {}).get("jurisdiction"),
                "accounts": [{
                    "account_id": str(a.id), "name": a.name, "custodian": a.custodian,
                } for a in accounts],
            })

        if not results:
            near = await self._near_households(str(query))
            names = ", ".join(n["name"] for n in near)
            return {
                "results": [], "did_you_mean": near,
                "message": (f"No household matches '{query}'. Closest on file: {names}. None of them has been looked up: "
                            "ask which was meant before using one." if near
                            else f"No household matches '{query}', and no name on file is close to it."),
            }
        return {
            "results": results,
            "message": f"Found {len(results)} household(s) matching '{query}'",
        }

    async def _near_households(self, query: str) -> list[dict[str, Any]]:
        """This firm's households nearest a name that matched nothing, by their own name, their people's
        or their entities'."""
        from app.aurea_core import client_names

        names = {h: n for h, n in (await self.session.execute(
            select(Household.id, Household.name).where(Household.firm_id == self.firm_id))).all()}
        people = (await self.session.execute(
            select(Person.household_id, Person.full_name).where(Person.firm_id == self.firm_id))).all()
        entities = (await self.session.execute(
            select(LegalEntity.household_id, LegalEntity.name).where(LegalEntity.firm_id == self.firm_id))).all()
        found = client_names.closest(query, [*names.items(), *[(h, n) for h, n in [*people, *entities] if h in names]])
        return [{"household_id": str(h), "name": names[h], **({"matched": said} if said != names[h] else {})} for h, said in found]

    async def check_household_wash_sale(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import tax_intelligence

        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")
        result = await tax_intelligence.household_wash_sale_calendar(
            self.session, household_id, firm_id=self.firm_id)
        if result is None:
            raise ExecutorError(f"Household {household_id} not found or access denied")

        from app.aurea_core import harvest_guard
        from app.models.tenant import Firm
        household = await self.session.get(Household, household_id)
        if harvest_guard.applies(await self.session.get(Firm, self.firm_id), household.values if household else None):
            return {**result, "violations": [], "total_disallowed_loss": 0,
                    "message": "India has no wash-sale rule, so there is no calendar to check. What applies is "
                               "the harvest guard on each proposal; read_tax_position has the tax position."}

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

    async def _valuation_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from datetime import date as _date

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
        return {"instrument_id": inst.id, "as_of": as_of, "unit_value": unit_value,
                "source_document": inputs.get("source_document") or "", "notes": inputs.get("notes")}

    async def _valuation_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, markets, valuations

        args = await self._valuation_args(inputs)
        say = markets.money_for(await self._firm())
        try:
            r = await valuations.prepare_valuation(self.session, self.firm_id, **args)
        except valuations.ValuationError as exc:
            raise ExecutorError(str(exc))
        inst = r["inst"]
        return [("Fund", f"{inst.symbol} · {inst.name}"), ("Unit value", f"{r['unit_value']:,.4f} {inst.currency}"),
                ("Was", f"{r['prior_value']:,.4f} on {r['prior_date']}" if r["prior_value"] else "Nothing on file"),
                ("Change", f"{r['change']:+.1%}" if r["change"] is not None else None),
                ("As at", firm_time.spoken(args["as_of"])), ("Holdings revalued", str(len(r["held"]))),
                ("Value held", f"{say(r['value_before'])} becomes {say(r['value_after'])}"),
                ("Document", args["source_document"].strip()), ("Note", (args["notes"] or "").strip() or None)]

    async def record_valuation(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import valuations
        from app.models.identity import User
        from app.models.portfolio import Instrument as _Instrument

        # The gateway checks the catalogue's roles; this re-checks, as for the other tools that move money.
        if self.role not in (UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM):
            raise ExecutorError("Recording a valuation changes client balances and fees, so only "
                                "operations or the portfolio team may do it.")
        args = await self._valuation_args(inputs)
        inst = await self.session.get(_Instrument, args["instrument_id"])
        user = await self.session.get(User, self.user_id)
        try:
            result = await valuations.record_valuation(
                self.session, self.firm_id, recorded_by=user.email if user else "unknown", **args)
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

    async def _refresh_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, price_freshness

        try:
            conn = await price_freshness.prepare_refresh(self.session, self.firm_id)
        except price_freshness.RefreshRefused as exc:
            raise ExecutorError(str(exc))
        s = (await price_freshness.price_freshness(self.session, self.firm_id))["summary"]
        last = conn.last_synced_at
        return [("Held instruments", str(s["total"])), ("Not current", str(s["total"] - s["current"])),
                ("Last fetched", firm_time.spoken(firm_time.today(await self._firm(), last)) if last else "Never"),
                ("Effect", "Holdings revalue at the new prices, which changes balances and fees")]

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

    async def read_tax_position(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import markets, tax_intelligence
        from app.models.tenant import Firm

        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")
        firm = await self.session.get(Firm, self.firm_id)
        result = await tax_intelligence.for_household(self.session, household_id, firm.jurisdiction, firm_id=self.firm_id)
        if result is None:
            raise ExecutorError(f"Household {household_id} not found or access denied")
        if result.get("supported") is False:
            result["message"] = f"This platform has no tax rules for {result['jurisdiction']}, so there is no report."
            return result
        s, say = result["summary"], markets.money_for(firm)
        if result["jurisdiction"] == "IN":
            result["message"] = (
                f"Tax Year {result['tax_year']}: {s['taxpayers']} taxpayer(s), estimated tax {say(s['estimated_tax'])}, "
                f"{say(s['exemption_unused'])} of exemption unused, {say(s['harvestable_loss'])} of harvestable "
                f"loss. Estimates for a qualified person to check.")
        else:
            result["message"] = f"{s['total_flags']} tax flag(s) for {result['household_name']}."
        return result

    async def read_licences(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import licences
        from app.models.tenant import Firm

        result = await licences.overview(self.session, await self.session.get(Firm, self.firm_id))
        if not result["applies"]:
            result["message"] = "This firm has no licences on file, so nothing here limits its agents by licence."
            return result
        names = ", ".join(l["name"] for l in result["licences"])
        result["message"] = (f"Licensed as {names}. {len(result['mandates'])} mandate(s); "
                             f"{len(result['awaiting_consent'])} proposal(s) waiting for a client's consent.")
        return result

    async def _consent_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import consent, markets
        from app.models.governance import Recommendation

        rec_id = _parse_uuid(inputs.get("recommendation_id"), "recommendation_id")
        rec = (await self.session.execute(select(Recommendation).where(
            Recommendation.id == rec_id, Recommendation.firm_id == self.firm_id))).scalar_one_or_none()
        if rec is None:
            raise ExecutorError(f"Proposal {rec_id} not found or access denied")
        firm = await self._firm()
        channel = str(inputs.get("channel") or "")
        try:
            r = await consent.prepare_answer(self.session, firm=firm, rec=rec, channel=channel, evidence_ref=inputs.get("evidence_ref"))
        except consent.ConsentError as exc:
            raise ExecutorError(str(exc))
        say = markets.money_for(firm)
        lines = [f"{str(o['side']).title()} {float(o['quantity'] or 0):g} {o['symbol']} ({say(float(o['est_value'] or 0))})"
                 for o in r["consent"].orders or []]
        given = bool(inputs.get("given"))
        return [("Proposal", rec.title), ("Mandate", rec.subject_label),
                ("Answer", "Yes: the orders are placed now" if given else "No: nothing is placed"),
                ("How", channel.replace("_", " ")), ("Evidence", (inputs.get("evidence_ref") or "").strip() or None),
                ("Orders", "; ".join(lines)), ("Simulated", "Yes, for demonstration only" if r["simulated"] else None),
                ("Note", (inputs.get("note") or "").strip() or None)]

    async def record_client_consent(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.atlas import runtime
        from app.aurea_core import consent
        from app.models.governance import Recommendation
        from app.models.identity import User
        from app.models.tenant import Firm

        if self.role not in (UserRole.ADVISER, UserRole.PARAPLANNER):
            raise ExecutorError("Recording a client's answer is for advisers and paraplanners.")
        rec_id = _parse_uuid(inputs.get("recommendation_id"), "recommendation_id")
        rec = (await self.session.execute(select(Recommendation).where(
            Recommendation.id == rec_id, Recommendation.firm_id == self.firm_id))).scalar_one_or_none()
        if rec is None:
            raise ExecutorError(f"Proposal {rec_id} not found or access denied")
        user = await self.session.get(User, self.user_id)
        try:
            rec = await runtime.client_response(
                self.session, firm=await self.session.get(Firm, self.firm_id), recommendation=rec,
                given=bool(inputs.get("given")), channel=str(inputs.get("channel") or ""),
                evidence_ref=inputs.get("evidence_ref"), note=inputs.get("note"),
                actor_label=user.email if user else "unknown")
        except (consent.ConsentError, runtime.AlreadyDecidedError) as exc:
            raise ExecutorError(str(exc))
        outcome = (rec.payload or {}).get("execution_result") or {}
        state = (rec.payload or {}).get("client_consent") or {}
        return {"recommendation_id": str(rec.id), "status": rec.status, "client_consent": state,
                "execution_result": outcome,
                "message": outcome.get("note") or f"The client's answer is on record: {state.get('status')}."}

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

    def _policy_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from datetime import date as _date

        expires = None
        if inputs.get("expires_on"):
            try:
                expires = _date.fromisoformat(str(inputs["expires_on"]))
            except ValueError:
                raise ExecutorError("expires_on must be a date, YYYY-MM-DD.")
        return {"person_id": _parse_uuid(inputs.get("person_id"), "person_id"),
                "kind": str(inputs.get("kind") or "").strip().lower(), "provider": inputs.get("provider") or "",
                "source_document": inputs.get("source_document") or "", "sum_insured": inputs.get("sum_insured"),
                "annual_benefit": inputs.get("annual_benefit"), "benefit_to_age": inputs.get("benefit_to_age"),
                "benefit_years": inputs.get("benefit_years"), "expires_on": expires}

    async def record_protection_policy(self, inputs: dict[str, Any]) -> dict[str, Any]:
        args = self._policy_args(inputs)
        result = await self._protection_write(lambda p, by: p.record_policy(
            self.session, self.firm_id, recorded_by=by, **args))
        result["message"] = f"Recorded {result['kind']} cover with {result['provider']} for {result['person']}."
        return result

    async def _protection_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """What a protection Confirm card names, refused for the reasons doing it would be refused."""
        from app.aurea_core import markets, protection

        firm = await self._firm()
        say = markets.money_for(firm)
        kinds = {"life": "Life", "tpd": "Total and permanent disablement", "income_protection": "Income protection",
                 "long_term_care": "Long-term care"}
        try:
            if tool_key == "set_care_assumptions":
                r = await protection.prepare_care(self.session, self.firm_id, annual_cost=inputs.get("annual_cost"),
                                                  years=inputs.get("years"), source_document=inputs.get("source_document") or "")
                was = r["previous"]
                return [("Cost of care", f"{say(r['annual_cost'])} a year"), ("Planned for", f"{r['years']} years"),
                        ("Each person's need", say(r["annual_cost"] * r["years"])),
                        ("Replaces", f"{say(was['annual_cost'])} a year for {was['years']} years" if was else "Nothing set"),
                        ("Document", r["source"])]
            if tool_key == "record_protection_policy":
                r = await protection.prepare_policy(self.session, self.firm_id, **self._policy_args(inputs))
                return [("Insured", r["person"].full_name), ("Cover", kinds.get(r["kind"], r["kind"])), ("Provider", r["provider"]),
                        ("Sum insured", say(r["sum_insured"]) if r["sum_insured"] else None),
                        ("Annual benefit", say(r["annual_benefit"]) if r["annual_benefit"] else None),
                        ("Benefit to age", r["benefit_to_age"]),
                        ("Paid for", f"{r['benefit_years']} years" if r["benefit_years"] else
                         ("As long as care lasts" if r["kind"] == "long_term_care" else None)),
                        ("Ends on", r["expires_on"]), ("Document", r["source"])]
            if tool_key == "end_protection_policy":
                r = await protection.prepare_end(self.session, self.firm_id, _parse_uuid(inputs.get("policy_id"), "policy_id"),
                                                 reason=inputs.get("reason") or "")
                p = r["policy"]
                amount = p.sum_insured if p.sum_insured is not None else p.annual_benefit
                return [("Insured", r["person"].full_name if r["person"] else None), ("Cover", kinds.get(p.kind, p.kind)),
                        ("Provider", p.provider), ("Amount", say(float(amount)) if amount is not None else None),
                        ("Reason", r["reason"])]
            r = await protection.prepare_income(self.session, self.firm_id, person_id=_parse_uuid(inputs.get("person_id"), "person_id"),
                                                annual_income=inputs.get("annual_income"), source_document=inputs.get("source_document") or "")
            return [("Person", r["person"].full_name), ("Income", say(r["amount"])),
                    ("Replaces", say(r["previous"]) if r["previous"] is not None else "Nothing on file"), ("Document", r["source"])]
        except protection.ProtectionError as exc:
            raise ExecutorError(str(exc))

    async def set_care_assumptions(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The firm's cost of care. A firm-wide figure, so it is a firm leader's to state, not an adviser's."""
        from app.aurea_core import markets, protection
        from app.models.identity import User

        if self.role not in (UserRole.BRANCH_LEADER, UserRole.ADMIN):
            raise ExecutorError("The firm's cost of care is set by a branch leader or an administrator.")
        user = await self.session.get(User, self.user_id)
        try:
            result = await protection.set_care(
                self.session, self.firm_id, annual_cost=inputs.get("annual_cost"), years=inputs.get("years"),
                source_document=inputs.get("source_document") or "", set_by=user.email if user else "unknown")
        except protection.ProtectionError as exc:
            raise ExecutorError(str(exc))
        say = markets.money_for(await self._firm())
        result["message"] = f"Care is now planned at {say(result['annual_cost'])} a year for {result['years']} years."
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

    async def read_risk_profiles(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import risk_profile

        if inputs.get("household_id"):
            household_id = _parse_uuid(inputs["household_id"], "household_id")
            result = await risk_profile.for_household(self.session, household_id, firm_id=self.firm_id)
            if result is None:
                raise ExecutorError(f"Household {household_id} not found or access denied")
            done = result["assessment"]
            wealth = result["wealth"]
            said = (f" What could be drawn on, less debts, covers {wealth['years_covered']} years of recorded spending." if wealth and wealth["status"] == "assessed"
                    else " Wealth against needs is not assessed: no spending is on file.")
            result["message"] = (f"{result['household']}: {result['status']}, indicated {done['indicated']}." if done and done["indicated"]
                                 else f"{result['household']}: no questionnaire is recorded here. That is all this shows. The firm may hold one elsewhere, so whether the profile on file is supported is not known: do not say it is unsupported."
                                 if result["status"] == "not_taken" else f"{result['household']}: {result['status']}.") + said
            return result
        result = await risk_profile.for_firm(self.session, self.firm_id)
        s = result["summary"]
        result["message"] = (f"{s['households']} household(s): {s['more_risk']} taking more risk than their answers "
                             f"support, {s['less_risk']} less, {s['aligned']} aligned, {s['not_taken']} not taken.")
        return result

    async def decide_risk_profile(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import risk_profile
        from app.models.identity import User

        # The gateway checks the catalogue's roles; this re-checks, as for the other record-keeping tools.
        if self.role != UserRole.ADVISER:
            raise ExecutorError("Only an adviser decides a client's risk profile.")
        mandate_id = _parse_uuid(inputs.get("mandate_id"), "mandate_id")
        user = await self.session.get(User, self.user_id)
        try:
            result = await risk_profile.decide(
                self.session, self.firm_id, mandate_id, decision=str(inputs.get("decision") or "").strip().lower(),
                reason=inputs.get("reason"), by=user.email if user else "unknown")
        except risk_profile.RiskProfileError as exc:
            raise ExecutorError(str(exc))
        result["message"] = (f"{result['mandate']} is now {result['risk_profile']} with {result['capacity_for_loss']} capacity."
                             if result["decision"] == "adopt" else f"{result['mandate']} keeps its {result['risk_profile']} profile.")
        return result

    async def _risk_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """What the risk decision's Confirm card names, refused for the reasons deciding would be refused."""
        from app.aurea_core import risk_profile

        decision = str(inputs.get("decision") or "").strip().lower()
        try:
            r = await risk_profile.prepare_decision(self.session, self.firm_id, _parse_uuid(inputs.get("mandate_id"), "mandate_id"),
                                                    decision=decision, reason=inputs.get("reason"))
        except risk_profile.RiskProfileError as exc:
            raise ExecutorError(str(exc))
        a = r["assessment"]
        on_file = f"{r['profile'] or 'None'} · {r['capacity'] or 'no capacity'}"
        return [("Mandate", r["mandate"].name), ("Decision", "Adopt the questionnaire" if decision == "adopt" else "Keep what is on file"),
                ("On file", on_file), ("Questionnaire", f"{a.indicated} · {a.capacity}"),
                ("Becomes", f"{a.indicated} · {a.capacity}" if decision == "adopt" else on_file), ("Reason", r["reason"]),
                ("Model", f"{r['model']} takes more risk than this profile, and stays until a person moves it" if r["model_fit"] == "above_profile" else None)]

    async def read_client_reviews(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import reviews

        if inputs.get("household_id"):
            household_id = _parse_uuid(inputs["household_id"], "household_id")
            result = await reviews.for_household(self.session, household_id, firm_id=self.firm_id)
            if result is None:
                raise ExecutorError(f"Household {household_id} not found or access denied")
            result["message"] = (f"{result['household']}: {result['status']}, next review due {result['next_due']}."
                                 if result["next_due"] else f"{result['household']}: no review on record.")
            return result
        result = await reviews.for_firm(self.session, self.firm_id)
        s = result["summary"]
        result["message"] = (f"{s['households']} household(s): {s['overdue']} overdue, {s['due_soon']} due within "
                             f"{result['due_soon_days']} days, {s['on_track']} on track, {s['no_record']} with no review on record.")
        return result

    @staticmethod
    def _review_day(value: Any, name: str):
        from datetime import date as _date

        if not value:
            return None
        try:
            return _date.fromisoformat(str(value)[:10])
        except ValueError:
            raise ExecutorError(f"{name} must be a date, YYYY-MM-DD.")

    def _review_record_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"held_on": self._review_day(inputs.get("held_on"), "held_on"), "covered": inputs.get("covered"),
                "notes": inputs.get("notes"),
                "meeting_id": _parse_uuid(inputs["meeting_id"], "meeting_id") if inputs.get("meeting_id") else None}

    async def _review_write(self, write, roles: set) -> dict[str, Any]:
        """A write to the review record: the role re-checked as for the other record-keeping tools, and
        a refusal handed back as the tool's error."""
        from app.aurea_core import reviews
        from app.models.identity import User

        if self.role not in roles:
            raise ExecutorError("Your role cannot change a client's review record.")
        user = await self.session.get(User, self.user_id)
        try:
            return await write(reviews, user.email if user else "unknown")
        except reviews.ReviewError as exc:
            raise ExecutorError(str(exc))

    async def record_client_review(self, inputs: dict[str, Any]) -> dict[str, Any]:
        household_id = _parse_uuid(inputs.get("household_id"), "household_id")
        args = self._review_record_args(inputs)
        result = await self._review_write(lambda r, by: r.record(
            self.session, household_id, firm_id=self.firm_id, by=by, role=str(self.role), **args),
            {UserRole.ADVISER, UserRole.PARAPLANNER})
        result["message"] = f"Recorded the review for {result['household']}. The next is due {result['next_due']}."
        return result

    async def set_review_cadence(self, inputs: dict[str, Any]) -> dict[str, Any]:
        household_id = _parse_uuid(inputs.get("household_id"), "household_id")
        result = await self._review_write(lambda r, by: r.set_cadence(
            self.session, household_id, firm_id=self.firm_id, months=inputs.get("months"), reason=inputs.get("reason"),
            by=by), {UserRole.ADVISER})
        result["message"] = f"{result['household']} is now reviewed every {result['cadence_months']} months."
        return result

    async def book_client_review(self, inputs: dict[str, Any]) -> dict[str, Any]:
        household_id = _parse_uuid(inputs.get("household_id"), "household_id")
        on = self._review_day(inputs.get("date"), "date")
        result = await self._review_write(lambda r, by: r.book(
            self.session, household_id, firm_id=self.firm_id, on=on, at=inputs.get("time"), by=by),
            {UserRole.ADVISER, UserRole.PARAPLANNER})
        result["message"] = f"Booked '{result['booked']['title']}' for {result['booked']['on']}."
        return result

    async def _review_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """What a review Confirm card names, refused for the reasons doing it would be refused."""
        from app.aurea_core import firm_time, reviews

        household_id = _parse_uuid(inputs.get("household_id"), "household_id")
        try:
            if tool_key == "record_client_review":
                r = await reviews.prepare_record(self.session, household_id, firm_id=self.firm_id,
                                                 **self._review_record_args(inputs))
                return [("Client", r["household"].name), ("Held on", firm_time.spoken(r["held_on"])),
                        ("Covered", ", ".join(t.replace("_", " ") for t in r["covered"])),
                        ("Meeting", r["meeting"].title if r["meeting"] else None), ("Notes", r["notes"])]
            if tool_key == "set_review_cadence":
                r = await reviews.prepare_cadence(self.session, household_id, firm_id=self.firm_id,
                                                  months=inputs.get("months"), reason=inputs.get("reason"))
                return [("Client", r["household"].name), ("From", f"Every {r['was']} months"),
                        ("To", f"Every {r['months']} months"), ("Reason", r["reason"])]
            r = await reviews.prepare_booking(self.session, household_id, firm_id=self.firm_id,
                                              on=self._review_day(inputs.get("date"), "date"), at=inputs.get("time"))
            return [("Client", r["household"].name), ("Meeting", r["title"]),
                    ("When", f"{firm_time.spoken(r['when'].date())}, {r['when']:%H:%M}")]
        except reviews.ReviewError as exc:
            raise ExecutorError(str(exc))

    async def read_balance_sheet(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import balance_sheet, markets

        say = markets.money_for(await self._firm())
        if inputs.get("household_id"):
            household_id = _parse_uuid(inputs["household_id"], "household_id")
            result = await balance_sheet.for_household(self.session, household_id, firm_id=self.firm_id)
            if result is None:
                raise ExecutorError(f"Household {household_id} not found or access denied")
            result["message"] = (f"{result['household']}: net worth {say(result['net_worth'])}"
                                 + (" before any debts, because none are recorded here. It is neither a floor nor a ceiling: a debt "
                                    "or an asset held elsewhere and not recorded would move it either way." if result["basis"] == "assets_only" else "."))
            return result
        result = await balance_sheet.for_firm(self.session, self.firm_id)
        s = result["summary"]
        result["message"] = (f"{s['households']} household(s): {s['with_liabilities']} with debts recorded, {s['assets_only']} assets only "
                             f"with no debts recorded here, {s['cash_flow_assessed']} with cash flow assessed.")
        return result

    def _balance_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        rate = inputs.get("interest_rate")
        try:
            rate = float(rate) / 100 if rate not in (None, "") else None
        except (TypeError, ValueError):
            raise ExecutorError("interest_rate must be a number.")
        return {"kind": str(inputs.get("kind") or "").strip().lower(), "name": inputs.get("name") or "", "amount": inputs.get("amount"),
                "as_of": self._review_day(inputs.get("as_of"), "as_of"), "source_document": inputs.get("source_document") or "",
                "person_id": _parse_uuid(inputs["person_id"], "person_id") if inputs.get("person_id") else None,
                "annual_payment": inputs.get("annual_payment"), "interest_rate": rate,
                "ends_on": self._review_day(inputs.get("ends_on"), "ends_on"), "currency": inputs.get("currency"),
                "replaces_id": _parse_uuid(inputs["replaces_id"], "replaces_id") if inputs.get("replaces_id") else None}

    async def _balance_write(self, write) -> dict[str, Any]:
        from app.aurea_core import balance_sheet
        from app.models.identity import User

        if self.role not in (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS):
            raise ExecutorError("Your role cannot change a client's balance sheet.")
        user = await self.session.get(User, self.user_id)
        try:
            return await write(balance_sheet, user.email if user else "unknown")
        except balance_sheet.BalanceSheetError as exc:
            raise ExecutorError(str(exc))

    async def record_balance_item(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import markets

        household_id = _parse_uuid(inputs.get("household_id"), "household_id")
        args = self._balance_args(inputs)
        result = await self._balance_write(lambda b, by: b.record_item(
            self.session, household_id, firm_id=self.firm_id, by=by, **args))
        result["message"] = f"Recorded {result['recorded']}. {result['household']} now reads {markets.money_for(await self._firm())(result['net_worth'])}."
        return result

    async def end_balance_item(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import markets

        item_id = _parse_uuid(inputs.get("item_id"), "item_id")
        result = await self._balance_write(lambda b, by: b.end_item(
            self.session, item_id, firm_id=self.firm_id, reason=inputs.get("reason"), by=by))
        result["message"] = f"{result['ended_item']} has ended. {result['household']} now reads {markets.money_for(await self._firm())(result['net_worth'])}."
        return result

    async def _balance_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """What a balance sheet Confirm card names, refused for the reasons doing it would be refused."""
        from app.aurea_core import balance_sheet, firm_time, markets

        say = markets.money_for(await self._firm())
        text = lambda k: k.replace("_", " ").capitalize()  # noqa: E731
        try:
            if tool_key == "end_balance_item":
                r = await balance_sheet.prepare_end(self.session, _parse_uuid(inputs.get("item_id"), "item_id"), firm_id=self.firm_id,
                                                    reason=inputs.get("reason"))
                i = r["item"]
                return [("Client", r["household"].name if r["household"] else None), ("Item", i.name), ("Type", text(i.kind)),
                        ("Amount", say(float(i.amount))), ("Reason", r["reason"])]
            household_id = _parse_uuid(inputs.get("household_id"), "household_id")
            r = await balance_sheet.prepare_item(self.session, household_id, firm_id=self.firm_id, **self._balance_args(inputs))
            now = await balance_sheet.for_household(self.session, household_id, firm_id=self.firm_id)
            old = float(r["replacing"].amount) if r["replacing"] else 0.0
            change = 0.0 if r["not_converted"] or r["side"] == "expense" else (r["amount"] - old) * (-1 if r["side"] == "liability" else 1)
            return [("Client", r["household"].name), ("Type", text(r["kind"])), ("Name", r["name"]),
                    ("Whose", r["person"].full_name if r["person"] else "Joint"),
                    ("Amount", f"{r['currency']} {r['amount']:,.2f}" if r["not_converted"] else say(r["amount"])),
                    ("Replaces", say(old) if r["replacing"] else None), ("As at", firm_time.spoken(r["as_of"])),
                    ("Paid a year", say(r["annual_payment"]) if r["annual_payment"] else None),
                    ("Interest rate", f"{r['interest_rate']:.2%}" if r["interest_rate"] is not None else None),
                    ("Ends on", firm_time.spoken(r["ends_on"]) if r["ends_on"] else None), ("Document", r["source"]),
                    ("Counted", "Not added: not converted" if r["not_converted"] else None),
                    ("Net worth", None if r["side"] == "expense" or r["not_converted"]
                     else f"{say(now['net_worth'])} becomes {say(now['net_worth'] + change)}")]
        except balance_sheet.BalanceSheetError as exc:
            raise ExecutorError(str(exc))

    async def read_product_shelf(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import shelf

        result = await shelf.for_firm(self.session, self.firm_id)
        s = result["summary"]
        result["message"] = (f"{s['approved']} approved, {s['watch']} under watch, {s['closed']} closed and {s['not_reviewed']} with no decision here. "
                             f"Approved products only is {'on' if result['approved_products_only'] else 'off'}.")
        return result

    def _decision_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        expense = inputs.get("expense_ratio_pct")
        try:
            expense = float(expense) / 100 if expense not in (None, "") else None
        except (TypeError, ValueError):
            raise ExecutorError("expense_ratio_pct must be a number.")
        return {"symbol": inputs.get("symbol"), "status": inputs.get("status"), "rationale": inputs.get("rationale"), "document": inputs.get("document"),
                "next_review_on": self._exit_day(inputs.get("next_review_on"), "next_review_on"), "minimum_class": inputs.get("minimum_class") or None,
                "family": inputs.get("family"), "share_class": inputs.get("share_class"), "expense_ratio": expense, "is_diversified": inputs.get("diversified")}

    def _investor_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"investor_class": inputs.get("investor_class"), "as_of": self._exit_day(inputs.get("as_of"), "as_of"), "document": inputs.get("document"),
                "expires_on": self._exit_day(inputs.get("expires_on"), "expires_on")}

    @staticmethod
    def _switch(inputs: dict[str, Any]) -> bool:
        return str(inputs.get("on")).strip().lower() in ("true", "1", "yes", "on")

    async def _shelf_user(self) -> str:
        from app.models.identity import User

        user = await self.session.get(User, self.user_id)
        return user.email if user else "unknown"

    async def record_product_decision(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import shelf

        try:
            row = await shelf.record_decision(self.session, self.firm_id, by=await self._shelf_user(), role=str(self.role), **self._decision_args(inputs))
            result = await shelf.for_firm(self.session, self.firm_id)
        except shelf.ShelfError as exc:
            raise ExecutorError(str(exc))
        result["message"] = f"Recorded {inputs.get('symbol')} as {row.status}."
        return result

    async def record_investor_status(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import shelf

        person_id = _parse_uuid(inputs.get("person_id") or "", "person_id")
        try:
            row = await shelf.record_certification(self.session, person_id, firm_id=self.firm_id, by=await self._shelf_user(), **self._investor_args(inputs))
        except shelf.ShelfError as exc:
            raise ExecutorError(str(exc))
        return {"person_id": str(person_id), "investor_class": row.investor_class, "as_of": row.as_of.isoformat(),
                "expires_on": row.expires_on.isoformat() if row.expires_on else None, "message": f"Recorded as {row.investor_class.replace('_', ' ')}."}

    async def set_approved_products_only(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import shelf

        try:
            result = await shelf.set_enforcement(self.session, self.firm_id, on=self._switch(inputs), by=await self._shelf_user())
        except shelf.ShelfError as exc:
            raise ExecutorError(str(exc))
        result["message"] = f"Approved products only is {'on' if result['approved_products_only'] else 'off'}."
        return result

    async def _shelf_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, markets, shelf

        say = markets.money_for(await self._firm())
        try:
            if tool_key == "record_product_decision":
                r = await shelf.prepare_decision(self.session, self.firm_id, **self._decision_args(inputs))
                inst, was = r["instrument"], r["was"]
                effect = {"closed": "No new purchases, for anyone", "watch": "Purchases go ahead, and are named", "approved": "May be bought"}[r["status"]]
                return [("Product", f"{inst.symbol} · {inst.name}"), ("Status", f"{was.status.capitalize()} becomes {r['status'].capitalize()}" if was else r["status"].capitalize()),
                        ("Effect", effect), ("Held", f"{r['holdings']} holding(s), {say(r['value_held'])}" if r["holdings"] else None),
                        ("Investors", r["minimum"].replace("_", " ").capitalize() + " only" if r["minimum"] else None),
                        ("Share class", " · ".join(x for x in (r["family"], r["share_class"]) if x) or None),
                        ("Expense ratio", f"{r['expense']:.2%}" if r["expense"] is not None else None),
                        ("Position limits", "Not read against this fund" if r["diversified"] else None), ("Reason", r["rationale"]), ("Document", r["document"]),
                        ("Next review", firm_time.spoken(r["next"]) if r["next"] else None)]
            if tool_key == "record_investor_status":
                r = await shelf.prepare_certification(self.session, _parse_uuid(inputs.get("person_id") or "", "person_id"), firm_id=self.firm_id, **self._investor_args(inputs))
                return [("Person", r["person"].full_name), ("Class", r["investor_class"].replace("_", " ").capitalize()),
                        ("Replaces", r["was"].investor_class.replace("_", " ") if r["was"] else "Nothing on file"), ("As at", firm_time.spoken(r["day"])),
                        ("Runs out", firm_time.spoken(r["expires"]) if r["expires"] else None), ("Document", r["document"])]
            r = await shelf.prepare_enforcement(self.session, self.firm_id, on=self._switch(inputs))
            return [("Approved products only", "Off becomes on" if r["on"] else "On becomes off"), ("On the shelf", str(r["approved"])),
                    ("Held with no decision", ", ".join(r["unreviewed_held"][:12]) or "None"),
                    ("Their value", say(r["value_unreviewed"]) if r["unreviewed_held"] else None),
                    ("Effect", "Those stay and may be sold, and cannot be added to" if r["on"] else "A product with no decision may be bought")]
        except shelf.ShelfError as exc:
            raise ExecutorError(str(exc))

    async def read_equity_comp(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import equity_comp, markets

        say = markets.money_for(await self._firm())
        if inputs.get("household_id"):
            household_id = _parse_uuid(inputs["household_id"], "household_id")
            result = await equity_comp.for_household(self.session, household_id, firm_id=self.firm_id)
            if result is None:
                raise ExecutorError(f"Household {household_id} not found or access denied")
            result["message"] = (f"{result['household']}: {len(result['grants'])} grant(s), {say(result['value_vested'])} vested and "
                                 f"{say(result['value_unvested'])} unvested, {sum(1 for e in result['exposures'] if e['concentrated'])} concentrated position(s).")
            return result
        result = await equity_comp.for_firm(self.session, self.firm_id)
        s = result["summary"]
        result["message"] = f"{s['with_grants']} household(s) with grants on file, {s['concentrated']} concentrated position(s), {s['no_plan']} with no plan."
        return result

    def _grant_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"kind": inputs.get("kind"), "employer": inputs.get("employer"), "units": inputs.get("units"), "grant_date": self._exit_day(inputs.get("grant_date"), "grant_date"),
                "source_document": inputs.get("source_document"), "symbol": inputs.get("symbol"), "strike": inputs.get("strike"), "vesting": inputs.get("vesting"),
                "expires_on": self._exit_day(inputs.get("expires_on"), "expires_on")}

    def _plan_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        target = inputs.get("target_weight_pct")
        try:
            target = float(target) / 100
        except (TypeError, ValueError):
            raise ExecutorError("target_weight_pct must be a number.")
        return {"symbol": inputs.get("symbol"), "method": inputs.get("method"), "target_weight": target, "target_date": self._exit_day(inputs.get("target_date"), "target_date"),
                "rationale": inputs.get("rationale"), "source_document": inputs.get("source_document")}

    async def _equity_write(self, write) -> dict[str, Any]:
        from app.aurea_core import equity_comp

        try:
            row = await write(equity_comp, await self._shelf_user())
        except equity_comp.EquityCompError as exc:
            raise ExecutorError(str(exc))
        return await equity_comp.for_household(self.session, row.household_id, firm_id=self.firm_id)

    async def record_equity_grant(self, inputs: dict[str, Any]) -> dict[str, Any]:
        person_id = _parse_uuid(inputs.get("person_id") or "", "person_id")
        args = self._grant_args(inputs)
        result = await self._equity_write(lambda e, by: e.record_grant(self.session, person_id, firm_id=self.firm_id, by=by, **args))
        result["message"] = f"Recorded. {result['household']} has {len(result['grants'])} grant(s) on file."
        return result

    async def end_equity_grant(self, inputs: dict[str, Any]) -> dict[str, Any]:
        grant_id = _parse_uuid(inputs.get("grant_id") or "", "grant_id")
        result = await self._equity_write(lambda e, by: e.end_grant(self.session, grant_id, firm_id=self.firm_id, by=by, reason=inputs.get("reason")))
        result["message"] = f"Ended. {result['household']} has {len(result['grants'])} grant(s) on file."
        return result

    async def record_concentration_plan(self, inputs: dict[str, Any]) -> dict[str, Any]:
        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")
        args = self._plan_args(inputs)
        result = await self._equity_write(lambda e, by: e.record_plan(self.session, household_id, firm_id=self.firm_id, by=by, **args))
        result["message"] = f"The plan for {args['symbol']} is on file. No order has been placed."
        return result

    async def _equity_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import equity_comp, firm_time, markets

        say = markets.money_for(await self._firm())
        try:
            if tool_key == "record_equity_grant":
                r = await equity_comp.prepare_grant(self.session, _parse_uuid(inputs.get("person_id") or "", "person_id"), firm_id=self.firm_id, **self._grant_args(inputs))
                vests = r["vesting"]
                return [("Person", r["person"].full_name), ("Grant", f"{r['kind'].upper()} · {r['employer']}"), ("Symbol", r["symbol"]), ("Units", f"{r['units']:,.0f}"),
                        ("Strike", say(r["strike"]) if r["strike"] else None), ("Granted", firm_time.spoken(r["grant_date"])),
                        ("Vesting", f"{len(vests)} dates, {vests[0]['on']} to {vests[-1]['on']}" if vests else "No schedule given, so all counts as vested"),
                        ("Expires", firm_time.spoken(r["expires_on"]) if r["expires_on"] else None), ("Document", r["document"])]
            if tool_key == "end_equity_grant":
                r = await equity_comp.prepare_end(self.session, _parse_uuid(inputs.get("grant_id") or "", "grant_id"), firm_id=self.firm_id, reason=inputs.get("reason"))
                g = r["grant"]
                return [("Person", r["person"].full_name if r["person"] else None), ("Grant", f"{g.kind.upper()} · {g.employer}"), ("Units", f"{float(g.units):,.0f}"), ("Reason", r["reason"])]
            r = await equity_comp.prepare_plan(self.session, _parse_uuid(inputs.get("household_id") or "", "household_id"), firm_id=self.firm_id, **self._plan_args(inputs))
            now = r["now"]
            return [("Client", r["household"].name), ("Position", r["symbol"]), ("Now", f"{now['weight']:.1%} of the book" if now else "Not above the line"),
                    ("Method", r["method"].replace("_", " ")), ("Down to", f"{r['target']:.1%}"), ("By", firm_time.spoken(r["day"])), ("Why", r["rationale"]),
                    ("Agreed in", r["document"]), ("Orders", "None placed")]
        except equity_comp.EquityCompError as exc:
            raise ExecutorError(str(exc))

    async def read_financial_plan(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import financial_plan, markets

        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")
        result = await financial_plan.for_household(self.session, household_id, firm_id=self.firm_id)
        if result is None:
            raise ExecutorError(f"Household {household_id} not found or access denied")
        say = markets.money_for(await self._firm())
        r = result["retirement"]
        result["message"] = (f"{result['household']}: {r['success_probability']:.0%} probability of success, sustainable income {say(r['sustainable_income'])} a year. "
                             "These are estimates from what is on file." if r else f"{result['household']}: no retirement projection.")
        return result

    def _stream_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        share = inputs.get("joint_survivor_share_pct")
        try:
            share = float(share) / 100 if share not in (None, "") else None
        except (TypeError, ValueError):
            raise ExecutorError("joint_survivor_share_pct must be a number.")
        return {"kind": inputs.get("kind"), "name": inputs.get("name"), "annual_amount": inputs.get("annual_amount"), "start_age": inputs.get("start_age"),
                "source_document": inputs.get("source_document"), "inflation_linked": str(inputs.get("inflation_linked")).lower() == "true", "lump_sum": inputs.get("lump_sum"),
                "joint_annual": inputs.get("joint_annual"), "joint_survivor_share": share}

    def _education_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {k: inputs.get(k) for k in ("student", "start_year", "years", "annual_cost", "cost_source")}

    async def _plan_write(self, write) -> dict[str, Any]:
        from app.aurea_core import financial_plan

        try:
            row = await write(financial_plan, await self._shelf_user())
        except financial_plan.PlanError as exc:
            raise ExecutorError(str(exc))
        return await financial_plan.for_household(self.session, row.household_id, firm_id=self.firm_id)

    async def record_income_stream(self, inputs: dict[str, Any]) -> dict[str, Any]:
        person_id = _parse_uuid(inputs.get("person_id") or "", "person_id")
        args = self._stream_args(inputs)
        result = await self._plan_write(lambda e, by: e.record_stream(self.session, person_id, firm_id=self.firm_id, by=by, **args))
        result["message"] = f"Recorded. {result['household']} has {len(result['streams'])} income stream(s) on file."
        return result

    async def end_income_stream(self, inputs: dict[str, Any]) -> dict[str, Any]:
        stream_id = _parse_uuid(inputs.get("stream_id") or "", "stream_id")
        result = await self._plan_write(lambda e, by: e.end_stream(self.session, stream_id, firm_id=self.firm_id, by=by, reason=inputs.get("reason")))
        result["message"] = f"Ended. {result['household']} has {len(result['streams'])} income stream(s) on file."
        return result

    async def record_education_goal(self, inputs: dict[str, Any]) -> dict[str, Any]:
        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")
        args = self._education_args(inputs)
        result = await self._plan_write(lambda e, by: e.record_education(self.session, household_id, firm_id=self.firm_id, by=by, **args))
        result["message"] = f"Recorded for {args['student']}."
        return result

    async def _plan_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import financial_plan, markets

        say = markets.money_for(await self._firm())
        try:
            if tool_key == "record_income_stream":
                r = await financial_plan.prepare_stream(self.session, _parse_uuid(inputs.get("person_id") or "", "person_id"), firm_id=self.firm_id, **self._stream_args(inputs))
                social = r["kind"] == "social_security"
                return [("Person", r["person"].full_name), ("Income", f"{r['kind'].replace('_', ' ').capitalize()} · {r['name']}"),
                        ("At full retirement age" if social else "A year", say(r["amount"])), ("Claiming at" if social else "From age", str(r["age"])),
                        ("Rises with inflation", "Yes" if r["inflation_linked"] else None), ("Lump sum", say(r["lump_sum"]) if r["lump_sum"] else None),
                        ("Joint-life", say(r["joint_annual"]) if r["joint_annual"] else None), ("Survivor keeps", f"{r['survivor_share']:.0%}" if r["survivor_share"] else None),
                        ("Document", r["document"])]
            if tool_key == "end_income_stream":
                r = await financial_plan.prepare_end(self.session, _parse_uuid(inputs.get("stream_id") or "", "stream_id"), firm_id=self.firm_id, reason=inputs.get("reason"))
                g = r["stream"]
                return [("Person", r["person"].full_name if r["person"] else None), ("Income", g.name), ("A year", say(float(g.annual_amount))), ("Reason", r["reason"])]
            r = await financial_plan.prepare_education(self.session, _parse_uuid(inputs.get("household_id") or "", "household_id"), firm_id=self.firm_id, **self._education_args(inputs))
            return [("Client", r["household"].name), ("For", r["student"]), ("Starts", str(r["start_year"])), ("Years", str(r["years"])), ("A year, today", say(r["annual_cost"])), ("Source", r["source"])]
        except financial_plan.PlanError as exc:
            raise ExecutorError(str(exc))

    async def read_client_file(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import client_file

        if inputs.get("household_id"):
            household_id = _parse_uuid(inputs["household_id"], "household_id")
            result = await client_file.for_household(self.session, household_id, firm_id=self.firm_id)
            if result is None:
                raise ExecutorError(f"Household {household_id} not found or access denied")
            result["message"] = f"{result['household']}: {len(result['events'])} life event(s), {len(result['designation_issues'])} designation issue(s). Not on file does not mean none exists."
            return result
        result = await client_file.for_firm(self.session, self.firm_id)
        s = result["summary"]
        result["message"] = f"{s['with_no_trusted_contact']} of {s['households']} household(s) have someone with no trusted contact on file, and {s['documents_out_of_date']} estate document(s) are out of date."
        return result

    def _contact_file_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {k: inputs.get(k) for k in ("name", "relationship", "source_document", "phone", "email")}

    def _estate_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"kind": inputs.get("kind"), "signed_on": self._exit_day(inputs.get("signed_on"), "signed_on"), "source_document": inputs.get("source_document"), "held_by": inputs.get("held_by")}

    def _event_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"kind": inputs.get("kind"), "happened_on": self._exit_day(inputs.get("happened_on"), "happened_on"), "source_document": inputs.get("source_document"),
                "person_id": _parse_uuid(inputs["person_id"], "person_id") if inputs.get("person_id") else None, "other_party": inputs.get("other_party"), "note": inputs.get("note")}

    async def _file_write(self, write, household_of) -> dict[str, Any]:
        from app.aurea_core import client_file

        try:
            row = await write(client_file, await self._shelf_user())
        except client_file.ClientFileError as exc:
            raise ExecutorError(str(exc))
        return await client_file.for_household(self.session, await household_of(row), firm_id=self.firm_id)

    async def _home_of(self, row) -> uuid.UUID:
        from app.models.graph import Person

        return (await self.session.get(Person, row.person_id)).household_id

    async def record_trusted_contact(self, inputs: dict[str, Any]) -> dict[str, Any]:
        person_id = _parse_uuid(inputs.get("person_id") or "", "person_id")
        args = self._contact_file_args(inputs)
        result = await self._file_write(lambda e, by: e.record_contact(self.session, person_id, firm_id=self.firm_id, by=by, **args), self._home_of)
        result["message"] = f"Recorded {args['name']} as a trusted contact. They hold no authority to act."
        return result

    async def record_estate_document(self, inputs: dict[str, Any]) -> dict[str, Any]:
        person_id = _parse_uuid(inputs.get("person_id") or "", "person_id")
        args = self._estate_args(inputs)
        result = await self._file_write(lambda e, by: e.record_estate_document(self.session, person_id, firm_id=self.firm_id, by=by, **args), self._home_of)
        result["message"] = f"Recorded that a {str(args['kind']).replace('_', ' ')} exists."
        return result

    async def record_life_event(self, inputs: dict[str, Any]) -> dict[str, Any]:
        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")
        args = self._event_args(inputs)

        async def same(_row):
            return household_id

        result = await self._file_write(lambda e, by: e.record_event(self.session, household_id, firm_id=self.firm_id, by=by, by_user=self.user_id, **args), same)
        result["message"] = f"Recorded, and a review task is open. It touches: {', '.join(result['events'][0]['touches'])}."
        return result

    async def _file_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import client_file, firm_time

        try:
            if tool_key == "record_trusted_contact":
                r = await client_file.prepare_contact(self.session, _parse_uuid(inputs.get("person_id") or "", "person_id"), firm_id=self.firm_id, **self._contact_file_args(inputs))
                return [("Client", r["person"].full_name), ("Contact", r["name"]), ("Relationship", r["relationship"]), ("Phone", r["phone"]), ("Email", r["email"]),
                        ("Replaces", r["was"].name if r["was"] else None), ("Authority", "None: they may be called, not instructed"), ("Document", r["document"])]
            if tool_key == "record_estate_document":
                r = await client_file.prepare_estate_document(self.session, _parse_uuid(inputs.get("person_id") or "", "person_id"), firm_id=self.firm_id, **self._estate_args(inputs))
                return [("Client", r["person"].full_name), ("Document", r["kind"].replace("_", " ").capitalize()), ("Signed", firm_time.spoken(r["signed_on"])), ("Held by", r["held_by"]), ("Known from", r["document"])]
            r = await client_file.prepare_event(self.session, _parse_uuid(inputs.get("household_id") or "", "household_id"), firm_id=self.firm_id, **self._event_args(inputs))
            return [("Client", r["household"].name), ("Event", r["kind"].replace("_", " ").capitalize()), ("Who", r["person"].full_name if r["person"] else None), ("On", firm_time.spoken(r["day"])),
                    ("Other party", r["other"]), ("Note", r["note"]), ("Touches", ", ".join(t.replace("_", " ") for t in r["touches"])), ("Then", "A review task opens"), ("Document", r["document"])]
        except client_file.ClientFileError as exc:
            raise ExecutorError(str(exc))

    async def read_cash_movements(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import cash_movement, markets

        household_id = _parse_uuid(inputs["household_id"], "household_id") if inputs.get("household_id") else None
        result = await cash_movement.for_firm(self.session, self.firm_id, household_id=household_id)
        s = result["summary"]
        say = markets.money_for(await self._firm())
        result["message"] = f"{s['open']} open request(s) for {say(s['value_open'])}, {s['short']} short of cash, {s['standing']} standing instruction(s), {say(s['rmd_left'])} of RMDs left this year."
        return result

    async def _cash_write(self, write, message: str) -> dict[str, Any]:
        from app.aurea_core import cash_movement

        try:
            await write(cash_movement, await self._shelf_user())
        except cash_movement.CashMovementError as exc:
            raise ExecutorError(str(exc))
        result = await cash_movement.for_firm(self.session, self.firm_id)
        result["message"] = message
        return result

    def _request_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"kind": inputs.get("kind"), "amount": inputs.get("amount"), "payee": inputs.get("payee"), "reason": inputs.get("reason"), "third_party": str(inputs.get("third_party")).lower() == "true"}

    def _paid_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"paid_on": self._exit_day(inputs.get("paid_on"), "paid_on"), "source_document": inputs.get("source_document"), "callback_number": inputs.get("callback_number")}

    def _standing_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"payee": inputs.get("payee"), "amount": inputs.get("amount"), "frequency": inputs.get("frequency"), "first_due": self._exit_day(inputs.get("first_due"), "first_due"),
                "source_document": inputs.get("source_document"), "third_party": str(inputs.get("third_party")).lower() == "true"}

    def _phase_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"amount": inputs.get("amount"), "tranches": inputs.get("tranches"), "every_months": inputs.get("every_months"), "reason": inputs.get("reason"), "start_on": self._exit_day(inputs.get("start_on"), "start_on")}

    async def request_cash_movement(self, inputs: dict[str, Any]) -> dict[str, Any]:
        account_id = _parse_uuid(inputs.get("account_id") or "", "account_id")
        args = self._request_args(inputs)
        return await self._cash_write(lambda e, by: e.request(self.session, account_id, firm_id=self.firm_id, by=by, **args), "Requested. The cash is held back, and nothing has been paid.")

    async def record_cash_paid(self, inputs: dict[str, Any]) -> dict[str, Any]:
        request_id = _parse_uuid(inputs.get("request_id") or "", "request_id")
        args = self._paid_args(inputs)
        return await self._cash_write(lambda e, by: e.record_paid(self.session, request_id, firm_id=self.firm_id, by=by, **args), "Recorded as paid. The cash has come off the book.")

    async def cancel_cash_request(self, inputs: dict[str, Any]) -> dict[str, Any]:
        request_id = _parse_uuid(inputs.get("request_id") or "", "request_id")
        return await self._cash_write(lambda e, by: e.cancel(self.session, request_id, firm_id=self.firm_id, by=by, reason=inputs.get("reason")), "Cancelled. The cash is released.")

    async def record_standing_instruction(self, inputs: dict[str, Any]) -> dict[str, Any]:
        account_id = _parse_uuid(inputs.get("account_id") or "", "account_id")
        args = self._standing_args(inputs)
        return await self._cash_write(lambda e, by: e.record_standing(self.session, account_id, firm_id=self.firm_id, by=by, **args), "Recorded.")

    async def end_standing_instruction(self, inputs: dict[str, Any]) -> dict[str, Any]:
        standing_id = _parse_uuid(inputs.get("standing_id") or "", "standing_id")
        return await self._cash_write(lambda e, by: e.end_standing(self.session, standing_id, firm_id=self.firm_id, by=by), "Ended.")

    async def start_phase_in(self, inputs: dict[str, Any]) -> dict[str, Any]:
        mandate_id = _parse_uuid(inputs.get("mandate_id") or "", "mandate_id")
        args = self._phase_args(inputs)
        return await self._cash_write(lambda e, by: e.start_phase_in(self.session, mandate_id, firm_id=self.firm_id, by=by, **args), "Started. The stages not yet due are held back from purchases.")

    async def end_phase_in(self, inputs: dict[str, Any]) -> dict[str, Any]:
        plan_id = _parse_uuid(inputs.get("plan_id") or "", "plan_id")
        return await self._cash_write(lambda e, by: e.end_phase_in(self.session, plan_id, firm_id=self.firm_id, by=by), "Ended. What was held back is released.")

    async def _cash_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import cash_movement as e, firm_time, markets

        say = markets.money_for(await self._firm())
        try:
            if tool_key == "request_cash_movement":
                r = await e.prepare_request(self.session, _parse_uuid(inputs.get("account_id") or "", "account_id"), firm_id=self.firm_id, **self._request_args(inputs))
                return [("Account", r["account"].name), ("Kind", r["kind"].upper() if r["kind"] == "rmd" else r["kind"].capitalize()), ("Amount", say(r["amount"])), ("To", r["payee"]), ("Third party", "Yes, a callback is needed" if r["third_party"] else None),
                        ("For", r["reason"]), ("Cash there", say(r["cash"])), ("Already asked for", say(r["already_open"]) if r["already_open"] else None), ("Short by", say(r["short_by"]) if r["short_by"] else None), ("Then", "Held back from purchases, nothing paid")]
            if tool_key == "record_cash_paid":
                who = await self._shelf_user()
                r = await e.prepare_paid(self.session, _parse_uuid(inputs.get("request_id") or "", "request_id"), firm_id=self.firm_id, by=who, **self._paid_args(inputs))
                q = r["request"]
                return [("Account", r["account"].name), ("Amount", say(float(q.amount))), ("To", q.payee), ("Paid", firm_time.spoken(r["day"])), ("Callback", r["callback"]), ("Cash then", say(float(r["account"].cash_balance or 0) - float(q.amount))), ("Document", r["document"])]
            if tool_key == "cancel_cash_request":
                r = await e.prepare_cancel(self.session, _parse_uuid(inputs.get("request_id") or "", "request_id"), firm_id=self.firm_id, reason=inputs.get("reason"))
                return [("Account", r["account"].name), ("Amount", say(float(r["request"].amount))), ("To", r["request"].payee), ("Reason", r["reason"]), ("Then", "The cash is released")]
            if tool_key == "record_standing_instruction":
                r = await e.prepare_standing(self.session, _parse_uuid(inputs.get("account_id") or "", "account_id"), firm_id=self.firm_id, **self._standing_args(inputs))
                return [("Account", r["account"].name), ("To", r["payee"]), ("Amount", f"{say(r['amount'])} {r['frequency']}"), ("First due", firm_time.spoken(r["first_due"])), ("Third party", "Yes" if r["third_party"] else None), ("Document", r["document"])]
            if tool_key == "end_standing_instruction":
                r = await e.prepare_end_standing(self.session, _parse_uuid(inputs.get("standing_id") or "", "standing_id"), firm_id=self.firm_id)
                return [("Account", r["account"].name if r["account"] else None), ("To", r["standing"].payee), ("Amount", f"{say(float(r['standing'].amount))} {r['standing'].frequency}")]
            if tool_key == "start_phase_in":
                r = await e.prepare_phase_in(self.session, _parse_uuid(inputs.get("mandate_id") or "", "mandate_id"), firm_id=self.firm_id, **self._phase_args(inputs))
                return [("Mandate", r["mandate"].name), ("Amount", say(r["amount"])), ("Stages", f"{r['tranches']} of {say(r['each'])}, every {r['every']} month(s)"), ("First", firm_time.spoken(r["day"])), ("Last", firm_time.spoken(r["last_on"])), ("Why", r["reason"]), ("Orders", "None placed")]
            r = await e.prepare_end_phase_in(self.session, _parse_uuid(inputs.get("plan_id") or "", "plan_id"), firm_id=self.firm_id)
            return [("Mandate", r["mandate"].name if r["mandate"] else None), ("Released", say(r["released"]))]
        except e.CashMovementError as exc:
            raise ExecutorError(str(exc))

    async def read_trades(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import blotter

        household_id = _parse_uuid(inputs["household_id"], "household_id") if inputs.get("household_id") else None
        result = await blotter.for_firm(self.session, self.firm_id, household_id=household_id, days=self._limit(inputs.get("days"), blotter.DEFAULT_DAYS, 365))
        s = result["summary"]
        result["message"] = (f"{s['filled']} filled in the last {result['days']} days, {s['unsettled']} unsettled, {s['direct']} from direct orders, {s['rejected']} rejected."
                             + (" Paper venue: no market was reached." if result["paper_venue"] else ""))
        return result

    async def read_tax_documents(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import tax_documents

        try:
            year = int(inputs["tax_year"]) if inputs.get("tax_year") else None
        except (TypeError, ValueError):
            raise ExecutorError("tax_year must be a year.")
        if inputs.get("household_id"):
            household_id = _parse_uuid(inputs["household_id"], "household_id")
            result = await tax_documents.for_household(self.session, household_id, firm_id=self.firm_id, tax_year=year)
            if result is None:
                raise ExecutorError(f"Household {household_id} not found or access denied")
            result["message"] = f"{result['household']}, {result['tax_year_label']}: {result['received']} received, {result['awaited']} awaited, {result['overdue']} overdue. This is a summary from the book, not the official form."
            return result
        result = await tax_documents.for_firm(self.session, self.firm_id, tax_year=year)
        s = result["summary"]
        result["message"] = f"{result['tax_year_label']}: {s['expected']} expected, {s['received']} received, {s['awaited']} awaited, {s['overdue']} overdue."
        return result

    def _tax_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {k: inputs.get(k) for k in ("kind", "tax_year", "issuer", "source_document")}

    async def record_tax_document(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import tax_documents

        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")
        try:
            row = await tax_documents.record_received(self.session, household_id, firm_id=self.firm_id, by=await self._shelf_user(), **self._tax_args(inputs))
        except tax_documents.TaxDocumentError as exc:
            raise ExecutorError(str(exc))
        result = await tax_documents.for_household(self.session, household_id, firm_id=self.firm_id, tax_year=row.tax_year)
        result["message"] = f"Recorded as received. {result['awaited']} awaited, {result['overdue']} overdue."
        return result

    async def _tax_document_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import tax_documents

        try:
            r = await tax_documents.prepare_received(self.session, _parse_uuid(inputs.get("household_id") or "", "household_id"), firm_id=self.firm_id, **self._tax_args(inputs))
        except tax_documents.TaxDocumentError as exc:
            raise ExecutorError(str(exc))
        return [("Client", r["household"].name), ("Document", r["label"]), ("Year", r["year_label"]), ("For", r["issuer"]), ("Filed at", r["document"]), ("Was expected", "No, the book did not call for it" if not r["expected"] else None)]

    async def read_onboarding_queues(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.api.onboarding import states_overview

        result = await states_overview(self.session, await self._firm())
        s, c = result["summary"], result["clocks"]
        stuck = ", ".join(f"{q['label'].lower()} {len(q['items'])}" for q in result["queues"].values() if q["items"]) or "none"
        result["message"] = (f"{s['accounts']} account(s) in onboarding, {s['overdue']} past their SLA. Exceptions: {stuck}. "
                             f"Median clocks: paperwork {c['paperwork']['p50']}, money {c['money']['p50']}, invested {c['invested']['p50']} day(s).")
        return result

    def _risk_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        facts = {k: inputs.get(k) for k in ("residence_country", "citizenship", "source_of_funds_country", "wealth_source", "channel",
                                             "wires_per_year", "expected_annual_inflow", "third_party_payments", "layered") if inputs.get(k) not in (None, "")}
        return {"facts": facts, "band": inputs.get("band"), "reason": inputs.get("reason")}

    def _consent_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"case_id": _parse_uuid(inputs["case_id"], "case_id") if inputs.get("case_id") else None,
                "household_id": _parse_uuid(inputs["household_id"], "household_id") if inputs.get("household_id") else None,
                "kind": inputs.get("kind"), "status": inputs.get("status"), "given_by": inputs.get("given_by"), "method": inputs.get("method"),
                "evidence": inputs.get("evidence"), "given_on": self._exit_day(inputs.get("given_on"), "given_on")}

    async def read_consents(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import consents

        try:
            result = await consents.registry(self.session, self.firm_id,
                                             household_id=_parse_uuid(inputs["household_id"], "household_id") if inputs.get("household_id") else None,
                                             case_id=_parse_uuid(inputs["case_id"], "case_id") if inputs.get("case_id") else None)
        except consents.ConsentError as exc:
            raise ExecutorError(str(exc))
        stands = result["standing"] or {}
        result["message"] = ((f"{sum(1 for v in stands.values() if v['status'] == 'granted')} consent(s) stand; " if result["standing"] is not None else
                              f"{len(result['records'])} consent record(s) on file; ") + f"{len(result['redeliveries_due'])} disclosure(s) due again.")
        return result

    async def record_consent(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import consents

        try:
            result = await consents.record(self.session, self.firm_id, by=await self._shelf_user(), **self._consent_args(inputs))
        except consents.ConsentError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Recorded."
        return result

    async def _consent_registry_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import consents, firm_time

        try:
            r = await consents.prepare(self.session, self.firm_id, **self._consent_args(inputs))
        except consents.ConsentError as exc:
            raise ExecutorError(str(exc))
        effect = {"e_delivery": "Disclosures may be delivered by email or portal", "fee_debit": "Fees may be debited from the account"}.get(r["kind"])
        return [("Client", r["whose"]), ("Consent", consents.LABELS[r["kind"]]), ("Status", r["status"].capitalize()), ("Given by", r["given_by"]),
                ("Given on", firm_time.spoken(r["given_on"])), ("How", r["method"].replace("_", " ")), ("Evidence", r["evidence"]),
                ("Effect", (effect if r["status"] == "granted" else "No longer stands") if effect else None)]

    def _case_registration_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"case_id": _parse_uuid(inputs.get("case_id") or "", "case_id"),
                "fields": {f: inputs.get(f) for f in ("state", "married", "tod", "funding", "contribution_tax_year", "plan_tax_year", "decedent_date_of_death", "year_of_death_rmd", "trust_date", "tax_id_type", "tax_id", "termination_age", "successor", "signed_by", "signed_on")},
                "documents": [d for d in (inputs.get("documents") or "").split(",") if d.strip()], "evidence": inputs.get("evidence")}

    async def read_document_matrix(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import document_matrix
        from app.models.onboarding import OnboardingCase
        from app.models.tenant import Firm

        firm = await self.session.get(Firm, self.firm_id)
        result = document_matrix.matrix(firm)
        if inputs.get("case_id"):
            case = await self.session.get(OnboardingCase, _parse_uuid(inputs["case_id"], "case_id"))
            if case is None or case.firm_id != self.firm_id:
                raise ExecutorError("Case not found.")
            result["check"] = {**await document_matrix.check_case(self.session, case, firm), "case_id": str(case.id), "case": case.prospect_name}
            c = result["check"]
            result["message"] = "In good order." if c["in_good_order"] else f"{len(c['issues'])} issue(s) before submission."
        else:
            result["message"] = f"{len(result['registrations'])} registrations; {len(result['custodians'])} custodian(s) with their own forms."
        return result

    async def record_registration_detail(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import document_matrix
        from app.models.tenant import Firm

        firm = await self.session.get(Firm, self.firm_id)
        try:
            check = await document_matrix.record_detail(self.session, firm, by=await self._shelf_user(), **self._case_registration_args(inputs))
        except document_matrix.MatrixError as exc:
            raise ExecutorError(str(exc))
        result = {**document_matrix.matrix(firm), "check": {**check, "case_id": inputs.get("case_id")}}
        result["message"] = "Recorded. " + ("In good order." if check["in_good_order"] else f"{len(check['issues'])} issue(s) remain.")
        return result

    async def _case_registration_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        import types
        from datetime import date

        from app.aurea_core import document_matrix, firm_time
        from app.models.onboarding import OnboardingDocument, OnboardingParty
        from app.models.tenant import Firm

        try:
            r = await document_matrix.prepare_detail(self.session, self.firm_id, **self._case_registration_args(inputs))
        except document_matrix.MatrixError as exc:
            raise ExecutorError(str(exc))
        case, firm = r["case"], await self.session.get(Firm, self.firm_id)
        reg = {**((case.intake or {}).get("registration") or {}), **r["fields"]}
        reg["documents"] = {**(reg.get("documents") or {}), **{k: {} for k in r["documents"]}}
        after = types.SimpleNamespace(registration_type=case.registration_type, custodian_name=case.custodian_name, intake={**(case.intake or {}), "registration": reg})
        parties = (await self.session.execute(select(OnboardingParty).where(OnboardingParty.case_id == case.id))).scalars().all()
        docs = (await self.session.execute(select(OnboardingDocument).where(OnboardingDocument.case_id == case.id))).scalars().all()
        check = document_matrix.check(after, list(parties), list(docs), firm=firm, today=firm_time.today(firm))
        def show(f, v):
            if isinstance(v, list):
                return ", ".join(v)
            if isinstance(v, bool):
                return "Yes" if v else "No"
            return firm_time.spoken(date.fromisoformat(v)) if f in ("trust_date", "decedent_date_of_death", "signed_on") else v

        shown = [(document_matrix.FIELD_LABELS[f], show(f, v)) for f, v in r["fields"].items()]
        return [("Case", case.prospect_name), ("Registration", (case.registration_type or "individual").replace("_", " ")), *shown,
                ("Documents received", ", ".join(document_matrix.DOCUMENTS[k] for k in r["documents"]) or None), ("Evidence", r["evidence"]),
                ("After this", "In good order" if check["in_good_order"] else f"{sum(1 for i in check['issues'] if i['severity'] in ('critical', 'high'))} issue(s) still block submission")]

    async def set_custodian_documents(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import document_matrix

        try:
            result = await document_matrix.record_custodian(self.session, self.firm_id, by=await self._shelf_user(), custodian=inputs.get("custodian"),
                                                            documents=[d for d in (inputs.get("documents") or "").split(",") if d.strip()], source=inputs.get("source"))
        except document_matrix.MatrixError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Set."
        return result

    async def _custodian_documents_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import document_matrix

        try:
            r = await document_matrix.prepare_custodian(self.session, self.firm_id, custodian=inputs.get("custodian"),
                                                        documents=[d for d in (inputs.get("documents") or "").split(",") if d.strip()], source=inputs.get("source"))
        except document_matrix.MatrixError as exc:
            raise ExecutorError(str(exc))
        label = lambda ks: ", ".join(document_matrix.DOCUMENTS.get(k, k) for k in ks) or "None"  # noqa: E731
        return [("Custodian", r["custodian"]), ("Forms required", label(r["documents"])), ("Was", label(r["was"])), ("Source", r["source"]),
                ("Effect", "Every open case at this custodian needs these before submission")]

    def _transfer_event_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"transfer_id": _parse_uuid(inputs.get("transfer_id") or "", "transfer_id"), "kind": inputs.get("kind"),
                "on": self._exit_day(inputs.get("on"), "on"), "evidence": inputs.get("evidence"), "code": inputs.get("code"),
                "amount": inputs.get("amount"), "lines": inputs.get("lines"), "carrier": inputs.get("carrier"), "contract": inputs.get("contract"),
                "note": inputs.get("note")}

    def _rollover_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"case_id": _parse_uuid(inputs.get("case_id") or "", "case_id"), "source": inputs.get("source"),
                "facts": {k: inputs.get(k) for k in ("plan_name", "plan_type", "balance", "plan_cost_bps", "ira_cost_bps", "employer_stock", "employer_stock_value", "employer_stock_basis", "loan_balance", "separation_year", "adviser_reason")}}

    async def read_transfer_files(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import transfer_file

        try:
            result = await transfer_file.for_case(self.session, self.firm_id,
                                                  case_id=_parse_uuid(inputs["case_id"], "case_id") if inputs.get("case_id") else None)
        except transfer_file.TransferFileError as exc:
            raise ExecutorError(str(exc))
        open_n = sum(1 for t in result["transfers"] if t["stage"] != "complete")
        result["message"] = f"{len(result['transfers'])} transfer(s) in, {open_n} open, {result['late']} late."
        return result

    async def record_transfer_event(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import transfer_file

        try:
            result = await transfer_file.record_entry(self.session, self.firm_id, by=await self._shelf_user(), **self._transfer_event_args(inputs))
        except transfer_file.TransferFileError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Recorded."
        return result

    async def _transfer_event_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, transfer_file

        try:
            r = await transfer_file.prepare_entry(self.session, self.firm_id, **self._transfer_event_args(inputs))
        except transfer_file.TransferFileError as exc:
            raise ExecutorError(str(exc))
        d = r["detail"]
        t = r["transfer"]
        return [("Client", r["client"]), ("Transfer", f"{t.transfer_type.upper()} from {t.delivering_firm or 'the delivering firm'}"),
                ("What happened", transfer_file.KINDS[r["kind"]]), ("On", firm_time.spoken(r["on"])),
                ("Reason", transfer_file.REJECT_CODES.get(d.get("code")) if d.get("code") else None),
                ("Amount", d.get("amount")),
                ("Memo", "; ".join(f"{m['description']}{' ' + format(m['quantity'], 'g') if m['quantity'] is not None else ''}: {m['how'].replace('_', ' ')}" for m in d.get("lines", [])) or None),
                ("Contract", f"{d['carrier']} {d['contract']}" if d.get("carrier") else None),
                ("Evidence", r["evidence"]), ("Stands now", r["before"]["stage"].replace("_", " "))]

    async def record_rollover_facts(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import transfer_file

        try:
            result = await transfer_file.record_rollover(self.session, self.firm_id, by=await self._shelf_user(), **self._rollover_args(inputs))
        except transfer_file.TransferFileError as exc:
            raise ExecutorError(str(exc))
        missing = (result.get("rollover") or {}).get("missing", [])
        result["message"] = "Recorded. " + (f"Not yet assessed: {', '.join(missing)}." if missing else "Every point is assessed.")
        return result

    async def _rollover_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import transfer_file

        try:
            r = await transfer_file.prepare_rollover(self.session, self.firm_id, **self._rollover_args(inputs))
        except transfer_file.TransferFileError as exc:
            raise ExecutorError(str(exc))
        from app.aurea_core import markets
        from app.models.tenant import Firm

        firm = await self.session.get(Firm, self.firm_id)

        def show(k, v):
            if isinstance(v, bool):
                return "Yes" if v else "No"
            if k.endswith("_bps"):
                return f"{v:g} bps"
            if k in ("balance", "employer_stock_value", "employer_stock_basis", "loan_balance"):
                return markets.money_for(firm)(v)
            return v

        shown = [(transfer_file.ROLLOVER_FACTS[k], show(k, v)) for k, v in r["facts"].items()]
        return [("Case", r["case"].prospect_name), *shown, ("Source", r["source"]),
                ("Still not assessed", ", ".join(r["after"]["missing"]) or "None")]

    def _alert_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        known = inputs.get("subject_known")
        if isinstance(known, str):
            known = known.strip().lower() not in ("false", "no", "0", "n")
        return {"alert_key": inputs.get("alert_key"), "decision": inputs.get("decision"), "reason": inputs.get("reason"), "subject_known": known}

    def _step_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"investigation_id": _parse_uuid(inputs.get("investigation_id") or "", "investigation_id"), "step": inputs.get("step"),
                "on": self._exit_day(inputs.get("on"), "on"), "note": inputs.get("note"), "reference": inputs.get("reference"),
                "attendees": [a for a in (inputs.get("attendees") or "").split(",") if a.strip()], "outcome": inputs.get("outcome"),
                "minutes": inputs.get("minutes")}

    async def read_transaction_monitoring(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import txn_monitoring

        result = await txn_monitoring.for_firm(self.session, self.firm_id,
                                               household_id=_parse_uuid(inputs["household_id"], "household_id") if inputs.get("household_id") else None)
        s = result["summary"]
        result["message"] = f"{s['open_alerts']} open alert(s); {s['open_investigations']} open investigation(s); {s['reports_due_7_days']} report(s) due within 7 days."
        return result

    async def decide_monitoring_alert(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import txn_monitoring

        try:
            result = await txn_monitoring.decide_alert(self.session, self.firm_id, by=await self._shelf_user(), **self._alert_args(inputs))
        except txn_monitoring.MonitoringError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Decided."
        return result

    async def _alert_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, txn_monitoring

        try:
            r = await txn_monitoring.prepare_alert(self.session, self.firm_id, **self._alert_args(inputs))
        except txn_monitoring.MonitoringError as exc:
            raise ExecutorError(str(exc))
        from app.aurea_core import markets
        from app.models.tenant import Firm

        money = markets.money_for(await self.session.get(Firm, self.firm_id))
        a = r["alert"]
        figures = "; ".join(f"{k.replace('_', ' ')} {money(v) if k in ('in', 'out', 'total', 'amount', 'threshold') else v}"
                            for k, v in a["figures"].items() if v is not None)
        effect = ("Closes the alert" if r["decision"] == "closed" else
                  f"Joins the open investigation from {firm_time.spoken(r['joins'].detected_on)}" if r["joins"] else
                  "Opens an investigation; the report clock starts" + ("" if r["subject_known"] else ", subject not identified"))
        return [("Household", a["household"]), ("Alert", a["label"]), ("Figures", figures),
                ("Movements", f"{len(a['moves'])}, {a['first_on']} to {a['last_on']}"), ("Decision", r["decision"].capitalize()),
                ("Reason", r["reason"]), ("Effect", effect)]

    async def record_investigation_step(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import txn_monitoring

        try:
            result = await txn_monitoring.record_step(self.session, self.firm_id, by=await self._shelf_user(), **self._step_args(inputs))
        except txn_monitoring.MonitoringError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Recorded."
        return result

    async def _step_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, txn_monitoring
        from app.models.graph import Household

        try:
            r = await txn_monitoring.prepare_step(self.session, self.firm_id, **self._step_args(inputs))
        except txn_monitoring.MonitoringError as exc:
            raise ExecutorError(str(exc))
        inv, e = r["investigation"], r["entry"]
        household = await self.session.get(Household, inv.household_id)
        return [("Household", household.name if household else None), ("Opened", firm_time.spoken(inv.detected_on)),
                ("Step", e["step"].replace("_", " ").capitalize()), ("On", firm_time.spoken(r["day"])), ("Reference", e.get("reference")),
                ("Forum", ", ".join(e["attendees"]) if e.get("attendees") else None), ("Outcome", e.get("outcome")), ("Minutes", e.get("minutes")),
                ("Note", e["note"])]

    def _link_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        failed = inputs.get("failed")
        if isinstance(failed, str):
            failed = failed.strip().lower() in ("true", "yes", "1", "y")
        return {"account_id": _parse_uuid(inputs.get("account_id") or "", "account_id"), "bank": inputs.get("bank"), "last4": inputs.get("last4"),
                "owner_name": inputs.get("owner_name"), "method": inputs.get("method"), "evidence": inputs.get("evidence"),
                "verified_on": self._exit_day(inputs.get("verified_on"), "verified_on"), "callback_by": inputs.get("callback_by"), "failed": bool(failed)}

    def _deposit_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        uid = lambda k: _parse_uuid(inputs[k], k) if inputs.get(k) else None  # noqa: E731
        return {"account_id": uid("account_id"), "deposit_id": uid("deposit_id"), "bank_link_id": uid("bank_link_id"), "method": inputs.get("method"),
                "amount": inputs.get("amount"), "on": self._exit_day(inputs.get("on"), "on"), "evidence": inputs.get("evidence"), "return_code": inputs.get("return_code")}

    def _revalidation_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"standing_id": _parse_uuid(inputs.get("standing_id") or "", "standing_id"), "evidence": inputs.get("evidence"),
                "on": self._exit_day(inputs.get("on"), "on"), "callback_by": inputs.get("callback_by")}

    async def read_money_in(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import money_in

        result = await money_in.for_firm(self.session, self.firm_id,
                                         household_id=_parse_uuid(inputs["household_id"], "household_id") if inputs.get("household_id") else None)
        s = result["summary"]
        result["message"] = f"{len(result['deposits'])} deposit(s), {s['returned']} returned; {s['instructions_overdue']} instruction(s) overdue for revalidation."
        return result

    async def record_bank_link(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import money_in

        try:
            result = await money_in.record_link(self.session, self.firm_id, by=await self._shelf_user(), **self._link_args(inputs))
        except money_in.MoneyInError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Recorded."
        return result

    async def _bank_link_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, money_in

        try:
            r = await money_in.prepare_link(self.session, self.firm_id, **self._link_args(inputs))
        except money_in.MoneyInError as exc:
            raise ExecutorError(str(exc))
        return [("Account", r["account"].name), ("Bank", f"{r['bank']} ending {r['last4']}"), ("Owner", r["owner"]),
                ("Party", "Third party" if r["third_party"] else "In the household"), ("Callback by", r["callback_by"]),
                ("Verified by", r["method"].replace("_", " ")), ("On", firm_time.spoken(r["on"])), ("Evidence", r["evidence"]),
                ("Status", r["status"].capitalize())]

    async def record_deposit(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import money_in

        try:
            result = await money_in.record_deposit(self.session, self.firm_id, by=await self._shelf_user(), **self._deposit_args(inputs))
        except money_in.MoneyInError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Recorded."
        return result

    async def _deposit_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, markets, money_in
        from app.models.tenant import Firm

        try:
            r = await money_in.prepare_deposit(self.session, self.firm_id, **self._deposit_args(inputs))
        except money_in.MoneyInError as exc:
            raise ExecutorError(str(exc))
        money = markets.money_for(await self.session.get(Firm, self.firm_id))
        if r["kind"] == "return":
            d = r["deposit"]
            return [("Account", r["account"].name), ("Deposit", f"{money(float(d.amount))} by {d.method.upper()} on {firm_time.spoken(d.received_on)}"),
                    ("Returned", f"{r['code']} {money_in.RETURN_CODES[r['code']]}"), ("On", firm_time.spoken(r["on"])), ("Evidence", r["evidence"]),
                    ("Already available", "Yes: it may have been invested" if r["after_available"] else "No: it was still on hold"),
                    ("Bank link", "Revoked" if r["revokes"] else None)]
        return [("Account", r["account"].name), ("Amount", money(r["amount"])), ("By", r["method"].upper()),
                ("From", f"{r['link'].bank} ending {r['link'].last4}" if r["link"] else None), ("Received", firm_time.spoken(r["on"])),
                ("Hold", f"Until {firm_time.spoken(r['hold_until'])}" if r["hold_until"] else "None"), ("Evidence", r["evidence"])]

    async def revalidate_standing_instruction(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import money_in

        try:
            result = await money_in.record_revalidation(self.session, self.firm_id, by=await self._shelf_user(), **self._revalidation_args(inputs))
        except money_in.MoneyInError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Recorded."
        return result

    async def _revalidation_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, markets, money_in
        from app.models.tenant import Firm

        try:
            r = await money_in.prepare_revalidation(self.session, self.firm_id, **self._revalidation_args(inputs))
        except money_in.MoneyInError as exc:
            raise ExecutorError(str(exc))
        s = r["standing"]
        money = markets.money_for(await self.session.get(Firm, self.firm_id))
        return [("Account", r["account"].name), ("Pays", f"{s.payee}, {money(float(s.amount))} {s.frequency}"), ("Third party", "Yes" if s.third_party else "No"),
                ("Callback by", r["callback_by"]), ("Confirmed", firm_time.spoken(r["on"])), ("Evidence", r["evidence"]), ("Next due", firm_time.spoken(r["next"]))]

    def _feature_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"account_id": _parse_uuid(inputs.get("account_id") or "", "account_id"), "feature": inputs.get("feature"), "level": inputs.get("level"),
                "agreement": inputs.get("agreement"), "experience_years": inputs.get("experience_years"), "objective": inputs.get("objective")}

    def _hold_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        uid = lambda k: _parse_uuid(inputs[k], k) if inputs.get(k) else None  # noqa: E731
        return {"action": inputs.get("action"), "account_id": uid("account_id"), "hold_id": uid("hold_id"), "reason": inputs.get("reason"),
                "impairment": inputs.get("impairment"), "notified": inputs.get("notified"), "not_notified_why": inputs.get("not_notified_why"),
                "reference": inputs.get("reference"), "on": self._exit_day(inputs.get("on"), "on")}

    async def read_account_features(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import account_features

        result = await account_features.for_firm(self.session, self.firm_id,
                                                 household_id=_parse_uuid(inputs["household_id"], "household_id") if inputs.get("household_id") else None)
        s = result["summary"]
        result["message"] = f"{s['waiting']} feature request(s) waiting; {s['holds_active']} hold(s) in force, {s['notices_late']} with notice late."
        return result

    async def request_account_feature(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import account_features

        try:
            result = await account_features.request(self.session, self.firm_id, by=await self._shelf_user(), **self._feature_args(inputs))
        except account_features.FeatureError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Asked; a principal decides."
        return result

    async def _feature_request_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import account_features

        try:
            r = await account_features.prepare_request(self.session, self.firm_id, **self._feature_args(inputs))
        except account_features.FeatureError as exc:
            raise ExecutorError(str(exc))
        p = r["profile"]
        return [("Account", r["account"].name), ("Feature", account_features.FEATURES[r["feature"]]),
                ("Level", f"{r['level']}: {account_features.OPTION_LEVELS[r['level']]}" if r["level"] else None),
                ("Experience", f"{p['experience_years']} years" if p else None), ("Objective", p.get("objective") if p else None),
                ("Agreement", r["agreement"]), ("Replaces", f"Level {r['replaces'].level}" if r["replaces"] else None), ("Next", "A principal decides")]

    async def decide_account_feature(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import account_features

        try:
            result = await account_features.decide(self.session, self.firm_id, by=await self._shelf_user(),
                                                   feature_id=_parse_uuid(inputs.get("feature_id") or "", "feature_id"),
                                                   decision=inputs.get("decision"), reason=inputs.get("reason"))
        except account_features.FeatureError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Decided."
        return result

    async def _feature_decision_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import account_features

        try:
            r = await account_features.prepare_decision(self.session, self.firm_id, by=await self._shelf_user(),
                                                         feature_id=_parse_uuid(inputs.get("feature_id") or "", "feature_id"),
                                                         decision=inputs.get("decision"), reason=inputs.get("reason"))
        except account_features.FeatureError as exc:
            raise ExecutorError(str(exc))
        f = r["feature"]
        return [("Account", r["account"].name), ("Feature", account_features.FEATURES[f.feature] + (f" level {f.level}" if f.level else "")),
                ("Asked by", f.requested_by), ("Agreement", f.agreement), ("Decision", r["decision"].capitalize()), ("Reason", r["reason"])]

    async def record_senior_hold(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import account_features

        try:
            result = await account_features.record_hold(self.session, self.firm_id, by=await self._shelf_user(), **self._hold_args(inputs))
        except account_features.FeatureError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Recorded."
        return result

    async def _senior_hold_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import account_features, firm_time

        try:
            r = await account_features.prepare_hold(self.session, self.firm_id, **self._hold_args(inputs))
        except account_features.FeatureError as exc:
            raise ExecutorError(str(exc))
        rows = [("Account", r["account"].name), ("Action", r["action"].capitalize()), ("On", firm_time.spoken(r["on"])), ("Reason", r["reason"])]
        if r["action"] == "place":
            rows += [("Specified adult", f"{r['age']} years old" if r["adult"] == "age_65" else f"Impairment: {r['impairment']}"),
                     ("Ends", firm_time.spoken(r["expires"])), ("Effect", "No new payment out of this account while it stands"),
                     ("Notice due", firm_time.spoken(account_features.business_days_after(r["on"], account_features.NOTIFY_DAYS)))]
        if r["action"] == "notify":
            rows += [("Told", ", ".join(r["notified"]) or None), ("Not told, because", r["not_notified_why"])]
        if r["action"] == "extend":
            rows += [("By", f"{r['days']} business days"), ("Now ends", firm_time.spoken(r["expires"])), ("Report", r["reference"])]
        if r["action"] == "release":
            rows += [("Effect", "Payments out may be requested again")]
        return rows

    def _feedback_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"case_id": _parse_uuid(inputs.get("case_id") or "", "case_id"), "score": inputs.get("score"), "comment": inputs.get("comment"),
                "given_on": self._exit_day(inputs.get("given_on"), "given_on"), "source": inputs.get("source")}

    async def read_onboarding_scoreboard(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.api.onboarding_scoreboard import scoreboard

        result = await scoreboard(self.session, await self._firm())
        d, n = result["days_to_invest"], result["nps"]
        result["message"] = (f"Days to invest: median {d['p50']} across {d['n']} account(s). {result['abandonment']['stalled']} stalled. "
                             + (f"NPS {n['nps']} from {n['responses']} answer(s)." if n["nps"] is not None else "NPS not measured."))
        return result

    async def record_onboarding_feedback(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.api.onboarding_scoreboard import scoreboard
        from app.aurea_core import onboarding_scoreboard

        try:
            await onboarding_scoreboard.record_feedback(self.session, self.firm_id, by=await self._shelf_user(), **self._feedback_args(inputs))
        except onboarding_scoreboard.ScoreboardError as exc:
            raise ExecutorError(str(exc))
        result = await scoreboard(self.session, await self._firm())
        result["message"] = "Recorded."
        return result

    async def _feedback_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, onboarding_scoreboard

        try:
            r = await onboarding_scoreboard.prepare_feedback(self.session, self.firm_id, **self._feedback_args(inputs))
        except onboarding_scoreboard.ScoreboardError as exc:
            raise ExecutorError(str(exc))
        band = "Promoter" if r["score"] >= 9 else "Detractor" if r["score"] <= 6 else "Passive"
        return [("Client", r["case"].prospect_name), ("Score", f"{r['score']} of 10"), ("Counts as", band), ("Comment", r["comment"]),
                ("Given", firm_time.spoken(r["on"])), ("Source", r["source"])]

    def _private_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        def yes(k):
            v = inputs.get(k)
            if v is None or v == "":
                return None
            return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1", "y")

        return {"transfer_id": _parse_uuid(inputs.get("transfer_id") or "", "transfer_id"), "kind": inputs.get("kind"), "evidence": inputs.get("evidence"),
                "on": self._exit_day(inputs.get("on"), "on"), "fund": inputs.get("fund"), "commitment": inputs.get("commitment"), "unfunded": inputs.get("unfunded"),
                "amount": inputs.get("amount"), "due_on": self._exit_day(inputs.get("due_on"), "due_on") if inputs.get("due_on") else None,
                "funded_by": inputs.get("funded_by"), "security": inputs.get("security"), "shares": inputs.get("shares"),
                "acquired_on": self._exit_day(inputs.get("acquired_on"), "acquired_on") if inputs.get("acquired_on") else None,
                "affiliate": yes("affiliate"), "reporting_issuer": yes("reporting_issuer"), "outcome": inputs.get("outcome"), "opinion": inputs.get("opinion")}

    async def record_private_transfer_item(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import transfer_file

        try:
            result = await transfer_file.record_private(self.session, self.firm_id, by=await self._shelf_user(), **self._private_args(inputs))
        except transfer_file.TransferFileError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Recorded."
        return result

    async def _private_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from datetime import date

        from app.aurea_core import firm_time, markets, transfer_file
        from app.models.tenant import Firm

        try:
            r = await transfer_file.prepare_private(self.session, self.firm_id, **self._private_args(inputs))
        except transfer_file.TransferFileError as exc:
            raise ExecutorError(str(exc))
        money = markets.money_for(await self.session.get(Firm, self.firm_id))
        d = r["detail"]
        rows = [("Client", r["client"]), ("What happened", transfer_file.KINDS[r["kind"]]), ("On", firm_time.spoken(r["on"]))]
        if d.get("fund"):
            rows.append(("Fund", d["fund"]))
        if r["kind"] == "assignment_requested":
            rows += [("Commitment", money(d["commitment"])), ("Unfunded", money(d["unfunded"]))]
        if r["kind"] == "capital_call":
            rows += [("Called", money(d["amount"])), ("Due", firm_time.spoken(date.fromisoformat(d["due_on"]))), ("Funded by", d["funded_by"] or "Not yet said")]
        if r["kind"] == "restricted_stock":
            met, ends = transfer_file.holding_period_met(date.fromisoformat(d["acquired_on"]), r["today"], reporting_issuer=d["reporting_issuer"])
            rows += [("Security", f"{d['security']}, {d['shares']:g} shares"), ("Holding period", ("Met " if met else "Ends ") + firm_time.spoken(ends)),
                     ("Affiliate", "Yes: volume, manner-of-sale and notice conditions apply to every sale" if d["affiliate"] else "No")]
        if r["kind"] == "legend_review":
            rows += [("Security", d["security"]), ("Outcome", "Legend removed" if d["outcome"] == "removed" else "Legend stays"), ("Opinion", d["opinion"])]
        return rows + [("Evidence", r["evidence"])]

    def _household_list(self, value) -> list:
        return [_parse_uuid(x.strip(), "household_ids") for x in (value if isinstance(value, list) else str(value or "").split(",")) if str(x).strip()]

    async def read_repapering(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import repapering

        result = await repapering.for_firm(self.session, self.firm_id,
                                           campaign_id=_parse_uuid(inputs["campaign_id"], "campaign_id") if inputs.get("campaign_id") else None)
        result["message"] = "; ".join(f"{c['name']}: {c['ready']} of {c['total']} ready" for c in result["campaigns"]) or "No campaigns."
        return result

    async def start_repapering(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import repapering

        try:
            result = await repapering.start(self.session, self.firm_id, by=await self._shelf_user(), name=inputs.get("name"), change_type=inputs.get("change_type"),
                                            document=inputs.get("document"), household_ids=self._household_list(inputs.get("household_ids")))
        except repapering.RepaperingError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Started."
        return result

    async def _repapering_start_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import repapering

        try:
            r = await repapering.prepare_campaign(self.session, self.firm_id, name=inputs.get("name"), change_type=inputs.get("change_type"),
                                                  document=inputs.get("document"), household_ids=self._household_list(inputs.get("household_ids")))
        except repapering.RepaperingError as exc:
            raise ExecutorError(str(exc))
        rule = r["rule"]
        consent = (f"Negative: silence for {rule['notice_days']} days after sending is consent" if rule["consent"] == "negative"
                   else "Affirmative: only a signed answer counts")
        return [("Campaign", r["name"]), ("Change", rule["label"]), ("Consent", consent), ("Table", "The firm's" if rule["set_by_firm"] else "The platform's"),
                ("Households", f"{len(r['households'])}: " + ", ".join(h.name for h in r["households"][:5]) + ("…" if len(r["households"]) > 5 else "")),
                ("Document", r["document"])]

    async def record_repapering(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import repapering

        try:
            result = await repapering.record_step(self.session, self.firm_id, by=await self._shelf_user(),
                                                  campaign_id=_parse_uuid(inputs.get("campaign_id") or "", "campaign_id"), action=inputs.get("action"),
                                                  evidence=inputs.get("evidence"), on=self._exit_day(inputs.get("on"), "on"),
                                                  household_ids=self._household_list(inputs.get("household_ids")))
        except repapering.RepaperingError as exc:
            raise ExecutorError(str(exc))
        result["message"] = "Recorded."
        return result

    async def _repapering_step_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from datetime import timedelta

        from app.aurea_core import firm_time, repapering
        from app.models.graph import Household

        try:
            r = await repapering.prepare_step(self.session, self.firm_id, campaign_id=_parse_uuid(inputs.get("campaign_id") or "", "campaign_id"),
                                              action=inputs.get("action"), evidence=inputs.get("evidence"), on=self._exit_day(inputs.get("on"), "on"),
                                              household_ids=self._household_list(inputs.get("household_ids")))
        except repapering.RepaperingError as exc:
            raise ExecutorError(str(exc))
        c = r["campaign"]
        names = [(await self.session.get(Household, a.household_id)).name for a in r["answers"]]
        effect = (f"The notice period runs to {firm_time.spoken(r['on'] + timedelta(days=c.notice_days))}" if r["action"] == "sent" and c.notice_days
                  else "Signatures are now awaited" if r["action"] == "sent"
                  else "Ready to convert" if r["action"] == "consented"
                  else "Kept off the conversion" if r["action"] == "objected" else "Silence is not consent for these: they need another notice or a signature")
        return [("Campaign", c.name), ("Action", r["action"].capitalize()), ("On", firm_time.spoken(r["on"])),
                ("Households", ", ".join(names) or None), ("Evidence", r["evidence"]), ("Effect", effect)]

    async def rate_aml_risk(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import aml_risk

        try:
            result = await aml_risk.record_rating(self.session, _parse_uuid(inputs.get("case_id") or "", "case_id"), firm_id=self.firm_id,
                                                  by=await self._shelf_user(), role=self.role, **self._risk_args(inputs))
        except aml_risk.AmlRiskError as exc:
            raise ExecutorError(str(exc))
        r = result["rating"]
        result["message"] = f"Rated {r['band']} (the model said {r['model_band']}, score {result['score']:.0f}). Next review {r['next_review_on']}."
        return result

    async def _risk_rating_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import aml_risk

        try:
            r = await aml_risk.prepare_rating(self.session, _parse_uuid(inputs.get("case_id") or "", "case_id"), firm_id=self.firm_id,
                                              role=self.role, **self._risk_args(inputs))
        except aml_risk.AmlRiskError as exc:
            raise ExecutorError(str(exc))
        c = aml_risk.CONSEQUENCES[r["band"]]
        return [("Case", r["case"].prospect_name), ("Score", f"{r['score']:.0f} of 100, model {aml_risk.MODEL_VERSION}"),
                ("Factors", "; ".join(f"{f['why']} {f['points']:.0f}" for f in r["factors"] if f["points"]) or "None scored"),
                ("Model says", r["model_band"].capitalize()), ("Rating", r["band"].capitalize() + (" (override)" if r["override"] else "")),
                ("Override reason", r["reason"] if r["override"] else None),
                ("Paths", "; ".join(f"{p['path'].replace('_', ' ')}: {', '.join(p['requires'])}" for p in r["paths"]) or None),
                ("Due diligence", c["due_diligence"]), ("Approver", c["approver"]), ("Review", f"Every {aml_risk.REVIEW_MONTHS[r['band']]} months")]

    async def read_aml_reviews(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import aml_review

        try:
            if inputs.get("case_id"):
                result = await aml_review.for_case(self.session, _parse_uuid(inputs["case_id"], "case_id"), firm_id=self.firm_id)
            else:
                result = await aml_review.for_firm(self.session, self.firm_id)
        except aml_review.AmlReviewError as exc:
            raise ExecutorError(str(exc))
        s = result["summary"]
        result["message"] = (f"{s['open']} hit(s) open, {s['awaiting_confirmation']} true match(es) waiting for a second reviewer, {s['qa_due']} due for QA; "
                             f"{s['edd_pending']} EDD file(s) awaiting sign-off, {s['edd_declined']} declined.")
        return result

    def _screening_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"hit": inputs.get("hit"), "kind": inputs.get("kind"), "decision": inputs.get("decision"), "rationale": inputs.get("rationale")}

    def _edd_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        evidence = {}
        for line in inputs.get("evidence") or []:
            key, _, source = str(line).partition(":")
            if key.strip() and source.strip():
                evidence[key.strip().lower().replace(" ", "_")] = source.strip()
        return {"wealth_type": inputs.get("wealth_type"), "evidence": evidence, "conclusion": inputs.get("conclusion"),
                "decision": inputs.get("decision"), "rationale": inputs.get("rationale")}

    async def record_screening_decision(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import aml_review

        try:
            result = await aml_review.record_decision(self.session, _parse_uuid(inputs.get("party_id") or "", "party_id"), firm_id=self.firm_id,
                                                      by=await self._shelf_user(), role=self.role, **self._screening_args(inputs))
        except aml_review.AmlReviewError as exc:
            raise ExecutorError(str(exc))
        s = result["summary"]
        result["message"] = f"Recorded. On this case: {s['open']} hit(s) open, {s['awaiting_confirmation']} waiting for a second reviewer."
        return result

    async def sign_off_edd(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import aml_review

        try:
            result = await aml_review.record_edd(self.session, _parse_uuid(inputs.get("case_id") or "", "case_id"), firm_id=self.firm_id,
                                                 by=await self._shelf_user(), role=self.role, **self._edd_args(inputs))
        except aml_review.AmlReviewError as exc:
            raise ExecutorError(str(exc))
        edd = result["edd"][0] if result["edd"] else {}
        result["message"] = f"Recorded. EDD on {result.get('case')} is now {str(edd.get('edd_status', '')).replace('_', ' ')}."
        return result

    async def _aml_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import aml_review

        try:
            if tool_key == "record_screening_decision":
                r = await aml_review.prepare_decision(self.session, _parse_uuid(inputs.get("party_id") or "", "party_id"), firm_id=self.firm_id,
                                                      by=await self._shelf_user(), role=self.role, **self._screening_args(inputs))
                h = r["hit"]
                effect = {("disposition", "true_match"): "Waits for a second reviewer in compliance",
                          ("disposition", "false_positive"): "Clears this hit; it may be sampled for QA",
                          ("disposition", "needs_info"): "The hit stays open",
                          ("confirmation", "confirmed"): "Stops the case" if h.get("category") == "sanctions" else "Opens enhanced due diligence",
                          ("confirmation", "rejected"): "Reopens the hit",
                          ("qa", "agree"): "The false positive stands", ("qa", "overturn"): "Reopens the hit"}[(r["kind"], r["decision"])]
                dob = (h.get("discriminators") or {}).get("dob")
                return [("Case", r["case"].prospect_name), ("Party", f"{r['party'].legal_name} · {r['party'].role.replace('_', ' ')}"),
                        ("Hit", f"{str(h.get('category')).replace('_', ' ')} · {h.get('matched_name')} · score {h.get('score')}"),
                        ("Date of birth", dob if dob and dob != "unknown" else None), ("Was", r["was"].replace("_", " ")),
                        ("Decision", f"{r['kind'].capitalize()}: {r['decision'].replace('_', ' ')}"), ("Effect", effect), ("Reason", r["rationale"])]
            r = await aml_review.prepare_edd(self.session, _parse_uuid(inputs.get("case_id") or "", "case_id"), firm_id=self.firm_id, role=self.role,
                                             **self._edd_args(inputs))
            effect = {"approve": "Completes EDD", "decline": "The account does not open", "more_info": "EDD stays pending"}[r["decision"]]
            return [("Case", r["case"].prospect_name), ("Risk", (r["tier"] or "not rated").capitalize()), ("Decision", r["decision"].replace("_", " ").capitalize()),
                    ("Wealth", r["wealth_type"].replace("_", " ") if r["wealth_type"] != "unstated" else None),
                    ("Sources", "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in r["evidence"].items()) or None),
                    ("Conclusion", r["conclusion"].replace("_", " ")), ("Effect", effect), ("Reason", r["rationale"])]
        except aml_review.AmlReviewError as exc:
            raise ExecutorError(str(exc))

    async def read_client_exits(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import exits, markets

        if inputs.get("household_id"):
            household_id = _parse_uuid(inputs["household_id"], "household_id")
            result = await exits.for_household(self.session, household_id, firm_id=self.firm_id)
            if result is None:
                raise ExecutorError(f"Household {household_id} not found or access denied")
        else:
            result = await exits.for_firm(self.session, self.firm_id)
        s = result["summary"]
        say = markets.money_for(await self._firm())
        result["message"] = (f"{s['departures']} client(s) leaving and {s['estates']} estate(s) open, {say(s['value_open'])} between them. "
                             f"{say(s['stayed_at_firm'])} has stayed at the firm and {say(s['left_firm'])} has left. "
                             "None on file means none has been recorded here.")
        return result

    @staticmethod
    def _exit_day(value: Any, name: str):
        from datetime import date as _date

        if value in (None, ""):
            return None
        try:
            return _date.fromisoformat(str(value)[:10])
        except ValueError:
            raise ExecutorError(f"{name} must be a date, YYYY-MM-DD.")

    def _departure_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"notice_date": self._exit_day(inputs.get("notice_date"), "notice_date"), "reason": inputs.get("reason"),
                "source_document": inputs.get("source_document"), "receiving_institution": inputs.get("receiving_institution"),
                "mandate_id": _parse_uuid(inputs["mandate_id"], "mandate_id") if inputs.get("mandate_id") else None}

    def _death_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"date_of_death": self._exit_day(inputs.get("date_of_death"), "date_of_death"),
                "source_document": inputs.get("source_document"), "executor": inputs.get("executor")}

    def _transfer_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        share = inputs.get("share_pct")
        try:
            share = float(share) / 100 if share not in (None, "") else None
        except (TypeError, ValueError):
            raise ExecutorError("share_pct must be a number.")
        return {"to_party": inputs.get("to_party"), "transfer_date": self._exit_day(inputs.get("transfer_date"), "transfer_date"),
                "source_document": inputs.get("source_document"), "share": share,
                "to_account_id": _parse_uuid(inputs["to_account_id"], "to_account_id") if inputs.get("to_account_id") else None}

    async def _exit_write(self, write) -> dict[str, Any]:
        """Run a write under the person's name, and answer with the record as it now stands."""
        from app.aurea_core import exits
        from app.models.identity import User

        user = await self.session.get(User, self.user_id)
        try:
            row = await write(exits, user.email if user else "unknown")
        except exits.ExitError as exc:
            raise ExecutorError(str(exc))
        return await exits.view_of(self.session, row.exit_id if hasattr(row, "exit_id") else row.id, firm_id=self.firm_id)

    async def start_client_departure(self, inputs: dict[str, Any]) -> dict[str, Any]:
        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")
        args = self._departure_args(inputs)
        result = await self._exit_write(lambda e, by: e.start_departure(self.session, household_id, firm_id=self.firm_id, by=by, **args))
        result["message"] = f"{result['household']} is recorded as leaving. Their mandates are on hold, and nothing has moved."
        return result

    async def record_client_death(self, inputs: dict[str, Any]) -> dict[str, Any]:
        person_id = _parse_uuid(inputs.get("person_id") or "", "person_id")
        args = self._death_args(inputs)
        result = await self._exit_write(lambda e, by: e.record_death(self.session, person_id, firm_id=self.firm_id, by=by, **args))
        result["message"] = f"An estate is open for {result['person']}, and trading has stopped on their mandates."
        return result

    async def record_executor(self, inputs: dict[str, Any]) -> dict[str, Any]:
        exit_id = _parse_uuid(inputs.get("exit_id") or "", "exit_id")
        result = await self._exit_write(lambda e, by: e.record_executor(
            self.session, exit_id, firm_id=self.firm_id, by=by, executor=inputs.get("executor"), source_document=inputs.get("source_document")))
        result["message"] = f"{result['executor']} is on file for the estate of {result['person']}."
        return result

    async def record_account_transfer(self, inputs: dict[str, Any]) -> dict[str, Any]:
        exit_id = _parse_uuid(inputs.get("exit_id") or "", "exit_id")
        account_id = _parse_uuid(inputs.get("account_id") or "", "account_id")
        args = self._transfer_args(inputs)
        result = await self._exit_write(lambda e, by: e.record_transfer(self.session, exit_id, account_id, firm_id=self.firm_id, by=by, **args))
        c = result["checks"]
        result["message"] = f"Recorded. {c['accounts_open']} of {c['accounts']} account(s) still open."
        return result

    async def close_client_exit(self, inputs: dict[str, Any]) -> dict[str, Any]:
        exit_id = _parse_uuid(inputs.get("exit_id") or "", "exit_id")
        result = await self._exit_write(lambda e, by: e.close(self.session, exit_id, firm_id=self.firm_id, by=by))
        result["message"] = f"Closed. {result['household']}'s accounts and mandates on this have ended."
        return result

    async def withdraw_client_exit(self, inputs: dict[str, Any]) -> dict[str, Any]:
        exit_id = _parse_uuid(inputs.get("exit_id") or "", "exit_id")
        result = await self._exit_write(lambda e, by: e.cancel(self.session, exit_id, firm_id=self.firm_id, by=by, reason=inputs.get("reason")))
        result["message"] = f"Withdrawn. The hold is off {result['household']}'s mandates."
        return result

    async def _exit_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """What each exit card names, refused for the reasons doing it would be refused."""
        from app.aurea_core import exits, firm_time, markets

        firm = await self._firm()
        say = markets.money_for(firm)

        def book_rows(book: dict) -> list[tuple[str, Any]]:
            gaps = [p["symbol"] for a in book["accounts"] for p in a["positions"] if p["value"] is None]
            return [("Accounts", ", ".join(a["name"] for a in book["accounts"]) or "None"),
                    ("Value then", say(book["total"])), ("No real price", ", ".join(gaps) or None)]

        try:
            if tool_key == "start_client_departure":
                r = await exits.prepare_departure(self.session, _parse_uuid(inputs.get("household_id") or "", "household_id"),
                                                  firm_id=self.firm_id, **self._departure_args(inputs))
                return [("Client", r["household"].name), ("Scope", "Whole household" if r["whole"] else r["mandates"][0].name),
                        ("Notice", firm_time.spoken(r["day"])), *book_rows(r["book"]), ("Reason", r["reason"]),
                        ("Going to", r["receiving"]), ("Document", r["document"]), ("Hold", "May sell, may not buy. The rebalancer stops")]
            if tool_key == "record_client_death":
                r = await exits.prepare_death(self.session, _parse_uuid(inputs.get("person_id") or "", "person_id"),
                                              firm_id=self.firm_id, **self._death_args(inputs))
                return [("Person", r["person"].full_name), ("Date of death", firm_time.spoken(r["day"])), *book_rows(r["book"]),
                        ("Document", r["document"]), ("Executor", r["executor"] or "None on file"),
                        ("Hold", "May sell, may not buy" if r["executor"] else "No trading until an executor is on file")]
            if tool_key == "record_executor":
                r = await exits.prepare_executor(self.session, _parse_uuid(inputs.get("exit_id") or "", "exit_id"), firm_id=self.firm_id,
                                                 executor=inputs.get("executor"), source_document=inputs.get("source_document"))
                return [("Estate of", r["person"].full_name if r["person"] else None), ("Executor", r["executor"]), ("Document", r["document"]),
                        ("Hold", "May then sell, may not buy")]
            if tool_key == "record_account_transfer":
                r = await exits.prepare_transfer(self.session, _parse_uuid(inputs.get("exit_id") or "", "exit_id"),
                                                 _parse_uuid(inputs.get("account_id") or "", "account_id"), firm_id=self.firm_id,
                                                 **self._transfer_args(inputs))
                lines = [f"{float(m['quantity']):g} {m['instrument'].symbol}" for m in r["moves"]]
                footing = {"designation": "Beneficiary designation", "estate": "Through the estate", "survivor": "Joint survivor", "departure": None}
                return [("Account", r["account"].name), ("To", r["to_party"]), ("Stays at the firm", r["to_account"].name if r["to_account"] else None),
                        ("Footing", footing[r["footing"]]), ("Share", f"{r['share']:.1%}"), ("Date", firm_time.spoken(r["day"])),
                        ("Positions", "; ".join(lines) or None), ("Cash", say(float(r["cash"])) if r["cash"] > 0 else None), ("Value", say(float(r["value"]))),
                        ("No real price", ", ".join(r["unpriced"]) or None),
                        ("Basis", {"stepped_up": "Steps up to the value at death", "carried": "Carried over", "left_firm": None}[r["basis"]]),
                        ("Account then", "Closes" if r["last"] else None), ("Document", r["document"])]
            if tool_key == "close_client_exit":
                r = await exits.prepare_close(self.session, _parse_uuid(inputs.get("exit_id") or "", "exit_id"), firm_id=self.firm_id)
                row = r["exit"]
                return [("Client", r["household"].name if r["household"] else None), ("Kind", "Estate" if row.kind == "death" else "Departure"),
                        ("Accounts closing", ", ".join(a.name for a in r["accounts"]) or "None"), ("Mandates ending", str(len(row.mandates or []))),
                        ("Household", "Leaves the client list" if row.whole_household else None)]
            r = await exits.prepare_cancel(self.session, _parse_uuid(inputs.get("exit_id") or "", "exit_id"), firm_id=self.firm_id, reason=inputs.get("reason"))
            row = r["exit"]
            return [("Client", r["household"].name if r["household"] else None), ("Kind", "Estate" if row.kind == "death" else "Departure"),
                    ("Date of death cleared", r["person"].full_name if r["person"] else None), ("Reason", r["reason"]),
                    ("Hold", "Comes off these mandates")]
        except exits.ExitError as exc:
            raise ExecutorError(str(exc))

    async def read_ips_monitoring(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import ips

        if inputs.get("mandate_id"):
            mandate_id = _parse_uuid(inputs["mandate_id"], "mandate_id")
            result = await ips.for_mandate(self.session, mandate_id, firm_id=self.firm_id)
            if result is None:
                raise ExecutorError(f"Mandate {mandate_id} not found or access denied")
            result["message"] = (f"{result['mandate']}: {result['status']}, {len(result['findings'])} term(s) outside IPS "
                                 f"version {result['ips']['version']}." if result["ips"]
                                 else f"{result['mandate']}: no IPS recorded here, so it is measured against its model.")
            return result
        if inputs.get("household_id"):
            household_id = _parse_uuid(inputs["household_id"], "household_id")
            result = await ips.for_household(self.session, household_id, firm_id=self.firm_id)
            if result is None:
                raise ExecutorError(f"Household {household_id} not found or access denied")
        else:
            result = await ips.for_firm(self.session, self.firm_id)
        s = result["summary"]
        result["message"] = (f"{s['mandates']} mandate(s): {s['breach']} in breach of their IPS, {s['excepted']} with an "
                             f"accepted exception, {s['within']} within, {s['not_assessed']} not assessed, and {s['no_ips']} "
                             f"with no IPS recorded here, measured against their model.")
        return result

    @staticmethod
    def _ips_percent(value: Any, name: str) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value) / 100
        except (TypeError, ValueError):
            raise ExecutorError(f"{name} must be a number.")

    def _ips_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The tool's percentages and 'equity 60' pairs as the fractions the record holds."""
        targets = None
        if inputs.get("targets"):
            targets = {}
            for item in inputs["targets"]:
                parts = str(item).replace(":", " ").replace("=", " ").replace("%", " ").split()
                if len(parts) != 2:
                    raise ExecutorError(f"A target is a class and a percentage, like 'equity 60', not '{item}'.")
                targets[parts[0]] = self._ips_percent(parts[1], parts[0])
        return {"accepted_on": self._review_day(inputs.get("accepted_on"), "accepted_on"), "document": inputs.get("document"),
                "targets": targets, "drift_band": self._ips_percent(inputs.get("drift_band"), "drift_band"),
                "max_position": self._ips_percent(inputs.get("max_position"), "max_position"),
                "min_cash": self._ips_percent(inputs.get("min_cash"), "min_cash"),
                "restricted": inputs.get("restricted"), "reason": inputs.get("reason")}

    async def record_ips(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import ips
        from app.models.identity import User

        if self.role != UserRole.ADVISER:
            raise ExecutorError("Only an adviser records an IPS.")
        mandate_id = _parse_uuid(inputs.get("mandate_id"), "mandate_id")
        user = await self.session.get(User, self.user_id)
        try:
            result = await ips.record(self.session, mandate_id, firm_id=self.firm_id, by=user.email if user else "unknown",
                                      role=str(self.role), **self._ips_args(inputs))
        except ips.IpsError as exc:
            raise ExecutorError(str(exc))
        result["message"] = f"Recorded version {result['version']} of the IPS for {result['mandate']}: {result['status']}."
        return result

    async def _ips_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """What the IPS Confirm card names, refused for the reasons recording would be refused."""
        from app.aurea_core import firm_time, ips

        def share(value: float | None) -> str | None:
            return f"{value:.1%}" if value is not None else None

        mandate_id = _parse_uuid(inputs.get("mandate_id"), "mandate_id")
        try:
            r = await ips.prepare_record(self.session, mandate_id, firm_id=self.firm_id, **self._ips_args(inputs))
        except ips.IpsError as exc:
            raise ExecutorError(str(exc))
        return [("Mandate", r["mandate"].name), ("Version", r["version"]), ("Accepted on", firm_time.spoken(r["accepted_on"])),
                ("Document", r["document"]),
                ("Targets", ", ".join(f"{c.replace('_', ' ')} {w:.1%}" for c, w in r["targets"].items())),
                ("Band", f"±{r['drift_band']:.1%}"), ("Largest holding", share(r["max_position"])),
                ("Cash floor", share(r["min_cash"])), ("Restricted", ", ".join(r["restricted"]) or None),
                ("Reason", r["reason"])]

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

    @staticmethod
    def _statement_day(inputs: dict[str, Any]):
        from datetime import date as _date

        try:
            return _date.fromisoformat(str(inputs.get("as_of") or ""))
        except ValueError:
            raise ExecutorError("as_of must be a date, YYYY-MM-DD.")

    async def _recon_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """What a reconciliation Confirm card names, refused for the reasons doing it would be refused."""
        from app.aurea_core import firm_time, markets, reconciliation

        firm = await self._firm()
        say = markets.money_for(firm)
        try:
            if tool_key == "import_custody_statement":
                as_of = self._statement_day(inputs)
                r = await reconciliation.prepare_statement(
                    self.session, self.firm_id, account_id=_parse_uuid(inputs.get("account_id"), "account_id"), as_of=as_of,
                    positions=inputs.get("positions"), cash_balance=inputs.get("cash_balance"),
                    source_document=inputs.get("source_document") or "")
                held = ", ".join(f"{s} {float(q):g}" for s, q in r["cleaned"].items())
                return [("Account", r["account"].name), ("Custodian", r["account"].custodian), ("As at", firm_time.spoken(as_of)),
                        ("Positions", f"{len(r['cleaned'])}: {held}" if r["cleaned"] else "None"),
                        ("Cash", say(float(r["cash"])) if r["cash"] is not None else "Not on the statement"),
                        ("Document", (inputs.get("source_document") or "").strip())]
            action = str(inputs.get("action") or "").strip().lower()
            r = await reconciliation.prepare_resolution(self.session, self.firm_id, _parse_uuid(inputs.get("break_id"), "break_id"),
                                                        action=action, note=inputs.get("note"))
            b = r["break"]
            cash = b.symbol is None
            show = (lambda v: say(float(v))) if cash else (lambda v: f"{float(v):g}")
            return [("Break", b.symbol or "Cash"), ("Account", r["account"].name if r["account"] else None),
                    ("Book", show(b.book_value)), ("Custodian", show(b.custodian_value)), ("Difference", show(b.difference)),
                    ("Decision", {"explained": "Accept as explained", "escalated": "Escalate to the custodian",
                                  "dismissed": "Set aside", "resolved": "Mark as fixed"}[action]),
                    ("Reason", r["note"])]
        except reconciliation.ReconciliationError as exc:
            raise ExecutorError(str(exc))

    async def import_custody_statement(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import reconciliation
        from app.models.identity import User

        if self.role not in (UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM):
            raise ExecutorError("Importing a custodian statement is for operations and the portfolio team.")
        as_of = self._statement_day(inputs)
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

    async def _billing_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import billing_run

        r = await billing_run.what_would_bill(self.session, self.firm_id)
        if r["waiting"] is not None:
            return [("Awaiting approval", f"The run for {r['waiting'].as_of.isoformat()}"),
                    ("Effect", "No new run is drafted while that one is open")]
        amounts = "; ".join(f"{c} {v:,.2f}" for c, v in r["amounts"].items())
        return [("Bills", str(r["bills"])), ("To issue", amounts or "Nothing"), ("Held by a block", str(r["blocked"])),
                ("Already billed", str(r["already_billed"])), ("No fee agreement", str(r["no_agreement"])),
                ("Effect", "Nothing is issued or taken until it is approved")]

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

    async def _invoice_to_credit(self, inputs: dict[str, Any]):
        from app.aurea_core import billing_run
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
        try:
            return inv, reason, billing_run.check_creditable(inv)
        except billing_run.BillingError as exc:
            raise ExecutorError(str(exc))

    async def _credit_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        inv, reason, r = await self._invoice_to_credit(inputs)
        amount = f"{inv.currency} {float(inv.amount):,.2f}"
        return [("Invoice", inv.invoice_number), ("Client", inv.household_name or inv.mandate_name),
                ("Period", f"{inv.period_start.isoformat()} to {inv.period_end.isoformat()}"), ("Credited", amount),
                ("Back to cash", f"{inv.currency} {r['returned']:,.2f} across {r['accounts']} account(s)" if r["returned"]
                 else "Nothing, no fee was taken"), ("Reason", reason)]

    async def credit_invoice(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import billing_run, billing_views
        from app.models.identity import User

        inv, reason, _ = await self._invoice_to_credit(inputs)
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

    async def _account(self, inputs: dict[str, Any]):
        from app.models.graph import Account

        account_id = _parse_uuid(inputs.get("account_id") or "", "account_id")
        account = (await self.session.execute(
            select(Account).where(Account.id == account_id, Account.firm_id == self.firm_id)
        )).scalar_one_or_none()
        if account is None:
            raise ExecutorError(f"Account {account_id} not found")
        return account

    async def _registration_args(self, inputs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        """The account and the changes, all checked before any is made."""
        from datetime import date as _date

        from app.models.enums import RegistrationType

        account = await self._account(inputs)
        changes: dict[str, Any] = {}
        reg_type = inputs.get("registration_type")
        if reg_type is not None:
            if reg_type not in {t.value for t in RegistrationType}:
                raise ExecutorError(f"Unknown registration_type: {reg_type}")
            changes["registration_type"] = reg_type

        death_date = inputs.get("original_owner_death_date")
        if death_date:
            try:
                changes["original_owner_death_date"] = _date.fromisoformat(str(death_date)[:10])
            except ValueError:
                raise ExecutorError(f"Invalid original_owner_death_date: {death_date}")

        election = inputs.get("rmd_election_method")
        if election is not None:
            if election not in ("10_year_rule", "life_expectancy"):
                raise ExecutorError(f"rmd_election_method must be 10_year_rule or life_expectancy, got {election}")
            changes["rmd_election_method"] = election
        if not changes:
            raise ExecutorError("Say what to change on this account's registration.")
        return account, changes

    async def _registration_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time

        account, changes = await self._registration_args(inputs)
        kind = changes.get("registration_type")
        death = changes.get("original_owner_death_date")
        method = changes.get("rmd_election_method")
        return [("Account", account.name),
                ("Registration", f"{(account.registration_type or 'Unset').replace('_', ' ')} becomes {kind.replace('_', ' ')}") if kind else ("Registration", None),
                ("Original owner died", firm_time.spoken(death) if death else None),
                ("RMD election", method.replace("_", " ") if method else None),
                ("Effect", "Changes how the account's tax treatment is worked out")]

    async def set_account_registration(self, inputs: dict[str, Any]) -> dict[str, Any]:
        account, changes = await self._registration_args(inputs)
        for field, value in changes.items():
            setattr(account, field, value)
        await self.session.flush()
        return {
            "account_id": str(account.id), "registration_type": account.registration_type,
            "message": f"Updated registration for {account.name}.",
        }

    async def _beneficiary_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        name = inputs.get("beneficiary_name")
        percentage = inputs.get("percentage")
        if not name or percentage is None:
            raise ExecutorError("beneficiary_name and percentage are required")
        account = await self._account(inputs)
        designation_class = inputs.get("designation_class") or "primary"
        if designation_class not in ("primary", "contingent"):
            raise ExecutorError("designation_class must be primary or contingent")
        try:
            share = float(percentage)
        except (TypeError, ValueError):
            raise ExecutorError("percentage must be a number.")
        if not 0 < share <= 100:
            raise ExecutorError("percentage must be above 0 and at most 100.")
        return {"account": account, "name": name, "share": share, "designation_class": designation_class,
                "relationship": inputs.get("relationship_to_owner"),
                "per_stirpes": str(inputs.get("per_stirpes", "")).lower() == "true"}

    async def _beneficiary_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.models.graph import AccountBeneficiary

        a = await self._beneficiary_args(inputs)
        others = (await self.session.execute(select(AccountBeneficiary.percentage).where(
            AccountBeneficiary.account_id == a["account"].id,
            AccountBeneficiary.designation_class == a["designation_class"]))).scalars().all()
        now = sum(float(p) for p in others)
        return [("Account", a["account"].name), ("Beneficiary", a["name"]), ("Share", f"{a['share']:g}%"),
                ("Class", a["designation_class"].title()), ("Relationship", a["relationship"]),
                ("Per stirpes", "Yes" if a["per_stirpes"] else None),
                (f"{a['designation_class'].title()} total", f"{now:g}% becomes {now + a['share']:g}%")]

    async def add_account_beneficiary(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.core.db import utcnow
        from app.models.graph import AccountBeneficiary

        a = await self._beneficiary_args(inputs)
        row = AccountBeneficiary(
            firm_id=self.firm_id, account_id=a["account"].id, beneficiary_name=a["name"],
            percentage=a["share"], designation_class=a["designation_class"],
            relationship_to_owner=a["relationship"], per_stirpes=a["per_stirpes"],
            date_designated=utcnow().date(),
        )
        self.session.add(row)
        await self.session.flush()

        return {
            "beneficiary_id": str(row.id), "beneficiary_name": row.beneficiary_name,
            "percentage": float(row.percentage),
            "message": f"Added {row.beneficiary_name} as a {a['designation_class']} beneficiary ({a['share']:g}%) on {a['account'].name}.",
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

    @staticmethod
    def _proposed_trades(rec) -> dict[str, Any]:
        """The exact orders a proposal would place, and what it says they do. Nothing for a proposal
        that places none. A person's modified version is the one that would be placed."""
        payload = rec.modified_payload or rec.payload or {}
        orders = payload.get("order_set") or []
        if not orders:
            return {}
        return {
            "orders": [{"side": o.get("side"), "symbol": o.get("symbol"), "quantity": o.get("quantity"),
                        "est_value": o.get("est_value"), "est_realised_gain": o.get("est_realised_gain"),
                        "account_id": o.get("account_id"), "reason": o.get("reason")} for o in orders],
            "estimated_realised_gain": payload.get("estimated_realised_gain"),
            "cgt_budget": payload.get("cgt_budget"),
        }

    async def read_recommendations(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The proposals waiting for a decision, firm-scoped, with whether this role may decide
        each one. The answer comes from decision_rights, the same source decide_recommendation
        re-checks, so the card never offers a decision the server would refuse."""
        from sqlalchemy import case

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
            # A person names a proposal by its title as often as by its client, and a firm-wide one's
            # client is the firm, so the words are looked for in the title, the client and the agent.
            # Each word on its own, so "Hartley rebalance" finds "Rebalance The Hartley Family".
            from sqlalchemy import or_

            words = [w for w in re.findall(r"[\w'&.-]+", subject.lower()) if w not in _PROPOSAL_NOISE] or [subject]
            for w in words:
                stmt = stmt.where(or_(Recommendation.subject_label.ilike(f"%{w}%"), Recommendation.title.ilike(f"%{w}%"),
                                      Recommendation.agent_key.ilike(f"%{w}%")))
        total = (await self.session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        # What can still be decided comes first, so history never buries it.
        recs = (await self.session.execute(
            stmt.order_by(case((Recommendation.status == RecommendationStatus.PROPOSED, 0), else_=1),
                          Recommendation.priority.asc(), Recommendation.created_at.desc()).limit(limit)
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
                "summary": r.summary,
                **({"rationale": r.rationale} if len(recs) <= 3 else {}),
                **self._proposed_trades(r),
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
        batch_in = inputs.get("batch_id")
        if need == "book_batch" or batch_in:
            from app.aurea_core import book_batches

            if need != "book_batch":
                raise ExecutorError(f"{name} does not work on a book batch.")
            if not batch_in:
                raise ExecutorError(f"{name} works on one acquired book. Give a batch_id (read_book_batches).")
            try:
                batch = await book_batches.get(self.session, self.firm_id, _parse_uuid(batch_in, "batch_id"))
            except book_batches.BatchError:
                raise ExecutorError(f"Book batch {batch_in} not found or access denied.")
            status = getattr(batch.status, "value", batch.status)
            if status != "received":
                raise ExecutorError(f"The {batch.source_firm} book has already been reconciled ({status}).")
            tier, guardrails = await policy.resolve_tier(self.session, self.firm_id, key, None)
            return {"key": key, "name": name, "firm": firm, "mandate_type": None, "tier": str(tier),
                    "guardrails": guardrails, "subject": Subject("book_batch", batch.id, batch.source_firm)}
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

        from app.core.tools import ONE_CLIENT_VIEWS, OPEN_VIEWS
        from app.models.billing import Invoice
        from app.models.engagement import ClientReport, Meeting
        from app.models.governance import Recommendation
        from app.models.onboarding import BookIntegrationBatch, OnboardingCase

        view = str(inputs.get("view") or "").strip().lower()
        spec = OPEN_VIEWS.get(view)
        if spec is None:
            raise ExecutorError(f"There is no view called '{view}'. Views: {', '.join(OPEN_VIEWS)}.")
        route, label, roles = spec
        if self.role != UserRole.ADMIN and self.role not in roles:
            raise ExecutorError(f"The {label} view is not one this role opens.")

        seen = json.dumps([t.get("result") for t in (turn or [])], default=str)

        finder = {Household: "search_households", Recommendation: "read_recommendations", Invoice: "read_billing",
                  OnboardingCase: "read_onboarding_cases", Meeting: "read_meetings", ClientReport: "read_reports",
                  BookIntegrationBatch: "read_book_batches"}

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
        elif view == "book_batch":
            route = f"{route}/{await grounded('batch_id', BookIntegrationBatch)}"
        elif view == "report":
            route = f"{route}/{await grounded('report_id', ClientReport)}"
        elif view == "message_thread":
            route = f"{route}/{await grounded('household_id', Household)}"
        elif view in ONE_CLIENT_VIEWS and inputs.get("household_id"):
            household = await self.session.get(Household, await grounded("household_id", Household))
            route = f"{route}?household={household.id}"
            return {"view": route, "kind": view, "message": f"The {label} view is open beside the conversation, on {household.name} alone."}

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
            household_id = (await self._own_household(inputs["household_id"])).id
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

    async def _own_household(self, value: Any) -> Household:
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

        household = await self._own_household(inputs["household_id"]) if inputs.get("household_id") else None
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

    async def _report(self, inputs: dict[str, Any]):
        from app.aurea_core import reports

        try:
            return await reports.get(self.session, self.firm_id, _parse_uuid(inputs.get("report_id") or "", "report_id"))
        except reports.ReportError as exc:
            raise ExecutorError(str(exc))

    async def read_reports(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The Reports page's list and one report (api.engage.list_reports / get_report). Only this
        firm's reports, and a household is read back inside the firm before it filters anything."""
        from app.aurea_core import reports

        if inputs.get("report_id"):
            report = await self._report(inputs)
            view = (await reports.views(self.session, self.firm_id, [report], full=True))[0]
            return {"reports": [view], "shown": 1, "total": 1, "status": view["status"],
                    "drafts": int(view["status"] == "draft"), "client_ready": int(view["status"] == "client_ready"),
                    "awaiting_decision": int(view["awaiting_decision"]),
                    "message": f"'{report.title}' for {view['household_name']}, {view['n_sections']} section(s)."}
        status = str(inputs.get("status") or "all").strip().lower()
        if status not in reports.STATUSES:
            raise ExecutorError(f"There is no report status '{status}'. Use {', '.join(reports.STATUSES)}.")
        household_id = (await self._own_household(inputs["household_id"])).id if inputs.get("household_id") else None
        rows = await reports.for_firm(self.session, self.firm_id, status=status, household_id=household_id)
        all_views = await reports.views(self.session, self.firm_id, rows)
        shown = all_views[:self._limit(inputs.get("limit"), 20, 50)]
        drafts = sum(1 for v in all_views if v["status"] == "draft")
        ready = sum(1 for v in all_views if v["status"] == "client_ready")
        waiting = sum(1 for v in all_views if v["awaiting_decision"])
        return {
            "reports": shown, "shown": len(shown), "total": len(all_views), "status": status,
            "drafts": drafts, "client_ready": ready, "awaiting_decision": waiting,
            "message": (f"{len(all_views)} report(s)" + (f"; showing {len(shown)}" if len(shown) < len(all_views) else "")
                        + f". {drafts} draft, {ready} with clients, {waiting} awaiting a decision."),
        }

    async def _withdrawal(self, inputs: dict[str, Any]):
        """The report and the proposal that put it with the client, or why it cannot be taken back."""
        from app.aurea_core import reports

        report = await self._report(inputs)
        if str(report.status) != "client_ready":
            raise ExecutorError(f"'{report.title}' is not with the client.")
        rec = (await reports.proposals(self.session, self.firm_id)).get(str(report.id))
        if not reports.withdrawable(report, rec):
            raise ExecutorError(f"'{report.title}' has no approved proposal on record, so it cannot be withdrawn here.")
        return report, rec

    async def withdraw_report(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Roll back the proposal that published the report, through the same exactly-once,
        role-checked path the Recommendations page uses (app.atlas.runtime.rollback)."""
        from app.atlas.runtime import rollback
        from app.aurea_core import reports
        from app.core import decision_rights
        from app.models.identity import User

        report, rec = await self._withdrawal(inputs)
        try:
            decision_rights.check(self.role, rec.agent_key, "rollback")
        except decision_rights.DecisionForbidden as exc:
            raise ExecutorError(str(exc))
        user = await self.session.get(User, self.user_id)
        try:
            await rollback(self.session, firm=await self._firm(), recommendation=rec, actor_id=self.user_id,
                           actor_label=f"{user.full_name} ({user.role})", note=inputs.get("note"))
        except ValueError as exc:
            raise ExecutorError(str(exc))
        view = (await reports.views(self.session, self.firm_id, [report]))[0]
        return {"reports": [view], "shown": 1, "total": 1, "status": view["status"], "drafts": 1, "client_ready": 0,
                "awaiting_decision": 0, "message": f"'{report.title}' is withdrawn: {view['household_name']} can no longer read it."}

    async def read_messages(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The Messages page's inbox and one thread (api.engage.message_inbox / message_thread).
        Only this firm's, and it marks nothing read: that is for a person opening the thread."""
        from app.aurea_core import messages

        limit = self._limit(inputs.get("limit"), 20, 50)
        if inputs.get("household_id"):
            household = await self._own_household(inputs["household_id"])
            rows = await messages.thread(self.session, self.firm_id, household.id)
            shown = rows[-limit:]
            waiting = messages.unread(rows)
            return {
                "scope": "household", "household_id": str(household.id), "household_name": household.name,
                "messages": [messages.view(m) for m in shown], "shown": len(shown), "total": len(rows), "unread": waiting,
                "needs_reply": messages.reply_owed(rows),
                "message": (f"{len(rows)} message(s) with {household.name}"
                            + (f"; showing the latest {len(shown)}" if len(shown) < len(rows) else "")
                            + f". {waiting} unread."),
            }
        threads = await messages.inbox(self.session, self.firm_id)
        shown = threads[:limit]
        waiting = sum(t["unread"] for t in threads)
        owed = sum(1 for t in threads if t["needs_reply"])
        return {
            "scope": "firm", "threads": shown, "shown": len(shown), "total": len(threads),
            "unread": waiting, "needs_reply": owed,
            "message": (f"{len(threads)} client thread(s)" + (f"; showing {len(shown)}" if len(shown) < len(threads) else "")
                        + f". {waiting} unread, {owed} waiting for a reply."),
        }

    async def _client_message(self, inputs: dict[str, Any]):
        """The household and the words, checked. Shared by the Confirm card and the send."""
        from app.aurea_core import messages

        household = await self._own_household(inputs.get("household_id") or "")
        try:
            body = messages.clean(inputs.get("body"))
        except messages.MessageError as exc:
            raise ExecutorError(str(exc))
        from app.aurea_core import txn_monitoring

        try:
            await txn_monitoring.tipping_off(self.session, self.firm_id, household.id, body)
        except txn_monitoring.MonitoringError as exc:
            raise ExecutorError(str(exc))
        return household, body

    async def send_client_message(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import messages
        from app.models.identity import User

        household, body = await self._client_message(inputs)
        user = await self.session.get(User, self.user_id)
        sent = messages.from_firm(self.firm_id, household.id, body, author=user.full_name)
        self.session.add(sent)
        await self.session.flush()
        rows = await messages.thread(self.session, self.firm_id, household.id)
        return {"scope": "household", "household_id": str(household.id), "household_name": household.name,
                "messages": [messages.view(sent)], "shown": 1, "total": len(rows), "unread": messages.unread(rows),
                "needs_reply": False, "message": f"Sent to {household.name}."}

    @staticmethod
    def _ips_decision_preview(rec, inputs: dict[str, Any]) -> dict[str, Any]:
        """Accepting a breach names what is being set aside, and is refused here with no reason, where
        the adviser can still give one, rather than after the decision is recorded."""
        from datetime import date as _date

        from app.aurea_core import firm_time

        if str(rec.status) != "proposed":
            raise ExecutorError(f"Already {rec.status}")
        action, note = str(inputs.get("action") or ""), str(inputs.get("note") or "").strip()
        if action == "approve" and len(note) < 3:
            raise ExecutorError("Accepting a breach needs the reason. Ask the adviser for it.")
        until = (rec.payload or {}).get("accept_until")
        return {"rows": [{"label": k, "value": str(v)} for k, v in (
            ("Mandate", rec.subject_label), ("Breaches", rec.summary),
            ("Decision", f"Accept until {firm_time.spoken(_date.fromisoformat(until))}" if action == "approve" and until else action.title()),
            ("Reason", note),
        ) if v]}

    async def _proposal_preview(self, rec, action: str, note: str | None) -> list[tuple[str, Any]]:
        """Any proposal: which one, what deciding it does, and for one that trades, each order and what
        it leaves in cash. This is the card a person approves real orders on."""
        from app.aurea_core import licences, markets
        from app.conduit.execution import venue_for_firm
        from app.core import decision_rights

        if action not in ("approve", "modify", "dismiss"):
            raise ExecutorError("Revise re-runs the agent, so it is done on the Recommendations page. Here a proposal can be approved, modified or dismissed."
                                if action == "revise" else f"Invalid action: {action}. Must be approve, modify, or dismiss")
        try:
            decision_rights.check(self.role, rec.agent_key, action)
        except decision_rights.DecisionForbidden as exc:
            raise ExecutorError(str(exc))
        if str(rec.status) != "proposed":
            raise ExecutorError(f"Already {rec.status}")

        firm = await self._firm()
        say = markets.money_for(firm)
        trades = self._proposed_trades(rec)
        orders = trades.get("orders") or []
        mandate = await self.session.get(Mandate, rec.subject_id) if rec.subject_type == "mandate" and rec.subject_id else None
        track = licences.track_for(firm, mandate) if orders and mandate else None
        waits = track is not None and (track.per_trade_consent or not track.may_execute)
        decision = {"dismiss": "Dismiss: nothing is placed and the proposal closes",
                    "modify": "Approve as modified",
                    "approve": ("Approve: the orders wait for the client's consent" if waits
                                else "Approve: the order is placed now" if len(orders) == 1
                                else f"Approve: {len(orders)} orders are placed now" if orders else "Approve")}[action]
        rows: list[tuple[str, Any]] = [("Decision", decision), ("Proposal", rec.title), ("Client", rec.subject_label)]
        if not orders:
            return [*rows, ("Agent", str(rec.agent_key).replace("_", " ")), ("Note", (note or "").strip() or None)]

        for n, o in enumerate(orders, 1):
            units = f"{float(o['quantity'] or 0):,.3f}".rstrip("0").rstrip(".")
            rows.append((f"Order {n}", f"{str(o['side']).title()} {units} {o['symbol']}, {say(float(o['est_value'] or 0))}"))
        accounts = {uuid.UUID(str(o["account_id"])) for o in orders if o.get("account_id")}
        cash = float((await self.session.execute(
            select(func.coalesce(func.sum(Account.cash_balance), 0)).where(
                Account.firm_id == self.firm_id, Account.id.in_(list(accounts))))).scalar_one())
        moved = sum(float(o["est_value"] or 0) * (1 if o["side"] == "sell" else -1) for o in orders)
        gain, budget = trades.get("estimated_realised_gain"), trades.get("cgt_budget")
        venue = venue_for_firm(firm)
        return [*rows, ("Cash, before fees", f"{say(cash)} becomes {say(cash + moved)}"),
                ("Realised gain", f"{say(float(gain))} of a {say(float(budget))} budget" if gain is not None and budget is not None
                 else say(float(gain)) if gain is not None else None),
                ("Venue", venue.key if venue.reaches_market else "Paper: real prices and book entries, no market reached"),
                ("Note", (note or "").strip() or None)]

    async def _report_decision_preview(self, inputs: dict[str, Any]) -> dict[str, Any] | None:
        """A decision on a report's proposal names the report, because approving it puts it in front
        of the client. Every other proposal names itself. An id that resolves to none of this firm's
        proposals is refused, so a card never asks someone to approve nothing it can name."""
        from app.aurea_core import reports
        from app.models.enums import AgentKey
        from app.models.governance import Recommendation

        def card(pairs) -> dict[str, Any]:
            return {"rows": [{"label": k, "value": str(v)} for k, v in pairs if v]}

        rec = await self.session.get(Recommendation, _parse_uuid(inputs.get("recommendation_id") or "", "recommendation_id"))
        if rec is None or rec.firm_id != self.firm_id:
            raise ExecutorError("That is not a proposal on file here. Look it up with read_recommendations this turn and use its id.")
        if rec is not None and rec.firm_id == self.firm_id and str(rec.agent_key) == AgentKey.IPS_MONITORING.value:
            return self._ips_decision_preview(rec, inputs)
        if rec is not None and rec.firm_id == self.firm_id and str(rec.agent_key) == AgentKey.PROTECTION_PLANNING.value:
            if str(rec.status) != "proposed":
                raise ExecutorError(f"Already {rec.status}")
            action = str(inputs.get("action") or "")
            return {"rows": [{"label": k, "value": str(v)} for k, v in (
                ("Client", rec.subject_label), ("Gaps", rec.summary),
                ("Decision", "Approve, and add a task to review this cover" if action == "approve" else action.title()),
            ) if v]}
        if rec is None or rec.firm_id != self.firm_id:
            return None
        action, note = str(inputs.get("action") or ""), inputs.get("note")
        if str(rec.agent_key) != AgentKey.RESEARCH_REPORTING.value:
            return card(await self._proposal_preview(rec, action, note))
        report = await reports.of_proposal(self.session, self.firm_id, rec.payload)
        if report is None:
            return card(await self._proposal_preview(rec, action, note))
        if str(rec.status) != "proposed":
            raise ExecutorError(f"Already {rec.status}")
        household = await self.session.get(Household, report.household_id)
        action = str(inputs.get("action") or "")
        return {"rows": [{"label": k, "value": str(v)} for k, v in (
            ("Report", report.title), ("Client", household.name if household else None), ("Period", report.period),
            ("Decision", "Approve, and the client can read it" if action == "approve" else action.title()),
        ) if v]}

    async def preview_change(self, tool_key: str, inputs: dict[str, Any]) -> dict[str, Any] | None:
        """What a Confirm card names, so a person confirms this task and not "a task": the record
        and the change, read now, as label and value rows. Raises what executing would raise, so
        a change that would only fail is refused instead of offered. None for a tool without one."""
        def rows(*pairs: tuple[str, Any]) -> dict[str, Any]:
            return {"rows": [{"label": k, "value": str(v)} for k, v in pairs if v]}

        from app.aurea_core import firm_time, tasks

        if tool_key == "decide_recommendation":
            return await self._report_decision_preview(inputs)
        if tool_key in ("resolve_surveillance_flag", "escalate_surveillance_flag", "evaluate_agents"):
            return rows(*await self._supervision_preview(tool_key, inputs))
        if tool_key in ("create_skill", "update_skill", "delete_skill", "clone_skill"):
            return rows(*await self._skill_preview(tool_key, inputs))
        if tool_key == "start_book_batch":
            return rows(*await self._batch_preview(inputs))
        if tool_key == "run_skill":
            return rows(*await self._run_skill_preview(inputs))
        if tool_key == "withdraw_report":
            report, _ = await self._withdrawal(inputs)
            household = await self.session.get(Household, report.household_id)
            return rows(("Report", report.title), ("Client", household.name if household else None),
                        ("Period", report.period),
                        ("Published", firm_time.spoken(firm_time.today(await self._firm(), report.published_at))
                         if report.published_at else None))
        if tool_key == "send_client_message":
            from app.models.identity import User

            household, body = await self._client_message(inputs)
            sender = await self.session.get(User, self.user_id)
            return rows(("To", household.name), ("From", sender.full_name if sender else None), ("Message", body))
        if tool_key in ("record_client_review", "set_review_cadence", "book_client_review"):
            return rows(*await self._review_preview(tool_key, inputs))
        if tool_key == "record_ips":
            return rows(*await self._ips_preview(inputs))
        if tool_key in ("record_protection_policy", "end_protection_policy", "record_income", "set_care_assumptions"):
            return rows(*await self._protection_preview(tool_key, inputs))
        if tool_key == "decide_risk_profile":
            return rows(*await self._risk_preview(inputs))
        if tool_key in ("record_balance_item", "end_balance_item"):
            return rows(*await self._balance_preview(tool_key, inputs))
        if tool_key in ("request_cash_movement", "record_cash_paid", "cancel_cash_request", "record_standing_instruction", "end_standing_instruction", "start_phase_in", "end_phase_in"):
            return rows(*await self._cash_preview(tool_key, inputs))
        if tool_key in ("record_trusted_contact", "record_estate_document", "record_life_event"):
            return rows(*await self._file_preview(tool_key, inputs))
        if tool_key in ("record_income_stream", "end_income_stream", "record_education_goal"):
            return rows(*await self._plan_preview(tool_key, inputs))
        if tool_key in ("record_equity_grant", "end_equity_grant", "record_concentration_plan"):
            return rows(*await self._equity_preview(tool_key, inputs))
        if tool_key in ("record_product_decision", "record_investor_status", "set_approved_products_only"):
            return rows(*await self._shelf_preview(tool_key, inputs))
        if tool_key in ("start_client_departure", "record_client_death", "record_executor", "record_account_transfer", "close_client_exit", "withdraw_client_exit"):
            return rows(*await self._exit_preview(tool_key, inputs))
        if tool_key in ("create_crm_contact", "create_crm_opportunity", "log_crm_activity", "update_crm_opportunity_stage"):
            return rows(*await self._crm_preview(tool_key, inputs))
        if tool_key in ("compute_corporate_action_entitlements", "post_corporate_action_entitlement"):
            return rows(*await self._corporate_preview(tool_key, inputs))
        if tool_key == "record_consent":
            return rows(*await self._consent_registry_preview(inputs))
        if tool_key == "record_registration_detail":
            return rows(*await self._case_registration_preview(inputs))
        if tool_key == "set_custodian_documents":
            return rows(*await self._custodian_documents_preview(inputs))
        if tool_key == "record_transfer_event":
            return rows(*await self._transfer_event_preview(inputs))
        if tool_key == "record_rollover_facts":
            return rows(*await self._rollover_preview(inputs))
        if tool_key == "decide_monitoring_alert":
            return rows(*await self._alert_preview(inputs))
        if tool_key == "record_investigation_step":
            return rows(*await self._step_preview(inputs))
        if tool_key == "record_bank_link":
            return rows(*await self._bank_link_preview(inputs))
        if tool_key == "record_deposit":
            return rows(*await self._deposit_preview(inputs))
        if tool_key == "revalidate_standing_instruction":
            return rows(*await self._revalidation_preview(inputs))
        if tool_key == "request_account_feature":
            return rows(*await self._feature_request_preview(inputs))
        if tool_key == "decide_account_feature":
            return rows(*await self._feature_decision_preview(inputs))
        if tool_key == "record_senior_hold":
            return rows(*await self._senior_hold_preview(inputs))
        if tool_key == "record_onboarding_feedback":
            return rows(*await self._feedback_preview(inputs))
        if tool_key == "record_private_transfer_item":
            return rows(*await self._private_preview(inputs))
        if tool_key == "start_repapering":
            return rows(*await self._repapering_start_preview(inputs))
        if tool_key == "record_repapering":
            return rows(*await self._repapering_step_preview(inputs))
        if tool_key == "rate_aml_risk":
            return rows(*await self._risk_rating_preview(inputs))
        if tool_key in ("record_screening_decision", "sign_off_edd"):
            return rows(*await self._aml_preview(tool_key, inputs))
        named = {"record_tax_document": self._tax_document_preview, "execute_orders": self._orders_preview, "credit_invoice": self._credit_preview,
                 "record_valuation": self._valuation_preview, "record_client_consent": self._consent_preview,
                 "refresh_prices": self._refresh_preview, "run_billing": self._billing_preview,
                 "set_account_registration": self._registration_preview, "add_account_beneficiary": self._beneficiary_preview,
                 "update_goal": self._goal_preview, "mark_conflict_reviewed": self._conflict_preview,
                 "log_wsp_evidence": self._wsp_preview}
        if tool_key in named:
            return rows(*await named[tool_key](inputs))
        if tool_key in ("import_custody_statement", "resolve_recon_break"):
            return rows(*await self._recon_preview(tool_key, inputs))
        if tool_key not in ("complete_task", "assign_task", "create_task"):
            return None

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

    async def _supervised_flag(self, inputs: dict[str, Any], roles):
        """The flag, refused for a role supervision is not for, as the route refuses it."""
        from app.provenance import supervision

        if self.role not in roles:
            raise ExecutorError("Supervision is for compliance or a branch leader.")
        try:
            return await supervision.flag(self.session, self.firm_id, _parse_uuid(inputs.get("flag_id") or "", "flag_id"))
        except supervision.SupervisionError as exc:
            raise ExecutorError(str(exc))

    async def _supervision_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """What a supervision Confirm card names: the flag and what is said about it, or the agents a
        re-run would narrow. Refused for the reasons doing it would be refused."""
        from app.provenance import supervision

        try:
            if tool_key == "evaluate_agents":
                if self.role not in supervision.EVALUATORS:
                    raise ExecutorError("Re-running the evaluation is for compliance, a branch leader or an administrator.")
                narrowed = await supervision.would_narrow(self.session, self.firm_id)
                return [("Agents scored", str(len(supervision.CATALOGUE))),
                        ("Would narrow", "; ".join(
                            f"{n['agent']}: {'paused' if n['paused'] else n['from'].replace('tier_', 'Tier ') + ' to ' + n['to'].replace('tier_', 'Tier ')}"
                            for n in narrowed) or "None on today's record"),
                        ("Widens", "Never")]
            row = await self._supervised_flag(inputs, supervision.SUPERVISORS)
            common = [("Flag", f"{row.severity} · {row.category}"), ("Agent", supervision.agent_name(row.target_agent_key)),
                      ("Finding", row.finding)]
            if tool_key == "resolve_surveillance_flag":
                note = supervision.check_resolve(row, inputs.get("note"))
                return [*common, ("Reason", note), ("Paused agent", "Stays paused; unpause it in Admin" if row.auto_paused_agent else None)]
            to, note = supervision.check_escalate(row, inputs.get("escalated_to"), inputs.get("note"))
            return [*common, ("To", to), ("Reason", note), ("Already escalated to", row.escalated_to)]
        except supervision.SupervisionError as exc:
            raise ExecutorError(str(exc))

    async def _supervisor(self) -> str:
        from app.models.identity import User

        user = await self.session.get(User, self.user_id)
        return user.email if user else "unknown"

    async def resolve_surveillance_flag(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.provenance import supervision

        row = await self._supervised_flag(inputs, supervision.SUPERVISORS)
        try:
            result = await supervision.resolve(self.session, row, note=inputs.get("note") or "", by=await self._supervisor())
        except supervision.SupervisionError as exc:
            raise ExecutorError(str(exc))
        return {**result, "message": "Resolved." + (" The agent it paused stays paused until someone unpauses it in Admin."
                                                    if result["agent_still_paused"] else "")}

    async def escalate_surveillance_flag(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.provenance import supervision

        row = await self._supervised_flag(inputs, supervision.SUPERVISORS)
        try:
            result = await supervision.escalate(self.session, row, to=inputs.get("escalated_to") or "",
                                                note=inputs.get("note") or "", by=await self._supervisor())
        except supervision.SupervisionError as exc:
            raise ExecutorError(str(exc))
        return {**result, "message": f"Escalated to {result['escalated_to']}. The flag stays open."}

    async def evaluate_agents(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.provenance import supervision
        from app.provenance.evaluation import evaluate_firm

        if self.role not in supervision.EVALUATORS:
            raise ExecutorError("Re-running the evaluation is for compliance, a branch leader or an administrator.")
        result = await evaluate_firm(self.session, self.firm_id)
        agents = [{"agent_key": a["agent_key"], "agent_name": supervision.agent_name(str(a["agent_key"])),
                   "quality_score": a["quality_score"], "grade": a["grade"], "sample_size": a["sample_size"]}
                  for a in result["agents"]]
        return {"evaluated": result["evaluated"], "autonomy_changes": result["autonomy_changes"], "evaluations": agents,
                "message": f"{result['evaluated']} agent(s) scored; {result['autonomy_changes']} narrowed."}

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

    # ─── The Studio pages that had no tool (B-94): each read returns what its page's route returns.
    async def _owned_household(self, inputs: dict[str, Any]) -> Household:
        hid = _parse_uuid(inputs.get("household_id") or "", "household_id")
        household = (await self.session.execute(
            select(Household).where(Household.id == hid, Household.firm_id == self.firm_id))).scalar_one_or_none()
        if household is None:
            raise ExecutorError(f"Household {hid} not found or access denied")
        return household

    async def read_tax_book(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import tax_intelligence

        firm = await self._firm()
        result = await tax_intelligence.for_firm(self.session, self.firm_id, firm.jurisdiction)
        return {**result, "message": f"The book's tax position across {result.get('total_households_scanned', 0)} household(s). "
                                     "Every figure is an estimate for a qualified person to check."}

    async def read_estate(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import estate

        household = await self._owned_household(inputs)
        result = await estate.for_household(self.session, household.id, firm_id=self.firm_id)
        risk = result["succession_risk"]
        return {**result, "message": f"{household.name}: {risk['gaps_count']} succession gap(s), {risk['level']} risk."}

    async def read_family(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import family

        household = await self._owned_household(inputs)
        result = await family.for_anchor(self.session, self.firm_id, household_id=household.id)
        return {**result, "household_id": str(household.id),
                "message": f"{household.name}'s family spans {result['household_count']} household(s)."}

    async def read_behavioural(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import behavioural

        household = await self._owned_household(inputs)
        result = await behavioural.for_household(self.session, household.id, firm_id=self.firm_id)
        if result is None:
            raise ExecutorError(f"Household {household.id} not found or access denied")
        return {**result, "message": f"{household.name}'s behavioural profile."}

    async def read_regulatory_countdown(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import regulatory_countdown

        firm = await self._firm()
        if inputs.get("household_id"):
            household = await self._owned_household(inputs)
            result = await regulatory_countdown.for_household(self.session, household.id, firm.jurisdiction, firm_id=self.firm_id)
            return {**result, "message": result.get("message") or f"{household.name}: {len(result['analyses'])} countdown(s)."}
        results = await regulatory_countdown.for_firm(self.session, self.firm_id, firm.jurisdiction)
        return {"results": results, "total": len(results),
                "message": f"{len(results)} household(s) with a regulatory countdown."}

    async def _me(self):
        from app.models.identity import User

        user = await self.session.get(User, self.user_id)
        if user is None or user.firm_id != self.firm_id:
            raise ExecutorError("Your user could not be found.")
        return user

    async def _skill_fields(self, inputs: dict[str, Any], user) -> dict[str, Any]:
        from app.skills import library

        fields = {k: inputs.get(k) for k in library.FIELDS if inputs.get(k) not in (None, "")}
        if "enabled" in fields:
            fields["enabled"] = str(fields["enabled"]).strip().lower() in ("true", "yes", "1", "on")
        if "shared_with" in fields:
            given = fields["shared_with"] if isinstance(fields["shared_with"], list) else str(fields["shared_with"]).split(",")
            fields["shared_with"] = await library.resolve_colleagues(self.session, self.firm_id, user, given)
            fields.setdefault("visibility", "shared")
        return fields

    async def read_skills(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.skills import library

        skills = await library.list_for(self.session, self.firm_id, await self._me())
        mine = sum(1 for s in skills if s["mine"])
        return {"skills": skills, "message": f"{len(skills)} skill(s) you can see, {mine} of them yours."}

    async def _skill_change(self, tool_key: str, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.skills import library

        user = await self._me()
        names = await library.owners(self.session, self.firm_id)
        try:
            if tool_key == "create_skill":
                s = await library.create(self.session, self.firm_id, user, await self._skill_fields(inputs, user))
                verb = "Created"
            elif tool_key == "update_skill":
                s = await library.update(self.session, self.firm_id, _parse_uuid(inputs.get("skill_id") or "", "skill_id"),
                                         user, await self._skill_fields(inputs, user))
                verb = "Changed"
            elif tool_key == "clone_skill":
                s = await library.clone(self.session, self.firm_id, _parse_uuid(inputs.get("skill_id") or "", "skill_id"), user)
                verb = "Copied into your skills as"
            else:
                gone = await library.delete(self.session, self.firm_id, _parse_uuid(inputs.get("skill_id") or "", "skill_id"), user)
                return {"deleted": gone, "message": f"Deleted the skill {gone['name']}."}
        except library.SkillError as exc:
            raise ExecutorError(str(exc))
        return {"skill": library.as_dict(s, user, names), "message": f"{verb} the skill {s.name}."}

    async def create_skill(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._skill_change("create_skill", inputs)

    async def update_skill(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._skill_change("update_skill", inputs)

    async def delete_skill(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._skill_change("delete_skill", inputs)

    async def clone_skill(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self._skill_change("clone_skill", inputs)

    async def _skill_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """The skill and the change, checked as the change would check it, without writing."""
        from app.skills import library

        user = await self._me()
        try:
            if tool_key == "create_skill":
                f = library.check(await self._skill_fields(inputs, user))
                if "name" not in f or "instruction" not in f:
                    raise library.SkillError("A skill needs a name and an instruction.")
                return [("Skill", f["name"]), ("Instruction", f["instruction"]), ("Description", f.get("description")),
                        ("Scope", f.get("scope", "book")), ("Output", f.get("output_kind", "insight")),
                        ("Tier", library.TIERS.index(f.get("default_tier", "tier_1")) + 1),
                        ("Visible to", await self._skill_audience(f, user))]
            sid = _parse_uuid(inputs.get("skill_id") or "", "skill_id")
            if tool_key == "clone_skill":
                s = await library.get(self.session, self.firm_id, sid)
                if not library.visible(s, user):
                    raise library.SkillForbidden("You don't have access to this skill.")
                return [("Skill", s.name), ("Copy", f"{s.name} (copy)"), ("Visible to", "Only you")]
            s = await library.owned(self.session, self.firm_id, sid, user, "edit" if tool_key == "update_skill" else "delete")
            if tool_key == "delete_skill":
                return [("Skill", s.name), ("Instruction", s.instruction), ("Proposals it raised", "Stay")]
            f = library.check(await self._skill_fields(inputs, user))
            if not f:
                raise library.SkillError("Name what to change.")
            shown = {"name": "Name", "instruction": "Instruction", "description": "Description", "scope": "Scope",
                     "output_kind": "Output", "default_tier": "Tier", "enabled": "Enabled", "visibility": "Visibility"}
            rows: list[tuple[str, Any]] = [("Skill", s.name)]
            for k, label in shown.items():
                if k in f:
                    rows.append((label, f"{getattr(s, k)} → {f[k]}"))
            if "shared_with" in f:
                rows.append(("Visible to", await self._skill_audience({**f, "visibility": f.get("visibility", s.visibility)}, user)))
            return rows
        except library.SkillError as exc:
            raise ExecutorError(str(exc))

    async def _skill_to_run(self, inputs: dict[str, Any]):
        """The skill, visible to the caller and enabled, and the household it is pointed at if any."""
        from app.skills import library

        user = await self._me()
        try:
            s = await library.get(self.session, self.firm_id, _parse_uuid(inputs.get("skill_id") or "", "skill_id"))
        except library.SkillError as exc:
            raise ExecutorError(str(exc))
        if not library.visible(s, user):
            raise ExecutorError("You don't have access to this skill.")
        if not s.enabled:
            raise ExecutorError(f"The skill {s.name} is disabled; enable it first.")
        household = await self._owned_household(inputs) if inputs.get("household_id") else None
        return s, household

    async def run_skill(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.skills import runtime as skill_runtime

        s, household = await self._skill_to_run(inputs)
        result = await skill_runtime.run_skill(self.session, await self._firm(), s,
                                               subject_type="household" if household else None,
                                               subject_id=household.id if household else None, persist=True)
        return {"skill": s.name, "run_id": result["run_id"], "scanned": result["scanned"], "surfaced": result["surfaced"],
                "proposals": [{"recommendation_id": rid, "household": p["household"], "household_id": p["household_id"], "title": p["title"]}
                              for rid, p in zip(result["recommendation_ids"], result["proposals"])],
                "message": f"The skill {s.name} looked at {result['scanned']} household(s) and proposed {result['surfaced']} thing(s) "
                           "for a person to decide. It acted on nothing."}

    async def _run_skill_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core.graph import list_households

        s, household = await self._skill_to_run(inputs)
        over = household.name if household else f"Every client ({len(await list_households(self.session, self.firm_id))} households)"
        return [("Skill", s.name), ("Instruction", s.instruction), ("Runs over", over),
                ("Tier", "2 · supervised" if s.default_tier in ("tier_2", "tier_3") else "1 · assistive"),
                ("What it does", "Proposes, for a person to decide; it acts on nothing")]

    async def _skill_audience(self, f: dict, user) -> str:
        from app.skills import library

        visibility = f.get("visibility", "private")
        if visibility == "public":
            return "Everyone in the firm"
        if visibility == "shared":
            names = await library.owners(self.session, self.firm_id)
            people = [names.get(uuid.UUID(x), "—") for x in f.get("shared_with") or []]
            return "You and " + ", ".join(people) if people else "Only you"
        return "Only you"

    async def read_book_batches(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import book_batches

        if inputs.get("batch_id"):
            try:
                batch = await book_batches.one(self.session, self.firm_id, _parse_uuid(inputs["batch_id"], "batch_id"))
            except book_batches.BatchError:
                raise ExecutorError(f"Book batch {inputs['batch_id']} not found or access denied")
            sample = " It is the generated sample book, not a real acquisition." if batch["sample"] else ""
            return {"batch": batch, "message": f"The {batch['source_firm']} book is {batch['status']}.{sample}"}
        batches = await book_batches.list_for(self.session, self.firm_id)
        return {"batches": batches, "message": f"{len(batches)} acquired book(s)."}

    async def start_book_batch(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import book_batches

        try:
            b = await book_batches.start(self.session, self.firm_id, str(inputs.get("source_firm") or ""))
        except book_batches.BatchError as exc:
            raise ExecutorError(str(exc))
        return {"batch": {**book_batches.as_dict(b), "recommendation_id": None, "recommendation_status": None},
                "message": f"Loaded the sample book as an acquisition from {b.source_firm}. Run book integration on it to map it."}

    async def _batch_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core.sample_book import sample_feed

        name = str(inputs.get("source_firm") or "").strip()
        if not name:
            raise ExecutorError("Name the firm the book comes from.")
        feed = sample_feed(name)
        return [("From", name), ("Feed", "Sample book - generated, not a real file"),
                ("Clients", len(feed.get("clients") or [])), ("Holdings", len(feed.get("holdings") or [])),
                ("Changes records", "Nothing until its proposal is approved")]

    async def screen_book(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The whole book, a row per household, filtered (aurea_core/book_screen.py)."""
        from app.aurea_core import book_screen

        only = inputs.get("only") or []
        if isinstance(only, str):
            only = [x for x in only.split(",")]
        limit = inputs.get("limit")
        try:
            return await book_screen.screen(self.session, self.firm_id, only=list(only),
                                            limit=int(limit) if limit else None)
        except book_screen.ScreenError as exc:
            raise ExecutorError(str(exc))

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

    async def _order_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """The mandate, its account and the order set, everything executing would refuse refused. Runs
        for the card and again at Confirm, because the book or the IPS can move between the two."""
        from app.aurea_core import order_guard
        from app.models.tenant import Firm

        mandate_id = _parse_uuid(inputs.get("mandate_id") or "", "mandate_id")
        orders_in = inputs.get("orders") or []
        reason = inputs.get("reason") or ""

        if not orders_in:
            raise ExecutorError("orders is required and must be a non-empty list")
        if len(reason.strip()) < 3:
            raise ExecutorError("Give the reason for this order. It is the record of why it was placed.")

        mandate = await self.session.get(Mandate, mandate_id)
        if not mandate or mandate.firm_id != self.firm_id:
            raise ExecutorError(f"Mandate {mandate_id} not found or access denied")

        # A direct order skips the proposal, so it skips the client's consent. Only a track that
        # needs none may use it.
        from app.aurea_core import licences
        firm = await self.session.get(Firm, self.firm_id)
        track = licences.track_for(firm, mandate)
        if track is not None and (track.per_trade_consent or not track.may_execute):
            raise ExecutorError(f"{track.name}: a trade on this mandate goes through a proposal and the "
                                "client's consent, not a direct order.")

        accounts = (
            await self.session.execute(select(Account).where(Account.mandate_id == mandate_id))
        ).scalars().all()
        if not accounts:
            raise ExecutorError(f"Mandate {mandate_id} has no account to trade in")
        # Simplification: trade against the mandate's first account. Mandates with
        # multiple accounts (rare) need an account_id per order — not yet supported
        # from conversation.
        account = accounts[0]

        order_set, unpriced, instruments = [], [], {}
        for o in orders_in:
            symbol = o.get("symbol")
            side = str(o.get("side", "buy")).strip().lower()
            quantity = o.get("quantity")
            if not symbol or not quantity:
                raise ExecutorError("Each order needs a symbol and quantity")
            if side not in ("buy", "sell"):
                raise ExecutorError(f"An order is a buy or a sell, not '{side}'.")
            try:
                units = float(quantity)
            except (TypeError, ValueError):
                raise ExecutorError(f"{symbol}: the quantity must be a number.")
            if units <= 0:
                raise ExecutorError(f"{symbol}: the quantity must be above zero.")

            inst = (
                await self.session.execute(
                    select(Instrument).where(Instrument.firm_id == self.firm_id, Instrument.symbol == symbol)
                )
            ).scalar_one_or_none()
            if not inst:
                raise ExecutorError(f"Unknown instrument symbol: {symbol}")
            instruments[symbol] = inst

            price_row = (
                await self.session.execute(
                    select(Price).where(Price.instrument_id == inst.id).order_by(Price.as_of.desc()).limit(1)
                )
            ).scalar_one_or_none()
            est_price = float(price_row.close) if price_row else 0.0
            est_value = est_price * units
            # The venue fills only on a real price, so an order on any other would not fill.
            if price_row is None or not price_row.is_real:
                unpriced.append(symbol)

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
        if len(unpriced) == len(order_set):
            raise ExecutorError(f"None of these could fill: there is no real price for {', '.join(unpriced)}.")
        review = await order_guard.review(self.session, firm=firm, mandate=mandate, account=account, orders=order_set,
                                          instruments=instruments, unpriced=unpriced)
        if review["refused"]:
            raise ExecutorError(f"Nothing can be placed. {' '.join(review['refused'])}")
        return {"mandate": mandate, "account": account, "accounts": len(accounts), "order_set": order_set,
                "unpriced": unpriced, "review": review, "reason": reason}

    async def _orders_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        """Orders place at once and a fill can only be offset, so the card shows each one, its estimated
        gain, what it does to the cash, and what the guard has to say short of refusing."""
        from app.aurea_core import markets
        from app.conduit.execution import venue_for_firm

        a = await self._order_args(inputs)
        firm = await self._firm()
        say = markets.money_for(firm)
        orders, account = a["order_set"], a["account"]
        where = f"{account.name}{f' · {account.custodian}' if account.custodian else ''}"
        rows: list[tuple[str, Any]] = [
            ("Mandate", a["mandate"].name),
            ("Account", f"{where} (the first of {a['accounts']})" if a["accounts"] > 1 else where)]
        for n, o in enumerate(orders, 1):
            at = (f"at about {markets.money(o['est_price'], firm.base_currency, dp=2)}, {say(o['est_value'])}"
                  if o["symbol"] not in a["unpriced"] else "no real price")
            gain = o.get("est_realised_gain")
            result = f", {'loss' if gain < 0 else 'gain'} about {say(abs(gain))}" if gain is not None else ""
            rows.append((f"Order {n}", f"{o['side'].title()} {float(o['quantity']):g} {o['symbol']} {at}{result}"))
        filling = [o for o in orders if o["symbol"] not in a["unpriced"]]
        buys = sum(o["est_value"] for o in filling if o["side"] == "buy")
        sells = sum(o["est_value"] for o in filling if o["side"] == "sell")
        cash = float(account.cash_balance or 0)
        rows.append(("Cash, before fees", f"{say(cash)} becomes {say(cash + sells - buys)}"))
        if a["unpriced"]:
            rows.append(("Will not fill", ", ".join(a["unpriced"])))
        policy = a["review"]["policy"]
        if policy:
            rows.append(("IPS", f"Version {policy.version}: breaches no term and widens no breach"))
        if a["review"]["concentrated"]:
            rows.append(("Concentration", ", ".join(a["review"]["concentrated"]) + " of the book"))
        if a["review"]["watched"]:
            rows.append(("Under watch", ", ".join(a["review"]["watched"])))
        for flag in a["review"]["flags"]:
            rows.append((f"Review {flag['symbol']}", f"Sold at a loss in {flag['account']} on {flag['on']}"))
        venue = venue_for_firm(firm)
        rows.append(("Venue", venue.key if venue.reaches_market else "Paper: real prices and book entries, no market reached"))
        rows.append(("Reason", a["reason"].strip() or None))
        return rows

    async def execute_orders(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Submit buy/sell orders on a mandate's first account, via the same order
        engine (draft -> staged -> placed -> filled -> settled) the drift-rebalancing
        agent uses — real prices, real book entries, honest about a paper venue."""
        from app.conduit.execution import execute_order_set
        from app.models.tenant import Firm
        from app.provenance import ledger

        a = await self._order_args(inputs)
        mandate_id, order_set = a["mandate"].id, a["order_set"]
        firm = await self.session.get(Firm, self.firm_id)
        result = await execute_order_set(
            self.session, firm=firm, order_set=order_set, mandate_id=mandate_id, actor=str(self.role),
        )
        # A direct order has no proposal to carry its record, so the ledger does.
        await ledger.append_entry(self.session, firm_id=self.firm_id, event_type="direct_order_placed", actor=await self._shelf_user(), content={
            "mandate_id": str(mandate_id), "reason": a["reason"], "orders": [{"side": o["side"], "symbol": o["symbol"], "quantity": float(o["quantity"]), "est_value": o["est_value"]} for o in order_set],
            "settled": result["orders_settled"], "failed": result["orders_failed"], "venue": result["venue"], "role": str(self.role)})

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

    async def _goal_changes(self, inputs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        """The goal and what would change on it, checked before any of it is written."""
        from datetime import date

        goal_id = _parse_uuid(inputs.get("goal_id") or "", "goal_id")
        target = inputs.get("target")
        timeline = inputs.get("timeline")
        priority = inputs.get("priority")

        goal = await self.session.get(Goal, goal_id)
        if not goal or goal.firm_id != self.firm_id:
            raise ExecutorError(f"Goal {goal_id} not found or access denied")

        changes: dict[str, Any] = {}
        if target is not None:
            try:
                changes["target_amount"] = float(target)
            except (TypeError, ValueError):
                raise ExecutorError("target must be a number.")
        if timeline:
            # Only a bare 4-digit year is unambiguous from conversation; anything
            # else (e.g. "5 years") is left for the goal-planning UI to resolve.
            text = str(timeline).strip()
            if not (text.isdigit() and len(text) == 4):
                raise ExecutorError(f"'{timeline}' is not a specific year, so the target date cannot be set from it.")
            changes["target_date"] = date(int(text), 12, 31)
        if priority:
            mapped = {"high": 1, "medium": 2, "low": 3}.get(str(priority).lower())
            if mapped is None:
                raise ExecutorError("priority must be high, medium or low.")
            changes["priority"] = mapped
        if not changes:
            raise ExecutorError("Say what to change on this goal: its target, year or priority.")
        return goal, changes

    async def _goal_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, markets

        goal, c = await self._goal_changes(inputs)
        say = markets.money_for(await self._firm())
        household = await self.session.get(Household, goal.household_id)
        rank = {1: "High", 2: "Medium", 3: "Low"}
        return [("Goal", goal.name), ("Client", household.name if household else None),
                ("Target", f"{say(float(goal.target_amount))} becomes {say(c['target_amount'])}" if "target_amount" in c else None),
                ("Date", f"{firm_time.spoken(goal.target_date) if goal.target_date else 'None'} becomes {firm_time.spoken(c['target_date'])}"
                 if "target_date" in c else None),
                ("Priority", f"{rank.get(goal.priority, goal.priority)} becomes {rank[c['priority']]}" if "priority" in c else None)]

    async def update_goal(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Update a household goal's target, timeline, or priority."""
        goal, changes = await self._goal_changes(inputs)
        for field, value in changes.items():
            setattr(goal, field, value)
        await self.session.flush()

        updates = dict(changes)
        if "target_date" in updates:
            updates["target_date"] = updates["target_date"].isoformat()
        if "priority" in updates:
            updates["priority"] = str(inputs.get("priority")).lower()
        return {
            "goal_id": str(goal.id),
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

    async def _conflict(self, inputs: dict[str, Any]):
        from app.models.compliance_program import ConflictInventoryItem

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
        return row

    async def _conflict_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time

        row = await self._conflict(inputs)
        firm = await self._firm()
        return [("Conflict", row.title), ("Owner", row.owner),
                ("Last reviewed", firm_time.spoken(firm_time.today(firm, row.last_reviewed_at)) if row.last_reviewed_at else "Never"),
                ("Reviewed", firm_time.spoken(firm_time.today(firm))), ("Notes", inputs.get("notes"))]

    async def mark_conflict_reviewed(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.core.db import utcnow
        from app.models.identity import User

        row = await self._conflict(inputs)
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

    async def _wsp_rule(self, inputs: dict[str, Any]):
        from app.models.compliance_program import WSPRule

        rule_key = inputs.get("rule_key")
        if not rule_key:
            raise ExecutorError("rule_key is required")
        row = (await self.session.execute(
            select(WSPRule).where(WSPRule.firm_id == self.firm_id, WSPRule.rule_key == rule_key)
        )).scalar_one_or_none()
        if row is None:
            raise ExecutorError(f"No WSP rule with key '{rule_key}'")
        return row

    async def _wsp_preview(self, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time

        row = await self._wsp_rule(inputs)
        firm = await self._firm()
        return [("Obligation", row.obligation), ("Rule", row.rule_key), ("Frequency", row.frequency.replace("_", " ")),
                ("Last evidence", firm_time.spoken(firm_time.today(firm, row.last_evidence_at)) if row.last_evidence_at else "Never"),
                ("Logged for", firm_time.spoken(firm_time.today(firm)))]

    async def log_wsp_evidence(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.core.db import utcnow

        row = await self._wsp_rule(inputs)
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

    @staticmethod
    def _contact_args(inputs: dict[str, Any]) -> tuple[str, str]:
        from app.models.crm import CONTACT_TYPES

        full_name = inputs.get("full_name")
        if not full_name:
            raise ExecutorError("full_name is required")
        contact_type = inputs.get("contact_type") or "prospect"
        if contact_type not in CONTACT_TYPES:
            raise ExecutorError(f"contact_type must be one of {CONTACT_TYPES}")
        return full_name, contact_type

    async def _contact(self, inputs: dict[str, Any]):
        from app.models.crm import CrmContact

        contact_id = _parse_uuid(inputs.get("contact_id") or "", "contact_id")
        contact = (await self.session.execute(
            select(CrmContact).where(CrmContact.id == contact_id, CrmContact.firm_id == self.firm_id)
        )).scalar_one_or_none()
        if contact is None:
            raise ExecutorError(f"Contact {contact_id} not found")
        return contact

    async def _opportunity_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        title = inputs.get("title")
        if not title:
            raise ExecutorError("title is required")
        contact = await self._contact(inputs)
        try:
            probability = int(inputs.get("probability_pct") or 10)
        except (TypeError, ValueError):
            raise ExecutorError("probability_pct must be a whole number.")
        return {"contact": contact, "title": title, "estimated_aum": inputs.get("estimated_aum"), "probability": probability}

    async def _activity_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.models.crm import ACTIVITY_TYPES, CrmOpportunity

        detail = inputs.get("detail")
        if not detail:
            raise ExecutorError("detail is required")
        activity_type = inputs.get("activity_type") or "note"
        if activity_type not in ACTIVITY_TYPES:
            raise ExecutorError(f"activity_type must be one of {ACTIVITY_TYPES}")
        contact = await self._contact(inputs)
        opportunity = None
        if inputs.get("opportunity_id"):
            opportunity_id = _parse_uuid(inputs["opportunity_id"], "opportunity_id")
            opportunity = (await self.session.execute(select(CrmOpportunity).where(
                CrmOpportunity.id == opportunity_id, CrmOpportunity.firm_id == self.firm_id,
                CrmOpportunity.contact_id == contact.id))).scalar_one_or_none()
            if opportunity is None:
                raise ExecutorError(f"Opportunity {opportunity_id} is not this contact's.")
        return {"contact": contact, "detail": detail, "activity_type": activity_type, "opportunity": opportunity}

    async def _stage_args(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.models.crm import PIPELINE_STAGES, CrmContact, CrmOpportunity

        opportunity_id = _parse_uuid(inputs.get("opportunity_id") or "", "opportunity_id")
        stage = inputs.get("stage")
        if stage not in PIPELINE_STAGES:
            raise ExecutorError(f"stage must be one of {PIPELINE_STAGES}")
        row = (await self.session.execute(
            select(CrmOpportunity).where(CrmOpportunity.id == opportunity_id, CrmOpportunity.firm_id == self.firm_id)
        )).scalar_one_or_none()
        if row is None:
            raise ExecutorError(f"Opportunity {opportunity_id} not found")
        return {"row": row, "stage": stage, "contact": await self.session.get(CrmContact, row.contact_id)}

    async def _crm_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import markets

        say = markets.money_for(await self._firm())
        if tool_key == "create_crm_contact":
            name, kind = self._contact_args(inputs)
            return [("Name", name), ("Type", kind.replace("_", " ").capitalize()), ("Email", inputs.get("email")),
                    ("Source", inputs.get("source")), ("Notes", inputs.get("notes"))]
        if tool_key == "create_crm_opportunity":
            a = await self._opportunity_args(inputs)
            return [("Contact", a["contact"].full_name), ("Opportunity", a["title"]),
                    ("Estimated assets", say(float(a["estimated_aum"])) if a["estimated_aum"] else None),
                    ("Probability", f"{a['probability']}%"), ("Stage", "Lead")]
        if tool_key == "log_crm_activity":
            a = await self._activity_args(inputs)
            return [("Contact", a["contact"].full_name), ("Type", a["activity_type"].capitalize()), ("Detail", a["detail"]),
                    ("Opportunity", a["opportunity"].title if a["opportunity"] else None)]
        a = await self._stage_args(inputs)
        return [("Opportunity", a["row"].title), ("Contact", a["contact"].full_name if a["contact"] else None),
                ("Stage", f"{a['row'].stage.capitalize()} becomes {a['stage'].capitalize()}"),
                ("Lost reason", inputs.get("lost_reason") if a["stage"] == "lost" else None)]

    async def create_crm_contact(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.models.crm import CrmContact

        full_name, contact_type = self._contact_args(inputs)
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
        from app.models.crm import CrmOpportunity

        a = await self._opportunity_args(inputs)
        contact, title = a["contact"], a["title"]
        row = CrmOpportunity(
            firm_id=self.firm_id, contact_id=contact.id, title=title, stage="lead",
            estimated_aum=a["estimated_aum"], probability_pct=a["probability"], opened_at=utcnow(),
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
        from app.models.crm import CrmActivity

        a = await self._activity_args(inputs)
        contact, activity_type = a["contact"], a["activity_type"]
        row = CrmActivity(
            firm_id=self.firm_id, contact_id=contact.id,
            opportunity_id=a["opportunity"].id if a["opportunity"] else None,
            activity_type=activity_type, occurred_at=utcnow(), detail=a["detail"],
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

        a = await self._stage_args(inputs)
        row, stage = a["row"], a["stage"]
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

    async def _corporate_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import corporate_actions as engine
        from app.aurea_core import firm_time, markets

        firm = await self._firm()
        try:
            if tool_key == "compute_corporate_action_entitlements":
                r = await engine.prepare_compute(self.session, _parse_uuid(inputs.get("corporate_action_id") or "", "corporate_action_id"),
                                                 firm_id=self.firm_id)
                a = r["action"]
                return [("Event", a.action_type.replace("_", " ").capitalize()), ("Instrument", r["instrument"].symbol if r["instrument"] else None),
                        ("Ex date", firm_time.spoken(a.ex_date) if a.ex_date else None),
                        ("Payable", firm_time.spoken(a.payable_date) if a.payable_date else None),
                        ("Voluntary", "Yes, this opens elections" if a.is_voluntary else None),
                        ("Accounts holding", str(r["holders"])), ("New entitlements", str(r["new"])),
                        ("Already computed", str(r["existing"]))]
            r = await engine.prepare_post(self.session, _parse_uuid(inputs.get("entitlement_id") or "", "entitlement_id"),
                                          firm_id=self.firm_id)
            a, row, effect = r["action"], r["row"], r["effect"]
            shares = float(row.shares_entitled)
            return [("Event", a.action_type.replace("_", " ").capitalize()), ("Instrument", r["instrument"].symbol if r["instrument"] else None),
                    ("Account", r["account"].name if r["account"] else None), ("Shares entitled", f"{shares:g}"),
                    ("Cash in", f"{markets.money(float(effect['cash']), firm.base_currency, dp=2)} at {float(effect['per_share']):g} a share" if "cash" in effect else None),
                    ("Holding", f"{shares:g} becomes {float(effect['new_quantity']):g} at {float(effect['ratio']):g} for 1" if "new_quantity" in effect else None),
                    ("Tax lots", "Reduced in cost" if a.action_type == "return_of_capital" else "Restated" if "new_quantity" in effect else None),
                    ("Effect", "Posts once and cannot be undone")]
        except engine.CorporateActionError as exc:
            raise ExecutorError(str(exc))

    async def compute_corporate_action_entitlements(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import corporate_actions as engine
        from app.aurea_core.corporate_actions import CorporateActionError

        action_id = _parse_uuid(inputs.get("corporate_action_id") or "", "corporate_action_id")
        try:
            action = (await engine.prepare_compute(self.session, action_id, firm_id=self.firm_id))["action"]
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

        entitlement_id = _parse_uuid(inputs.get("entitlement_id") or "", "entitlement_id")
        try:
            await engine.prepare_post(self.session, entitlement_id, firm_id=self.firm_id)
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
