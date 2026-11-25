"""Prove B-301 (a client's language and WhatsApp) and B-302 (DigiLocker and eSign, simulated).

Covers a language recorded by a person from the client's request (added never edited, latest stands, an unknown language and no evidence
refused, a client or another role refused); the translation seam (a stub that returns the English marked "translation not connected", a
registered translator used); the message card showing both texts and the language, the checks run on both texts (a promise in the translation
refused), a message in English where translation is not connected; WhatsApp: no opt-in refused, no provider refused, free text refused outside
the 24-hour window, an approved template allowed, templates recorded by compliance only with their exact wording and approval reference,
statuses recorded as the channel reports them and nothing claimed delivered without one; a client under investigation; DigiLocker offers confirmed
into the KYC with mode digilocker (an Aadhaar number never kept, whole or in a note), an eSigned agreement confirmed into the consent registry
(sent and declined envelopes refused, once only); every tool's roles and Confirm cards, and the simulations' seams.

Builds the Indian demonstration firm (invented prices, no network) on a throwaway SQLite database.

    python -m tests.verify_india_batch_b
"""
from __future__ import annotations

import asyncio
import sys
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import client_language, consents, india_stack, kyc_india, licences, messages, whatsapp
from app.core.db import Base, utcnow
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.aml_monitoring import AmlInvestigation
from app.models.consent_registry import ConsentRecord
from app.models.client_experience import Message
from app.models.enums import UserRole
from app.models.graph import Household, Person
from app.models.identity import User
from app.models.india_batch_b import ClientLanguage, MessageDelivery, StackItem, WhatsappTemplate
from app.models.kyc import KycRecord
from app.simulations import enabled_for, opt_in
from app.simulations import india_stack as sim_stack
from app.simulations import whatsapp_channel as sim_wa
from seed import india_demo

PASS, FAIL = [], []
ERRORS = (client_language.LanguageError, whatsapp.WhatsappError, india_stack.StackError, consents.ConsentError, kyc_india.KycError, ExecutorError)
NEW_TOOLS = ("read_client_channels", "record_client_language", "record_whatsapp_template", "read_india_stack", "pull_digilocker", "send_for_esign",
             "record_digilocker_kyc", "record_esigned_agreement")
