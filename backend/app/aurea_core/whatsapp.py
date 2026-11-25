"""WhatsApp as a way a message reaches a client, and the words and language a message carries (L100 India edition, B-301).

The channel is the firm's, the words are a person's. What this module holds:

  - **The client's opt-in.** A `whatsapp` consent in the registry (`consents.py`), recorded by a person from the client's own opt-in.
    A message for the channel without it is refused, naming how to record it. Not on file does not mean not given, and nothing is
    backfilled. A withdrawal is a new record and the latest stands.
  - **The window and the templates.** WhatsApp lets a business write free text only within 24 hours of the client's last message to it.
    Outside that window only an approved template may go. Compliance records each template with its exact wording and the provider's
    approval reference (`record_template`); the wording is the only text sent, `{name}` the only variable. Free text outside the window
    is refused. The window opens from an inbound message the channel reported (`MessageDelivery` event `inbound`).
  - **What the channel reports.** A message is never described as delivered without a status: the provider's own sent, delivered, read
    or failed is recorded as it arrives (`event` `status`), added and never edited. A message submitted with no provider connected has
    no status and reads "not reported". The provider is a seam (`PROVIDERS`): the WhatsApp Business API will register here, and a
    simulation stands in at `app/simulations/whatsapp_channel` until then.
  - **The two texts.** The portal and WhatsApp alike carry the client's language and the English original: `plan` works out which
    words go (the person's translation, the seam's, or English with "translation not connected"), runs the over-promising, advertising
    and tipping-off checks on both, and `submit` records what a person confirmed.

A message to a client still goes out only behind a person's Confirm and only from an adviser or paraplanner; nothing here sends on its own.
This is not a statement that the firm's use of WhatsApp complies with any rule; counsel and the provider's own terms govern.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import advertising, client_language, consents, txn_monitoring
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.consent_registry import ConsentRecord
from app.models.graph import Household
from app.models.india_batch_b import MessageDelivery, WhatsappTemplate
from app.models.tenant import Firm
from app.provenance import ledger

TEMPLATE_RECORDERS = frozenset({UserRole.COMPLIANCE})
CHANNELS = {"portal": "Secure message (portal)", "whatsapp": "WhatsApp"}
WINDOW = timedelta(hours=24)
BODY_MAX = 1024                                     # a template body's limit on the WhatsApp Business platform
STATUSES = ("sent", "delivered", "read", "failed")
NOT_COMPLIANCE = "This does not say the firm's use of WhatsApp complies with any rule; counsel and the provider's terms decide that."
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,59}$")

PROVIDERS: dict[str, object] = {}                   # the WhatsApp Business API's client registers here; a simulation may too


class WhatsappError(ValueError):
    """This cannot be sent or recorded, with the reason."""


def register(provider) -> None:
    PROVIDERS[provider.key] = provider


def provider_for(firm: Firm):
    """The provider that serves this firm, or None. A simulation serves only a firm that opted in."""
    for key in sorted(PROVIDERS):
        p = PROVIDERS[key]
        if p.enabled_for(firm):
            return p
    return None


# ── The opt-in ─────────────────────────────────────────────────────────────────────────────────────────────────
async def opt_in(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> ConsentRecord | None:
    """The WhatsApp consent that stands for a household, or None."""
    rows = (await session.execute(select(ConsentRecord).where(ConsentRecord.firm_id == firm_id, ConsentRecord.household_id == household_id,
                                                              ConsentRecord.kind == "whatsapp"))).scalars().all()
    return consents.standing(list(rows)).get("whatsapp")


# ── The window and the templates ────────────────────────────────────────────────────────────────────────────────
async def last_inbound(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> datetime | None:
    row = (await session.execute(select(MessageDelivery).where(
        MessageDelivery.firm_id == firm_id, MessageDelivery.household_id == household_id, MessageDelivery.event == "inbound",
        MessageDelivery.channel == "whatsapp").order_by(MessageDelivery.happened_at.desc()).limit(1))).scalars().first()
    return row.happened_at if row else None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=utcnow().tzinfo)


async def window(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID, *, now: datetime | None = None) -> dict:
    now = now or utcnow()
    last = await last_inbound(session, firm_id, household_id)
    if last is None:
        return {"open": False, "last_inbound": None, "closes_at": None, "said": "The client has not written on WhatsApp: only an approved template may go."}
    closes = _aware(last) + WINDOW
    return {"open": now < closes, "last_inbound": _aware(last).isoformat(), "closes_at": closes.isoformat(),
            "said": "Free text may go until the window closes." if now < closes else "The 24-hour window has closed: only an approved template may go."}


async def templates(session: AsyncSession, firm_id: uuid.UUID) -> list[WhatsappTemplate]:
    """The latest row for each name and language, approved or withdrawn."""
    rows = (await session.execute(select(WhatsappTemplate).where(WhatsappTemplate.firm_id == firm_id)
                                  .order_by(WhatsappTemplate.created_at.asc()))).scalars().all()
    latest: dict = {}
    for r in rows:
        latest[(r.name, r.language)] = r
    return sorted(latest.values(), key=lambda r: (r.name, r.language))


def template_view(t: WhatsappTemplate) -> dict:
    return {"id": str(t.id), "name": t.name, "language": t.language, "language_label": client_language.label(t.language), "body": t.body,
            "provider_ref": t.provider_ref, "state": t.state, "reason": t.reason, "recorded_by": t.recorded_by,
            "recorded_on": t.created_at.date().isoformat() if t.created_at else None}


async def prepare_template(session: AsyncSession, firm: Firm, role, *, name=None, language=None, body=None, provider_ref=None, state: str | None = None,
                           reason=None) -> dict:
    if not client_language.applies(firm):
        raise WhatsappError("WhatsApp templates are recorded for an Indian firm.")
    if UserRole(role) not in TEMPLATE_RECORDERS:
        raise WhatsappError("A WhatsApp template is recorded by compliance, with its exact wording and the provider's approval reference.")
    key = (name or "").strip().lower()
    if not NAME_RE.match(key):
        raise WhatsappError("A template's name is lower case letters, digits and underscores, starting with a letter, 3 to 60 characters.")
    try:
        code = client_language.clean_code(language)
    except client_language.LanguageError as exc:
        raise WhatsappError(str(exc))
    state = (state or "approved").strip().lower()
    if state not in ("approved", "withdrawn"):
        raise WhatsappError("A template is approved or withdrawn.")
    text = (body or "").strip()
    ref = " ".join((provider_ref or "").split())
    if len(ref) < 4:
        raise WhatsappError("Give the provider's approval reference for the template: the firm's WhatsApp Business account shows it.")
    if state == "approved":
        if not text:
            raise WhatsappError("A template needs its exact wording.")
        if len(text) > BODY_MAX:
            raise WhatsappError(f"A template is at most {BODY_MAX} characters.")
        leftover = re.sub(r"\{name\}", "", text)
        if "{" in leftover or "}" in leftover:
            raise WhatsappError("{name} is the only variable a template may carry.")
        why = advertising.refusal(firm, text)
        if why:
            raise WhatsappError(why)
    elif len((reason or "").strip()) < 3:
        raise WhatsappError("Say why the template is withdrawn.")
    before = {(t.name, t.language): t for t in await templates(session, firm.id)}.get((key, code))
    if before and before.state == state and before.body == (text or before.body) and state == "approved":
        raise WhatsappError("Nothing changes: that template is already approved with those words.")
    if state == "withdrawn" and (before is None or before.state != "approved"):
        raise WhatsappError("There is no approved template of that name and language to withdraw.")
    return {"name": key, "language": code, "body": text if state == "approved" else (before.body if before else ""), "provider_ref": ref,
            "state": state, "reason": (reason or "").strip() or None, "before": before}


async def record_template(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_template(session, firm, role, **terms)
    session.add(WhatsappTemplate(firm_id=firm.id, name=p["name"], language=p["language"], body=p["body"], provider_ref=p["provider_ref"],
                                 state=p["state"], reason=p["reason"], recorded_by=by, created_at=utcnow()))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="whatsapp_template_recorded", actor=by, content={
        "name": p["name"], "language": p["language"], "state": p["state"], "provider_ref": p["provider_ref"]})
    return await overview(session, firm)


# ── Planning a send ─────────────────────────────────────────────────────────────────────────────────────────────
async def plan(session: AsyncSession, firm: Firm, household: Household, *, body: str | None, translation: str | None = None,
               channel: str | None = None, template: str | None = None, now: datetime | None = None) -> dict:
    """The words that would go and whether they may, on a channel. Raises WhatsappError with the reason; changes nothing. Shared by the
    Confirm card and the send, so the card shows what the send will do."""
    from app.aurea_core import messages

    channel = (channel or "portal").strip().lower()
    if channel not in CHANNELS:
        raise WhatsappError(f"A channel is one of {', '.join(CHANNELS)}.")
    india = client_language.applies(firm)
    row = await client_language.standing(session, firm.id, household.id) if india else None
    code = row.language if row else client_language.DEFAULT
    out = {"channel": channel, "language": code, "template": None, "window": None, "provider": None, "opt_in": None}

    wanted = (template or "").strip().lower()
    if channel == "whatsapp":
        if not india:
            raise WhatsappError("WhatsApp is a delivery channel for an Indian firm.")
        consent = await opt_in(session, firm.id, household.id)
        if consent is None or consent.status != "granted":
            raise WhatsappError(f"{household.name} has not opted in to WhatsApp on file. Record their opt-in from its evidence (record_consent, kind whatsapp); "
                                "until then the message can go in the secure thread.")
        out["opt_in"] = {"given_on": consent.given_on.isoformat(), "method": consent.method, "evidence": consent.evidence}
        provider = provider_for(firm)
        if provider is None:
            raise WhatsappError("WhatsApp is not connected for this firm: there is no provider, so nothing would be delivered. The message can go in the secure thread.")
        out["provider"] = {"key": provider.key, "name": provider.name, "simulated": bool(getattr(provider, "simulated", False))}
        out["window"] = await window(session, firm.id, household.id, now=now)
        if wanted or not out["window"]["open"]:
            if not wanted:
                raise WhatsappError("The client has not written on WhatsApp in the last 24 hours, so only an approved template may go (WhatsApp's rule). "
                                    "Name one with template, or send it in the secure thread.")
            have = {(t.name, t.language): t for t in await templates(session, firm.id)}
            use = have.get((wanted, code)) or have.get((wanted, client_language.DEFAULT))
            if use is None or use.state != "approved":
                raise WhatsappError(f"No approved template '{wanted}' is on file in {client_language.label(code)} or English. Compliance records one with its wording "
                                    "and the provider's approval reference.")
            text = use.body.replace("{name}", household.name)
            out["template"] = {"name": use.name, "language": use.language, "provider_ref": use.provider_ref}
            english = have[(wanted, client_language.DEFAULT)].body.replace("{name}", household.name) if (wanted, client_language.DEFAULT) in have else None
            out.update(english=english or text, local=text, translation_state="template", sent_language=use.language)
            await _check_both(session, firm, household, out["english"], out["local"])
            return out

    english = messages.clean(body)
    if code == client_language.DEFAULT:
        out.update(english=english, local=english, translation_state="english", sent_language=client_language.DEFAULT)
    elif (translation or "").strip():
        out.update(english=english, local=messages.clean(translation), translation_state="written", sent_language=code)
    else:
        t = client_language.translate(english, code)
        if t.connected:
            out.update(english=english, local=messages.clean(t.text), translation_state="seam", sent_language=code, translator=t.note)
        else:
            out.update(english=english, local=english, translation_state="not_connected", sent_language=client_language.DEFAULT)
    await _check_both(session, firm, household, out["english"], out["local"])
    return out


async def _check_both(session, firm, household, english: str, local: str) -> None:
    """The checks a message to a client is held to, on the English original and on the words that go."""
    for text in dict.fromkeys((english, local)):
        try:
            await txn_monitoring.tipping_off(session, firm.id, household.id, text)
        except txn_monitoring.MonitoringError as exc:
            raise WhatsappError(str(exc))
        why = advertising.refusal(firm, text)
        if why:
            raise WhatsappError(why)


def card_rows(plan_: dict) -> list[tuple[str, str]]:
    """What the Confirm card shows beyond who and what: the language, both texts, the channel and its rules."""
    rows: list[tuple[str, str]] = []
    state = plan_["translation_state"]
    lang = client_language.label(plan_["language"])
    if plan_["channel"] == "whatsapp":
        sim = " (simulated: a stand-in for the WhatsApp Business API)" if (plan_.get("provider") or {}).get("simulated") else ""
        rows.append(("Channel", f"WhatsApp{sim}"))
        if plan_.get("template"):
            rows.append(("Template", f"{plan_['template']['name']} ({client_language.label(plan_['template']['language'])}), approval {plan_['template']['provider_ref']}"))
        elif plan_.get("window"):
            rows.append(("Window", "Open: free text may go"))
    if plan_["language"] != client_language.DEFAULT or state == "template":
        rows.append(("Client's language", lang))
    if state == "written":
        rows += [(f"Message ({lang})", plan_["local"]), ("Message (English original)", plan_["english"]),
                 ("Translation", "The words above are the person's once confirmed; the English beside them is the original")]
    elif state == "seam":
        rows += [(f"Message ({lang})", plan_["local"]), ("Message (English original)", plan_["english"]),
                 ("Translation", f"By {plan_.get('translator')}; the person confirms these words")]
    elif state == "not_connected":
        rows += [("Message (English)", plan_["english"]),
                 ("Translation", f"{client_language.NOT_CONNECTED}: it goes in English, and {lang} is not invented")]
    elif state == "template":
        rows += [(f"Message ({client_language.label(plan_['sent_language'])})", plan_["local"])]
        if plan_["english"] != plan_["local"]:
            rows.append(("Message (English original)", plan_["english"]))
    else:
        rows.append(("Message", plan_["local"]))
    return rows


# ── Sending ─────────────────────────────────────────────────────────────────────────────────────────────────────
async def submit(session: AsyncSession, firm: Firm, household: Household, message, plan_: dict, *, by: str) -> dict:
    """After a person's Confirm: keep both texts and, for WhatsApp, hand it to the provider and record each status it reports."""
    now = utcnow()
    session.add(MessageDelivery(firm_id=firm.id, household_id=household.id, message_id=message.id, event="submitted", channel=plan_["channel"],
                                happened_at=now, language=plan_["sent_language"], translation_state=plan_["translation_state"],
                                english_body=plan_["english"], template_name=(plan_.get("template") or {}).get("name"), recorded_by=by,
                                source_system=None, created_at=now))
    statuses: list[dict] = []
    if plan_["channel"] == "whatsapp":
        provider = provider_for(firm)
        if provider is None:
            raise WhatsappError("WhatsApp is not connected for this firm.")
        reported = await provider.send(session, firm, household, plan_["local"], (plan_.get("template") or {}).get("name"))
        for i, s in enumerate(reported):
            if s.get("status") not in STATUSES:
                continue                                                    # what the channel did not report as a status is not recorded as one
            at = s.get("at") or now
            session.add(MessageDelivery(firm_id=firm.id, household_id=household.id, message_id=message.id, event="status", channel="whatsapp",
                                        status=s["status"], detail=s.get("detail"), provider_ref=s.get("ref"), happened_at=at,
                                        source_system=provider.key if getattr(provider, "simulated", False) else None,
                                        created_at=at + timedelta(microseconds=i)))
            statuses.append({"status": s["status"], "at": _aware(at).isoformat(), "detail": s.get("detail")})
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="client_message_channel", actor=by, content={
        "household_id": str(household.id), "channel": plan_["channel"], "language": plan_["sent_language"], "translation": plan_["translation_state"],
        "template": (plan_.get("template") or {}).get("name"), "statuses": [s["status"] for s in statuses]})
    return {"channel": plan_["channel"], "statuses": statuses, "state": statuses[-1]["status"] if statuses else ("not reported" if plan_["channel"] == "whatsapp" else "in the thread")}


