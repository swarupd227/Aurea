"""The conversation's tools for India's agreements, disclosures and the NRI checklist (B-391): the fund-house acceptance
list, one household's agreement file, and each record a person makes from a document.

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card
in ``preview_change``. Every card is the module's ``prepare_*``: every check the write makes, nothing written.
``ExecutorError`` is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

AGREEMENT_WRITES = ("record_fund_acceptance", "record_order_euin", "record_scheme_document", "record_distribution_disclosure",
                    "record_agreement_item", "record_aif_commitment", "record_aif_drawdown", "record_aif_payment", "record_estamp",
                    "record_trust_owners")
AGREEMENT_TOOLS = ("read_fund_acceptance", "read_agreement_file", *AGREEMENT_WRITES)
TERMS = {
    "record_fund_acceptance": ("fund_house", "symbol", "country", "stance", "conditions", "stated_on", "document"),
    "record_order_euin": ("order_id", "euin", "arn", "execution_only", "document"),
    "record_scheme_document": ("symbol", "kind", "dated_on", "document"),
    "record_distribution_disclosure": ("household_id", "kind", "symbol", "fund_house", "terms", "items", "method", "given_on", "document"),
    "record_agreement_item": ("relationship", "item", "answer", "mandate_id", "account_id", "commitment_id", "powers", "dated_on",
                              "document", "note"),
    "record_aif_commitment": ("person_id", "entity_id", "fund_name", "category", "commitment", "committed_on", "document"),
    "record_aif_drawdown": ("commitment_id", "number", "amount", "notice_on", "due_on", "document"),
    "record_aif_payment": ("drawdown_id", "amount", "paid_on", "document"),
    "record_estamp": ("household_id", "instrument_kind", "certificate_number", "state", "duty", "stamped_on", "parties", "document"),
    "record_trust_owners": ("case_id", "document"),
}


def _error(exc: Any):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


def _terms(inputs: dict, tool: str) -> dict:
    return {k: (None if inputs.get(k) == "" else inputs.get(k)) for k in TERMS[tool]}


def _names(rows: list[str], most: int = 6) -> str:
    return ", ".join(rows[:most]) + (f" and {len(rows) - most} more" if len(rows) > most else "")


def _money(x) -> str:
    from app.aurea_core import markets

    return markets.money(float(x), "INR")


def acceptance_message(v: dict) -> str:
    if not v.get("applies"):
        return "The fund-house acceptance list is read for an Indian firm."
    refused = [f"{l['fund_house']}{' (' + l['symbol'] + ')' if l['symbol'] else ''} for {l['country_label']}"
               for l in v["lines"] if l["stance"] == "refuses"]
    out = f"{len(v['lines'])} line(s) in force across {len(v['schemes'])} scheme(s) on file. "
    out += f"Does not accept: {_names(refused)}. " if refused else "No line refuses a resident. "
    if v["clients"]:
        out += ("Reached by the rule: " + _names([f"{c['reached'][0]['name']} ({c['mandate']}): {c['barred']} scheme(s) barred, "
                                                  f"{c['not_assessed']} not assessed" for c in v["clients"]], 4) + ". ")
    else:
        out += "No active mandate's holder is recorded as a US or Canada resident or US person. "
    return out + v["note"]


def household_message(v: dict) -> str:
    c = v["counts"]
    parts = []
    if v["funds"]["orders"]:
        parts.append(f"{c['missing_euin']} mutual fund order(s) without an EUIN or declaration")
    if v["funds"]["commission"]["asked"]:
        parts.append("commission disclosure " + ("on file" if not c["commission_missing"] else "not on file"))
    if v["funds"]["schemes"]:
        parts.append(f"{c['schemes_not_given']} scheme(s) without both SID and KIM recorded as given")
    if v["pms"]:
        parts.append(f"PMS: {c['pms_breaches']} check(s) failing, {c['pms_not_on_file']} not on file")
    if v["aif"]:
        parts.append(f"AIF: {c['drawdowns_unpaid']} unpaid drawdown(s) of {_money(c['unpaid_amount'])}, {c['drawdowns_overdue']} overdue; "
                     f"{c['aif_missing']} paper(s) not on file or not done")
    if v["broking"]:
        parts.append(f"broking and demat: {c['broking_missing']} paper(s) not on file or not done")
    if v["estamps"]:
        parts.append(f"{len(v['estamps'])} e-stamp certificate(s) on file")
    for p in v["nri"]:
        why = [x["why"] for x in p["checks"] if x.get("why")]
        parts.append(f"{p['name']} (NRI intake): {sum(1 for x in p['checks'] if x['state'] != 'on_file')} item(s) open"
                     + (f"; {why[0]}" if why else "")
                     + (f"; {p['refused']} scheme reading(s) refused, {p['not_assessed']} not assessed" if p["reached"] else ""))
    return f"{v['household']}: " + ("; ".join(parts) if parts else "no relationship paperwork applies on what is on file") + f". {v['note']}"


def firm_message(v: dict) -> str:
    t = v["totals"]
    if not v["households"]:
        return "No household has a relationship whose papers are read. " + v["note"]
    out = (f"{len(v['households'])} household(s): {t['missing_euin']} fund order(s) without an EUIN or declaration, "
           f"{t['commission_missing']} without a commission disclosure that is asked, {t['pms_breaches']} PMS check(s) failing, "
           f"{t['drawdowns_unpaid']} unpaid AIF drawdown(s) of {_money(t['unpaid_amount'])} ({t['drawdowns_overdue']} overdue), "
           f"{t['broking_missing']} broking or demat paper(s) not on file. ")
    waiting = [f"{x['case']} ({len(x['missing'])})" for x in v["trusts"] if x["missing"]]
    if waiting:
        out += f"Trust cases with beneficial owners not recorded as parties, so held: {_names(waiting)}. "
    wrong = [x["case"] for x in v["nri_cases"] if x["mismatch"]]
    if wrong:
        out += f"NRI cases where the account does not match the repatriation intent: {_names(wrong)}. "
    return out + v["note"]


class IndiaAgreementsExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _agreements_map(self) -> dict:
        return {k: getattr(self, k) for k in AGREEMENT_TOOLS}

    def _agreements_reader(self) -> None:
        from app.aurea_core import fund_acceptance, india_agreements

        if self.role not in india_agreements.READERS | fund_acceptance.RECORDERS:
            raise _error("The agreement file and the acceptance list are read by staff.")

    async def read_fund_acceptance(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import fund_acceptance

        self._agreements_reader()
        result = await fund_acceptance.overview(self.session, await self._firm())
        result["message"] = acceptance_message(result)
        return result

    async def read_agreement_file(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_agreements

        self._agreements_reader()
        if self.role not in india_agreements.READERS:
            raise _error("A household's agreement file is read by its advisory, operations and compliance staff.")
        firm = await self._firm()
        try:
            if inputs.get("household_id"):
                result = await india_agreements.household_view(self.session, firm, inputs["household_id"])
            else:
                result = await india_agreements.overview(self.session, firm)
        except india_agreements.AgreementError as exc:
            raise _error(exc)
        if not result.get("applies"):
            result["message"] = "The agreement file is read for an Indian firm."
        else:
            result["message"] = household_message(result) if result["scope"] == "household" else firm_message(result)
        return result

    async def record_fund_acceptance(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import fund_acceptance

        try:
            result = await fund_acceptance.record(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                                  **_terms(inputs, "record_fund_acceptance"))
        except fund_acceptance.AcceptanceError as exc:
            raise _error(exc)
        result["message"] = "Recorded. It is read by the rebalancer and by a direct order from now on, for purchases only."
        return result

    async def _agreement_write(self, tool: str, inputs: dict[str, Any]) -> dict:
        from app.aurea_core import india_agreements as ag

        fn = {"record_order_euin": ag.record_euin, "record_scheme_document": ag.record_scheme_document,
              "record_distribution_disclosure": ag.record_disclosure, "record_agreement_item": ag.record_item,
              "record_aif_commitment": ag.record_commitment, "record_aif_drawdown": ag.record_drawdown,
              "record_aif_payment": ag.record_payment, "record_estamp": ag.record_estamp}[tool]
        extra = {"by_user": self.user_id} if tool == "record_aif_drawdown" else {}
        try:
            return await fn(self.session, await self._firm(), self.role, by=await self._shelf_user(), **extra, **_terms(inputs, tool))
        except ag.AgreementError as exc:
            raise _error(exc)

    async def record_order_euin(self, inputs: dict[str, Any]) -> dict[str, Any]:
        result = await self._agreement_write("record_order_euin", inputs)
        result["message"] = f"Recorded on the order. {result['funds']['missing_euin']} order(s) of {result['household']} still carry neither."
        return result

    async def record_scheme_document(self, inputs: dict[str, Any]) -> dict[str, Any]:
        result = await self._agreement_write("record_scheme_document", inputs)
        result["message"] = (f"Recorded {result['kind_label']} for {result['symbol']}, dated {result['dated_on']}. It records that the firm holds "
                             "it; what a client was given is recorded separately.")
        return result

    async def record_distribution_disclosure(self, inputs: dict[str, Any]) -> dict[str, Any]:
        result = await self._agreement_write("record_distribution_disclosure", inputs)
        result["message"] = f"Recorded what {result['household']} was given. Nothing was sent."
        return result

    async def record_agreement_item(self, inputs: dict[str, Any]) -> dict[str, Any]:
        result = await self._agreement_write("record_agreement_item", inputs)
        result["message"] = f"Recorded on {result['household']}'s agreement file. It holds no order or approval."
        return result

    async def record_aif_commitment(self, inputs: dict[str, Any]) -> dict[str, Any]:
        result = await self._agreement_write("record_aif_commitment", inputs)
        result["message"] = f"Recorded the commitment on {result['household']}'s agreement file. No money moved."
        return result

    async def record_aif_drawdown(self, inputs: dict[str, Any]) -> dict[str, Any]:
        result = await self._agreement_write("record_aif_drawdown", inputs)
        result["message"] = ("Recorded the drawdown and raised a task for it on the due date. Nothing here pays it; record the payment from "
                             "the bank's advice to close it.")
        return result

    async def record_aif_payment(self, inputs: dict[str, Any]) -> dict[str, Any]:
        result = await self._agreement_write("record_aif_payment", inputs)
        c = result["counts"]
        result["message"] = f"Recorded the payment. {c['drawdowns_unpaid']} drawdown(s) of {_money(c['unpaid_amount'])} remain unpaid. No money moved here."
        return result

    async def record_estamp(self, inputs: dict[str, Any]) -> dict[str, Any]:
        result = await self._agreement_write("record_estamp", inputs)
        result["message"] = "Recorded the e-stamp certificate as it states. Nothing here pays or verifies duty."
        return result

    async def record_trust_owners(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import trust_owners

        try:
            result = await trust_owners.record(self.session, await self._firm(), self.role, by=await self._shelf_user(),
                                               **_terms(inputs, "record_trust_owners"))
        except trust_owners.TrustOwnerError as exc:
            raise _error(exc)
        hits = [a["name"] for a in result["added"] if a["screening"] != "clear"]
        result["message"] = (f"Added {len(result['added'])} part(ies) to {result['case']}, each screened. "
                             + (f"Hits to decide: {_names(hits)}. " if hits else "No screening hit. ")
                             + "Identity verification and the party rules now apply to them.")
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _agreements_preview(self, tool: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, fund_acceptance, india_agreements as ag, trust_owners

        firm = await self._firm()
        spoken = lambda d: firm_time.spoken(d) if d else None  # noqa: E731
        try:
            if tool == "record_fund_acceptance":
                p = await fund_acceptance.prepare(self.session, firm, self.role, **_terms(inputs, tool))
                r, before = p["row"], p["before"]
                return [("Fund house", r.fund_house), ("Scheme", r.symbol or "Every scheme of the house"),
                        ("Residents of", fund_acceptance.COUNTRIES[r.country]), ("Answer", fund_acceptance.STANCES[r.stance]),
                        ("Conditions", r.conditions),
                        ("Was", f"{fund_acceptance.STANCES[before.stance]}, {spoken(before.stated_on)}" if before else "Not on file"),
                        ("Covers", ", ".join(p["covered"]) or "No scheme on file yet"),
                        ("Clients reached", ", ".join(p["reached"]) or "None on file"), ("Document dated", spoken(r.stated_on)),
                        ("Document", r.document),
                        ("Effect", "Purchases for these residents are refused" if r.stance == "refuses" else "Nothing is refused")]
            if tool == "record_trust_owners":
                p = await trust_owners.prepare(self.session, firm, self.role, **_terms(inputs, tool))
                from app.aurea_core import parties

                return [("Case", p["case"].prospect_name),
                        *[(f"Party {n}", f"{o['name']}, {parties.ROLE_LABELS.get(o['as_role'], o['as_role'])} ({o['roles_label']})"
                           + (f", {o['share']:g}%" if o["share"] is not None else "")) for n, o in enumerate(p["missing"], 1)],
                        ("Document", p["document"]), ("Then", "Each is screened now; every hit is a person's to decide before the account opens")]
            if tool == "record_order_euin":
                p = await ag.prepare_euin(self.session, firm, self.role, **_terms(inputs, tool))
                o = p["order"]
                return [("Order", f"{str(o.side).title()} {float(o.quantity):g} {p['instrument'].symbol}, {p['account'].name}"),
                        ("EUIN", p["euin"]), ("ARN", p["arn"]),
                        ("Declaration", "Execution-only, declared by the investor" if p["execution_only"] else None),
                        ("Document", p["document"])]
            if tool == "record_scheme_document":
                p = await ag.prepare_scheme_document(self.session, firm, self.role, **_terms(inputs, tool))
                return [("Scheme", p["instrument"].name), ("Document", ag.SCHEME_KINDS[p["kind"]]), ("Dated", spoken(p["dated_on"])),
                        ("Filed", p["document"])]
            if tool == "record_distribution_disclosure":
                p = await ag.prepare_disclosure(self.session, firm, self.role, **_terms(inputs, tool))
                return [("Household", p["household"].name), ("Given", ag.DISCLOSURES[p["kind"]]),
                        ("For", p["instrument"].name if p["instrument"] else p["fund_house"]),
                        ("Commission as stated", p["terms"]), ("Documents", ", ".join(x.upper() for x in p["items"]) or None),
                        ("How", ag.METHODS[p["method"]]), ("On", spoken(p["given_on"])), ("Evidence", p["document"]),
                        ("Note", "A record that it was given: nothing is sent")]
            if tool == "record_agreement_item":
                p = await ag.prepare_item(self.session, firm, self.role, **_terms(inputs, tool))
                v = p["view"]
                return [("Relationship", ag.RELATIONSHIPS[p["relationship"]]), ("About", p["subject"]), ("Item", p["label"]),
                        ("Answer", p["answer"].replace("_", " ")), ("Powers", ", ".join(p["detail"].get("powers") or []) or None),
                        ("Dated", spoken(p["dated_on"])), ("Reads as", "On file" if v["state"] == "on_file" else f"Fails: {v['why']}"),
                        ("Evidence", p["document"]), ("Note", p["note"])]
            if tool == "record_aif_commitment":
                p = await ag.prepare_commitment(self.session, firm, self.role, **_terms(inputs, tool))
                return [("Investor", p["investor"]), ("Fund", p["fund"]), ("Category", ag.CATEGORIES[p["category"]]),
                        ("Commitment", _money(p["commitment"])),
                        ("Minimum", f"Below the {_money(p['minimum'])} minimum (exceptions are not read)" if p["below_minimum"] else None),
                        ("Agreement dated", spoken(p["committed_on"])), ("Evidence", p["document"]), ("Note", "No money moves")]
            if tool == "record_aif_drawdown":
                p = await ag.prepare_drawdown(self.session, firm, self.role, **_terms(inputs, tool))
                c = p["commitment"]
                return [("Fund", c.fund_name), ("Investor", c.investor_name), ("Drawdown", p["number"]), ("Amount", _money(p["amount"])),
                        ("Called after this", f"{_money(p['called_after'])} of {_money(c.commitment)}"),
                        ("Notice", spoken(p["notice_on"])), ("Due", spoken(p["due_on"])),
                        ("Task for", p["adviser"].full_name if p["adviser"] else "Nobody: no adviser on record"),
                        ("Evidence", p["document"]), ("Note", "Nothing here pays it")]
            if tool == "record_aif_payment":
                p = await ag.prepare_payment(self.session, firm, self.role, **_terms(inputs, tool))
                d, c = p["drawdown"], p["commitment"]
                return [("Fund", c.fund_name), ("Drawdown", f"{d.number}, {_money(d.amount)}"), ("Paid", _money(p["amount"])),
                        ("On", spoken(p["paid_on"])), ("Left unpaid", _money(p["left"])),
                        ("Task", "Closes" if p["task"] else ("Stays open" if p["left"] > 0 else None)), ("Evidence", p["document"]),
                        ("Note", "A record only: no cash moves")]
            p = await ag.prepare_estamp(self.session, firm, self.role, **_terms(inputs, "record_estamp"))
            return [("Household", p["household"].name), ("Instrument", ag.STAMPED[p["kind"]]), ("Certificate", p["certificate_number"]),
                    ("State", p["state"]), ("Duty", _money(p["duty"])), ("Stamped", spoken(p["stamped_on"])), ("Parties", p["parties"]),
                    ("Evidence", p["document"]), ("Note", "As the certificate states: nothing is paid or verified")]
        except (fund_acceptance.AcceptanceError, ag.AgreementError, trust_owners.TrustOwnerError) as exc:
            raise _error(exc)