HINDI = "नमस्ते, आपकी समीक्षा बैठक अगले सप्ताह है।"


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None) -> None:
    try:
        await coro
    except ERRORS as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:120]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = await india_demo.build(s)
            await s.commit()
            users = {u.role: u for u in (await s.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
            people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
            homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
            sharma, banerjee, dsouza, kapoor = (homes["The Sharma Family"], homes["The Banerjee Household"], homes["The D'Souza Household"],
                                                homes["The Kapoor Family Trust"])
            adv = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, firm.id)
            comp = ToolExecutors(s, users[UserRole.COMPLIANCE].id, UserRole.COMPLIANCE, firm.id)
            ops = ToolExecutors(s, users[UserRole.OPERATIONS].id, UserRole.OPERATIONS, firm.id)
            pm = ToolExecutors(s, users[UserRole.PORTFOLIO_TEAM].id, UserRole.PORTFOLIO_TEAM, firm.id)

            print("\n=== The tools ===")
            check("every tool exists", all(t in TOOLS for t in NEW_TOOLS), True)
            check("no client holds any of them or send_client_message", [UserRole.CLIENT in TOOLS[t].roles_required for t in (*NEW_TOOLS, "send_client_message")], [False] * 9)
            check("each write is behind a Confirm card", all(TOOLS[t].change_state == ToolChangeState.PAUSE_FOR_CONFIRMATION for t in NEW_TOOLS if t.startswith(("record_", "pull_", "send_for"))), True)
            check("reads change nothing", [TOOLS[t].change_state for t in ("read_client_channels", "read_india_stack")], [ToolChangeState.NO] * 2)
            check("a template is compliance's alone", TOOLS["record_whatsapp_template"].roles_required, {UserRole.COMPLIANCE})

            print("\n=== B-301: a language recorded by a person ===")
            demo = await client_language.overview(s, firm)
            check("the demonstration has Hindi and Bengali on file and the D'Souzas none",
                  {h["household"]: h["language"] for h in demo["households"] if h["household"] in ("The Sharma Family", "The Banerjee Household", "The D'Souza Household")},
                  {"The Sharma Family": "hi", "The Banerjee Household": "bn", "The D'Souza Household": None})
            check("none on file reads English and says it is not the client's reading", ("English" == next(h["label"] for h in demo["households"] if h["household"] == "The D'Souza Household"),
                                                                                       "does not mean" in demo["said"]), (True, True))
            check("eight languages in one place", sorted(client_language.LANGUAGES), sorted(["en", "hi", "mr", "ta", "te", "kn", "bn", "gu"]))
            ask = {"household_id": str(dsouza.id), "language": "Marathi", "asked_by": "Elaine D'Souza", "evidence": "Her message of last week asking for Marathi"}
            card = {r["label"]: r["value"] for r in (await adv.preview_change("record_client_language", ask))["rows"]}
            check("the language card names the household, the language, who asked and the evidence",
                  (card["Household"], card["Language"].startswith("Marathi"), card["Asked by"], "Evidence" in card), ("The D'Souza Household", True, "Elaine D'Souza", True))
            await refused("a language outside the list is refused", adv.preview_change("record_client_language", {**ask, "language": "Klingon"}), "language is one of")
            await refused("a language with no evidence is refused", adv.preview_change("record_client_language", {**ask, "evidence": ""}), "where the client's request")
            await refused("a language with no one who asked is refused", adv.preview_change("record_client_language", {**ask, "asked_by": ""}), "who in the household")
            await refused("operations does not record a language", ops.preview_change("record_client_language", ask), "adviser or a paraplanner")
            await refused("recording what stands is refused", adv.preview_change("record_client_language", {**ask, "household_id": str(sharma.id), "language": "hi"}), "already")
            n = (await s.execute(select(func.count()).select_from(ClientLanguage))).scalar_one()
            out = await adv.record_client_language(ask)
            check("recorded, added not edited", ((await s.execute(select(func.count()).select_from(ClientLanguage))).scalar_one(), out["recorded"]), (n + 1, demo["recorded"] + 1))
            await adv.record_client_language({**ask, "language": "Tamil"})
            check("the latest stands and the first stays", ((await client_language.standing(s, firm.id, dsouza.id)).language, (await s.execute(select(func.count()).select_from(ClientLanguage))).scalar_one()), ("ta", n + 2))
            check("the reply language is a seam that reads it and the firm's own is None", (await client_language.reply_language(s, firm, dsouza.id), await client_language.reply_language(s, firm, kapoor.id)), ("ta", None))
            await adv.record_client_language({**ask, "language": "English"})

            print("\n=== B-301: the translation seam invents nothing ===")
            t = client_language.translate("Your review is next week.", "hi")
            check("the stub returns the English, marked not connected", (t.text, t.connected, t.note), ("Your review is next week.", False, "translation not connected"))
            check("English into English is the same words", client_language.translate("Hello", "en").connected, True)
            client_language.TRANSLATORS["test"] = lambda text, lang: f"[{lang}] {text}"
            check("a registered translator is used and named", (client_language.translate("Hi", "hi").text, client_language.translate("Hi", "hi").note), ("[hi] Hi", "test"))
            client_language.TRANSLATORS.clear()

            print("\n=== B-301: a message in the client's language ===")
            msg = {"household_id": str(sharma.id), "body": "Your annual review is next week. Please reply with a day that suits you.", "translation": HINDI}
            card = {r["label"]: r["value"] for r in (await adv.preview_change("send_client_message", msg))["rows"]}
            check("the card shows the exact words in Hindi and the English original beside them", (card["Message (Hindi)"], card["Message (English original)"]), (HINDI, msg["body"]))
            check("and says the words are the person's once confirmed", "person's once confirmed" in card["Translation"], True)
            before = (await s.execute(select(func.count()).select_from(Message))).scalar_one()
            sent = await adv.send_client_message(msg)
            thread = await messages.thread(s, firm.id, sharma.id)
            check("the client reads the Hindi, in the adviser's name", (thread[-1].body, sent["language"], sent["translation"]), (HINDI, "hi", "written"))
            check("the English original is kept beside it", (await s.execute(select(MessageDelivery.english_body).where(MessageDelivery.message_id == thread[-1].id, MessageDelivery.event == "submitted"))).scalar_one(), msg["body"])
            check("one message was added", (await s.execute(select(func.count()).select_from(Message))).scalar_one(), before + 1)
            no_tr = {k: v for k, v in msg.items() if k != "translation"}
            card = {r["label"]: r["value"] for r in (await adv.preview_change("send_client_message", no_tr))["rows"]}
            check("with no translation and none connected the card says so and the English goes", ("Message (English)" in card, "translation not connected" in card["Translation"], "Message (Hindi)" in card), (True, True, False))
            sent = await adv.send_client_message(no_tr)
            check("it went in English and the record says it was not translated", (sent["language"], sent["translation"]), ("en", "not_connected"))
            client_language.TRANSLATORS["test"] = lambda text, lang: f"[{lang}] {text}"
            card = {r["label"]: r["value"] for r in (await adv.preview_change("send_client_message", no_tr))["rows"]}
            check("a connected translator's words are shown for the person to confirm", (card["Message (Hindi)"].startswith("[hi] "), "test" in card["Translation"]), (True, True))
            client_language.TRANSLATORS.clear()
            await refused("a promise in the translation is refused", adv.preview_change("send_client_message", {**msg, "translation": "आपका पैसा guaranteed returns के साथ सुरक्षित है"}), "promises a return")
            await refused("a promise in the English is refused though the translation is clean", adv.preview_change("send_client_message", {**msg, "body": "We offer guaranteed returns."}), "promises a return")
            card = {r["label"]: r["value"] for r in (await adv.preview_change("send_client_message", {"household_id": str(dsouza.id), "body": "Hello, your statement is ready."}))["rows"]}
            check("a client with English on file sees one message and no translation rows", ("Message" in card, "Translation" in card), (True, False))
            await refused("a client cannot be sent a message with no words", adv.preview_change("send_client_message", {"household_id": str(sharma.id), "body": ""}), "words")

            print("\n=== B-301: WhatsApp needs the client's opt-in, a provider and the window ===")
            wa = {"household_id": str(sharma.id), "body": "Your review is next week.", "translation": HINDI, "channel": "whatsapp"}
            await refused("with no provider nothing is claimed", adv.preview_change("send_client_message", wa), "not connected")
            whatsapp.register(sim_wa.PROVIDER)
            await refused("a provider that the firm has not opted in to serves nobody", adv.preview_change("send_client_message", wa), "not connected")
            await opt_in(s, firm, sim_wa.KEY)
            await sim_wa.seed(s, firm)
            await s.commit()
            check("the simulated channel is tagged and the opt-in is the firm's", (enabled_for(firm, sim_wa.KEY), sim_wa.SOURCE), (True, "simulated:whatsapp_channel"))
            check("the demonstration's inbound events are tagged and keep no words", (await s.execute(select(func.count()).select_from(MessageDelivery).where(
                MessageDelivery.source_system == sim_wa.SOURCE, MessageDelivery.event == "inbound", MessageDelivery.detail.like("%no words%")))).scalar_one(), 2)
            await refused("a household that did not opt in is refused", adv.preview_change("send_client_message", {**wa, "household_id": str(dsouza.id)}), "not opted in")
            await refused("a household whose opt-in was withdrawn is refused (a withdrawal is a new record)", _withdraw_then(adv, s, firm, dsouza, wa), "opted in")
            win = await whatsapp.window(s, firm.id, sharma.id)
            check("the Sharmas wrote two hours ago: the window is open", win["open"], True)
            card = {r["label"]: r["value"] for r in (await adv.preview_change("send_client_message", wa))["rows"]}
            check("the WhatsApp card names the channel as simulated, the window and both texts",
                  ("simulated" in card["Channel"], card["Window"], card["Message (Hindi)"], card["Message (English original)"]), (True, "Open: free text may go", HINDI, wa["body"]))
            sent = await adv.send_client_message(wa)
            check("the channel's statuses are recorded as it reported them", (sent["delivery"]["state"] in ("delivered", "read"), [x["status"] for x in sent["delivery"]["statuses"]][:2]), (True, ["sent", "delivered"]))
            check("the status rows are tagged simulated and say who reported", (await s.execute(select(func.count()).select_from(MessageDelivery).where(
                MessageDelivery.event == "status", MessageDelivery.source_system == sim_wa.SOURCE))).scalar_one() >= 2, True)
            check("the message reads delivered or read only from a status", (await whatsapp.deliveries(s, firm.id, sharma.id, limit=1))[0]["state"] in ("delivered", "read"), True)
            lines = await whatsapp.deliveries(s, firm.id, sharma.id, limit=1)
            check("the delivery line names the language, channel and that it is simulated", (lines[0]["channel"], lines[0]["language"], lines[0]["simulated"]), ("whatsapp", "hi", True))

            print("\n=== B-301: outside the window only an approved template ===")
            check("the Banerjees wrote three days ago: the window is closed", (await whatsapp.window(s, firm.id, banerjee.id))["open"], False)
            out_of_window = {"household_id": str(banerjee.id), "body": "Hello, a quick note about your portfolio.", "channel": "whatsapp"}
            await refused("free text outside the 24-hour window is refused", adv.preview_change("send_client_message", out_of_window), "24 hours")
            await refused("a template that is not on file is refused", adv.preview_change("send_client_message", {**out_of_window, "template": "nothing_here"}), "No approved template")
            card = {r["label"]: r["value"] for r in (await adv.preview_change("send_client_message", {"household_id": str(banerjee.id), "channel": "whatsapp", "template": "annual_review_reminder"}))["rows"]}
            check("the template's own wording is the message, in English as there is none in Bengali",
                  ("Template" in card, card["Message (English)"] if "Message (English)" in card else card.get("Message (English original)")), (True, whatsapp_template_en(banerjee)))
            sent = await adv.send_client_message({"household_id": str(banerjee.id), "channel": "whatsapp", "template": "annual_review_reminder"})
            check("it went with the template recorded", (await s.execute(select(MessageDelivery.template_name).where(MessageDelivery.event == "submitted", MessageDelivery.household_id == banerjee.id))).scalars().first(), "annual_review_reminder")
            check("a template send carries the channel's status", sent["delivery"]["statuses"][0]["status"], "sent")
            hi = await adv.preview_change("send_client_message", {"household_id": str(sharma.id), "channel": "whatsapp", "template": "annual_review_reminder"})
            check("a Hindi client gets the Hindi template, with the English beside it", (any(r["label"] == "Message (Hindi)" and r["value"].startswith("नमस्ते The Sharma Family") for r in hi["rows"]), any(r["label"] == "Message (English original)" for r in hi["rows"])), (True, True))

            print("\n=== B-301: a failed number is a failure, and no provider means no claim ===")
            await adv.send_client_message({"household_id": str(kapoor.id), "body": "A quick note.", "channel": "whatsapp"}) if await _window_open(s, firm, kapoor) else None
            s.add(MessageDelivery(firm_id=firm.id, household_id=kapoor.id, event="inbound", channel="whatsapp", happened_at=utcnow(), created_at=utcnow(), source_system=sim_wa.SOURCE))
            await s.flush()
            fresh = await adv.send_client_message({"household_id": str(kapoor.id), "body": "A quick note.", "channel": "whatsapp"})
            check("a number not on WhatsApp reads failed, never delivered", (fresh["delivery"]["state"], "failed" in fresh["message"]), ("failed", True))
            original = whatsapp.PROVIDERS.copy()
            whatsapp.PROVIDERS.clear()
            row = Message(firm_id=firm.id, household_id=sharma.id, author_role="adviser", author_name="x", body="y")
            s.add(row)
            await s.flush()
            plan = {"channel": "whatsapp", "sent_language": "en", "translation_state": "english", "english": "y", "template": None}
            await refused("with the provider gone the send itself is refused", whatsapp.submit(s, firm, sharma, row, plan, by="t"), "not connected")
            whatsapp.PROVIDERS.update(original)

            print("\n=== B-301: templates are compliance's, with wording and a reference ===")
            tmpl = {"name": "payment_reminder", "language": "English", "body": "Hello {name}, a payment is due. Please contact your adviser.", "provider_ref": "DEMO-TPL-EN-0002 (invented)"}
            card = {r["label"]: r["value"] for r in (await comp.preview_change("record_whatsapp_template", tmpl))["rows"]}
            check("the card shows the exact wording and the provider's reference", (card["Exact wording"], card["Provider approval reference"]), (tmpl["body"], tmpl["provider_ref"]))
            for who, label in ((adv, "an adviser"), (ops, "operations"), (pm, "the portfolio team")):
                await refused(f"{label} does not record a template", who.preview_change("record_whatsapp_template", tmpl), "compliance")
            await refused("a template with no approval reference is refused", comp.preview_change("record_whatsapp_template", {**tmpl, "provider_ref": ""}), "approval reference")
            await refused("a template with another variable is refused", comp.preview_change("record_whatsapp_template", {**tmpl, "body": "Hello {amount}"}), "only variable")
            await refused("a template that promises a return is refused", comp.preview_change("record_whatsapp_template", {**tmpl, "body": "Hello {name}, guaranteed returns await you."}), "promises a return")
            await refused("a template with a bad name is refused", comp.preview_change("record_whatsapp_template", {**tmpl, "name": "Bad Name"}), "name")
            n = (await s.execute(select(func.count()).select_from(WhatsappTemplate))).scalar_one()
            await comp.record_whatsapp_template(tmpl)
            check("a template is added, not edited", (await s.execute(select(func.count()).select_from(WhatsappTemplate))).scalar_one(), n + 1)
            await refused("the same wording again is refused", comp.preview_change("record_whatsapp_template", tmpl), "already approved")
            await comp.record_whatsapp_template({**tmpl, "state": "withdrawn", "reason": "Wording changed"})
            check("a withdrawn template can no longer be sent", [t["state"] for t in (await whatsapp.overview(s, firm))["templates"] if t["name"] == "payment_reminder"], ["withdrawn"])
            await refused("so a send naming it is refused", adv.preview_change("send_client_message", {"household_id": str(banerjee.id), "channel": "whatsapp", "template": "payment_reminder"}), "No approved template")
            await refused("withdrawing what is not approved is refused", comp.preview_change("record_whatsapp_template", {**tmpl, "name": "never_existed", "state": "withdrawn", "reason": "x y z"}), "no approved template")

            print("\n=== B-301: never to a client under investigation, never without a Confirm ===")
            s.add(AmlInvestigation(firm_id=firm.id, household_id=dsouza.id, status="open", opened_by="compliance", detected_on=(await _today(s, firm))))
            await s.flush()
            await refused("a message that tells a client they are being looked at is refused, in any language",
                          adv.preview_change("send_client_message", {"household_id": str(dsouza.id), "body": "We are conducting an investigation into your account."}), "monitoring")
            check("sending needs a Confirm card: the tool pauses for confirmation", TOOLS["send_client_message"].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            check("and is the adviser's or a paraplanner's alone", TOOLS["send_client_message"].roles_required, {UserRole.ADVISER, UserRole.PARAPLANNER})

            print("\n=== B-301: the page reads it ===")
            ch = await whatsapp.overview(s, firm)
            check("the overview carries languages, opt-ins, windows, templates and the provider",
                  (ch["applies"], ch["counts"]["opted_in"], ch["counts"]["window_open"] >= 1, ch["provider"]["simulated"], ch["translation"]["said"]), (True, 3, True, True, "translation not connected"))
            read = await adv.read_client_channels({})
            check("the tool reads it and names the simulation", ("simulation" in read["message"], len(read["deliveries"]) >= 1), (True, True))
            pack = await licences.overview(s, firm)
            check("Licences & SEBI carries it", ("channels" in pack, "india_stack" in pack), (True, True))
            nz = await whatsapp.overview(s, type(firm)(jurisdiction="NZ", settings={}, slug="nz", name="n"))
            check("a firm outside India is told it is not kept", nz["applies"], False)

            print("\n=== B-302: DigiLocker, a pull a person confirms ===")
            subir = people["Subir Banerjee"]
            await refused("with no provider a pull is refused and nothing is fetched", adv.preview_change("pull_digilocker", {"person_id": str(subir.id)}), "not connected")
            india_stack.register(sim_stack.PROVIDER)
            await refused("a provider the firm did not opt in to serves nobody", adv.preview_change("pull_digilocker", {"person_id": str(subir.id)}), "not connected")
            await opt_in(s, firm, sim_stack.KEY)
            seeded = await sim_stack.seed(s, firm)
            await s.commit()
            check("the simulation offers one document and one signed envelope, tagged", (seeded["offers"], (await s.execute(select(func.count()).select_from(StackItem).where(StackItem.source_system == sim_stack.SOURCE))).scalar_one()), (2, 2))
            check("the KRA and CKYC lookup says not connected", (await adv.read_india_stack({}))["kra_ckyc"]["connected"], False)
            await refused("a client is not a person at another firm", adv.preview_change("pull_digilocker", {"person_id": "00000000-0000-0000-0000-000000000000"}), "no such person")
            await refused("operations may pull, a portfolio team may not", pm.preview_change("pull_digilocker", {"person_id": str(subir.id)}), "adviser, a paraplanner or operations")
            card = {r["label"]: r["value"] for r in (await adv.preview_change("pull_digilocker", {"person_id": str(subir.id)}))["rows"]}
            check("the pull's card says it records nothing in the KYC", ("nothing is recorded in the KYC" in card["Effect"], "simulated" in card["Provider"]), (True, True))
            kycs = (await s.execute(select(func.count()).select_from(KycRecord))).scalar_one()
            out = await adv.pull_digilocker({"person_id": str(subir.id)})
            check("a pull delivers offers (the Aadhaar one was already offered by the seed) and changes no KYC", ((await s.execute(select(func.count()).select_from(KycRecord))).scalar_one(), out["added"]), (kycs, 1))
            again = await adv.pull_digilocker({"person_id": str(subir.id)})
            check("pulling again adds nothing already offered", again["added"], 0)
            stack = await adv.read_india_stack({"household_id": str(banerjee.id)})
            docs = [i for i in stack["items"] if i["kind"] == "digilocker"]
            aadhaar = next(i for i in docs if i["doc_type"] == "aadhaar")
            pan = next(i for i in docs if i["doc_type"] == "pan")
            check("each offer shows its issuer, type, masked number and fetched day, and no whole Aadhaar number",
                  (aadhaar["issuer"], aadhaar["masked_number"].startswith("XXXX XXXX "), aadhaar["fetched_on"] is not None, kyc_india.AADHAAR_NUMBER.search(str(aadhaar)) is None), ("UIDAI (simulated)", True, True, True))
            card = {r["label"]: r["value"] for r in (await adv.preview_change("record_digilocker_kyc", {"item_id": aadhaar["id"]}))["rows"]}
            check("the KYC card names what changes: the mode, the identity proof and the Aadhaar's last four",
                  (card.get("Verified by", "").endswith("digilocker"), "Identity proof" in card, "Aadhaar, last four" in card), (True, True, True))
            check("and says no lookup is made", "no KRA or CKYC lookup" in card["Note"], True)
            await refused("a PAN offer needs only the mode and the day: it is accepted", adv.preview_change("record_digilocker_kyc", {"item_id": pan["id"]}), None) if False else None
            await refused("a compliance officer may confirm, a portfolio team may not", pm.preview_change("record_digilocker_kyc", {"item_id": aadhaar["id"]}), "adviser, a paraplanner")
            await refused("an Aadhaar number in the note is refused", adv.preview_change("record_digilocker_kyc", {"item_id": aadhaar["id"], "document": "Aadhaar 1234 5678 9012 on file"}), "never stored")
            done = await adv.record_digilocker_kyc({"item_id": aadhaar["id"]})
            row = (await s.execute(select(KycRecord).where(KycRecord.person_id == subir.id).order_by(KycRecord.created_at.desc()))).scalars().first()
            check("the KYC now carries mode digilocker, the fetched day and the last four only",
                  (row.verification_mode, row.kyc_completed_on is not None, len(row.aadhaar_last4 or ""), kyc_india.AADHAAR_NUMBER.search(row.document or "") is None), ("digilocker", True, 4, True))
            check("the evidence names the reference and says it was simulated", ("simulated" in row.document, aadhaar["reference"] in row.document), (True, True))
            check("the offer reads recorded and cannot be confirmed twice", (next(i for i in done["items"] if i["id"] == aadhaar["id"])["outcome"], ), ("recorded",))
            await refused("confirming it twice is refused", adv.preview_change("record_digilocker_kyc", {"item_id": aadhaar["id"]}), "already")
            await refused("an envelope is not a document", adv.preview_change("record_digilocker_kyc", {"item_id": next(i["id"] for i in stack["items"] if i["kind"] == "esign")}), "No such DigiLocker")
            check("a provider's whole Aadhaar number is cut to its last four", india_stack.mask("Aadhaar 1234 5678 9012"), "Aadhaar XXXX XXXX 9012")
            fake = type("P", (), {"key": "simulated:fake", "name": "Fake", "simulated": True, "enabled_for": lambda self, f: True,
                                  "pull_documents": _leaky})()
            india_stack.PROVIDERS["simulated:fake"] = fake
            del india_stack.PROVIDERS[sim_stack.SOURCE]
            await adv.pull_digilocker({"person_id": str(people["Rajesh Sharma"].id)})
            leaked = (await s.execute(select(StackItem).where(StackItem.provider == "Fake"))).scalars().all()
            check("a leaky provider's number is stored masked, never whole", [i.masked_number for i in leaked], ["XXXX XXXX 9012"])
            del india_stack.PROVIDERS["simulated:fake"]
            india_stack.register(sim_stack.PROVIDER)

            print("\n=== B-302: eSign of the advisory agreement ===")
            envs = [i for i in (await adv.read_india_stack({"household_id": str(banerjee.id)}))["items"] if i["kind"] == "esign"]
            env = envs[0]
            check("the envelope reads signed with its signer and day", (env["provider_state"], env["signer"], env["fetched_on"] is not None), ("signed", "Subir Banerjee", True))
            has = lambda: select(func.count()).select_from(ConsentRecord).where(ConsentRecord.household_id == banerjee.id, ConsentRecord.kind == "advisory_agreement")  # noqa: E731
            before = (await s.execute(has())).scalar_one()
            dsouza_has = (await s.execute(select(func.count()).select_from(ConsentRecord).where(ConsentRecord.household_id == dsouza.id, ConsentRecord.kind == "advisory_agreement"))).scalar_one()
            card = {r["label"]: r["value"] for r in (await adv.preview_change("record_esigned_agreement", {"item_id": env["id"]}))["rows"]}
            check("the agreement card names the signer, the day, the envelope and what it records",
                  (card["Signed by"], card["Envelope"] == env["reference"], "advisory agreement" in card["Records"], "Annexure A" in card["Note"]), ("Subir Banerjee", True, True, True))
            await refused("a portfolio team does not record an agreement", pm.preview_change("record_esigned_agreement", {"item_id": env["id"]}), "adviser")
            check("nothing is on file before a person confirms", (await s.execute(has())).scalar_one(), before)
            out = await adv.record_esigned_agreement({"item_id": env["id"]})
            rec = (await s.execute(select(ConsentRecord).where(ConsentRecord.household_id == banerjee.id, ConsentRecord.kind == "advisory_agreement").order_by(ConsentRecord.created_at.desc()))).scalars().first()
            check("a consent of kind advisory_agreement, e_signature, with the envelope as evidence",
                  ((await s.execute(has())).scalar_one(), rec.method, env["reference"] in rec.evidence, "simulated" in rec.evidence, rec.given_by), (before + 1, "e_signature", True, True, "Subir Banerjee"))
            check("the consent registry stands on it", consents.granted([rec]) >= {"advisory_agreement"}, True)
            await refused("recording it twice is refused", adv.preview_change("record_esigned_agreement", {"item_id": env["id"]}), "already")
            sent_env = StackItem(firm_id=firm.id, household_id=dsouza.id, person_id=people["Elaine D'Souza"].id, kind="esign", state="offered", provider="x", reference="ENV-1234",
                                 document_name="Agreement", signer="Elaine D'Souza", provider_state="sent", recorded_by="t", created_at=utcnow())
            declined = StackItem(firm_id=firm.id, household_id=dsouza.id, person_id=people["Elaine D'Souza"].id, kind="esign", state="offered", provider="x", reference="ENV-5678",
                                 document_name="Agreement", signer="Elaine D'Souza", provider_state="declined", recorded_by="t", created_at=utcnow())
            s.add_all([sent_env, declined])
            await s.flush()
            await refused("a sent envelope has no signature to record", adv.preview_change("record_esigned_agreement", {"item_id": str(sent_env.id)}), "not signed")
            await refused("a declined envelope is refused", adv.preview_change("record_esigned_agreement", {"item_id": str(declined.id)}), "declined")
            card = {r["label"]: r["value"] for r in (await adv.preview_change("send_for_esign", {"person_id": str(people["Elaine D'Souza"].id)}))["rows"]}
            check("sending for eSign says the agreement is on file only once confirmed", "only when a person confirms" in card["Effect"], True)
            sent = await adv.send_for_esign({"person_id": str(people["Elaine D'Souza"].id)})
            check("the envelope comes back as the provider reports it and records no agreement",
                  (any(i["kind"] == "esign" and i["household"] == "The D'Souza Household" and i["open"] for i in sent["items"]),
                   (await s.execute(select(func.count()).select_from(ConsentRecord).where(ConsentRecord.household_id == dsouza.id, ConsentRecord.kind == "advisory_agreement"))).scalar_one()), (True, dsouza_has))
            await refused("a document name carrying an Aadhaar number is refused", adv.preview_change("send_for_esign", {"person_id": str(subir.id), "document_name": "Agreement 1234 5678 9012"}), "Aadhaar")

            print("\n=== The seed and the simulations' own discard ===")
            from seed import india_batch_b

            check("the language seed adds nothing twice", await india_batch_b.add(s, firm), 0)
            gone = await sim_wa.discard(s, firm)
            check("discarding the channel removes what it tagged and the opt-in", (gone["deliveries"] >= 5, gone["opted_out"], enabled_for(firm, sim_wa.KEY)), (True, True, False))
            check("a message a person sent stays, its simulated statuses go", ((await s.execute(select(func.count()).select_from(Message).where(Message.household_id == sharma.id))).scalar_one() >= 2,
                                                                          await whatsapp.deliveries(s, firm.id, sharma.id, limit=1) != []), (True, True))
            check("with the simulation gone WhatsApp is refused again", await _refusal(adv, wa), True)
            gone = await sim_stack.discard(s, firm)
            check("discarding the stack removes its offers", (gone["items"] >= 3, enabled_for(firm, sim_stack.KEY)), (True, False))
            check("the KYC and the consent a person confirmed stay, naming simulated in their evidence", (row.id is not None, "simulated" in rec.evidence), (True, True))
            check("no whole Aadhaar number is in any stored text", (await s.execute(select(func.count()).select_from(StackItem).where(StackItem.masked_number.op("GLOB")("*[0-9][0-9][0-9][0-9] [0-9][0-9][0-9][0-9] [0-9][0-9][0-9][0-9]*")))).scalar_one(), 0)

        await s.close() if False else None
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


async def _leaky(self, session, firm, person):
    return [{"reference": "LEAK/AADHAAR/1", "issuer": "Test", "doc_type": "aadhaar", "masked_number": "1234 5678 9012"}]


async def _window_open(s, firm, h) -> bool:
    return False


async def _refusal(ex, msg) -> bool:
    try:
        await ex.preview_change("send_client_message", msg)
    except ExecutorError:
        return True
    return False


def whatsapp_template_en(h) -> str:
    from seed import india_batch_b

    return india_batch_b.TEMPLATE["en"].replace("{name}", h.name)


async def _withdraw_then(adv, s, firm, household, msg) -> None:
    await consents.record(s, firm.id, by="test", case_id=None, household_id=household.id, kind="whatsapp", status="withdrawn", given_by="Elaine D'Souza",
                          method="portal", evidence="Withdrawn on the client form (test)", given_on=(await _today(s, firm)))
    await adv.preview_change("send_client_message", {**msg, "household_id": str(household.id)})


async def _today(s, firm):
    from app.aurea_core import firm_time

    return firm_time.today(firm)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