async def deliveries(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID | None = None, *, limit: int = 20) -> list[dict]:
    """Each submitted message with the latest status the channel reported for it, newest first."""
    q = select(MessageDelivery).where(MessageDelivery.firm_id == firm_id)
    if household_id is not None:
        q = q.where(MessageDelivery.household_id == household_id)
    rows = (await session.execute(q.order_by(MessageDelivery.created_at.asc()))).scalars().all()
    subs = [r for r in rows if r.event == "submitted"]
    stat: dict = {}
    for r in rows:
        if r.event == "status" and r.message_id is not None:
            stat.setdefault(r.message_id, []).append(r)
    out = []
    for r in reversed(subs[-limit:]):
        ss = stat.get(r.message_id, [])
        last = ss[-1] if ss else None
        out.append({"message_id": str(r.message_id) if r.message_id else None, "household_id": str(r.household_id), "channel": r.channel,
                    "language": r.language, "language_label": client_language.label(r.language), "translation": r.translation_state,
                    "template": r.template_name, "submitted_at": r.happened_at.isoformat(),
                    "state": last.status if last else ("not reported" if r.channel == "whatsapp" else "in the thread"),
                    "statuses": [{"status": x.status, "at": x.happened_at.isoformat(), "detail": x.detail} for x in ss],
                    "simulated": any(x.source_system for x in ss)})
    return out


# ── The page and the tool's read ────────────────────────────────────────────────────────────────────────────────
async def overview(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None) -> dict:
    if not client_language.applies(firm):
        return {"applies": False, "households": [], "templates": [], "languages": []}
    langs = await client_language.overview(session, firm)
    provider = provider_for(firm)
    homes = [h for h in langs["households"] if household_id is None or h["household_id"] == str(household_id)]
    rows = []
    for h in homes:
        hid = uuid.UUID(h["household_id"])
        consent = await opt_in(session, firm.id, hid)
        win = await window(session, firm.id, hid)
        mine = await deliveries(session, firm.id, hid, limit=1)
        rows.append({**h, "whatsapp": {"opted_in": bool(consent and consent.status == "granted"), "withdrawn": bool(consent and consent.status == "withdrawn"),
                                       "given_on": consent.given_on.isoformat() if consent else None, "method": consent.method if consent else None},
                     "window_open": win["open"], "closes_at": win["closes_at"], "last_delivery": mine[0] if mine else None})
    tmpl = [template_view(t) for t in await templates(session, firm.id)]
    return {"applies": True, "households": rows, "templates": tmpl, "languages": langs["languages"],
            "provider": ({"key": provider.key, "name": provider.name, "simulated": bool(getattr(provider, "simulated", False))} if provider else None),
            "translation": {"connected": bool(client_language.TRANSLATORS), "said": client_language.NOT_CONNECTED if not client_language.TRANSLATORS else None},
            "counts": {"opted_in": sum(1 for r in rows if r["whatsapp"]["opted_in"]), "with_language": sum(1 for r in rows if r["recorded"]),
                       "templates": sum(1 for t in tmpl if t["state"] == "approved"), "window_open": sum(1 for r in rows if r["window_open"])},
            "said": NOT_COMPLIANCE}
