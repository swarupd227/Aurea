"""Aurea Canvas API — the client-facing experience (spec §9, Table 14).

Answers 'am I going to be okay?' in the firm's voice and the named adviser's name. Staff may
preview any household's Canvas via ?household_id=; a Canvas client sees only their own."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from app.api.deps import current_firm
from app.aurea_core import balance_sheet, client_view, goal_tradeoff, ips, markets, messages as message_core, retirement
from app.aurea_core.graph import household_brain
from app.core.db import get_db, utcnow
from app.core.security import get_current_user
from app.models.client_experience import HeirJourney, Message, default_heir_steps
from app.models.engagement import Task
from app.models.enums import HeirJourneyStatus, MessageAuthor, UserRole
from app.models.graph import Household, Person
from app.models.identity import User
from app.models.tenant import Firm


router = APIRouter(prefix="/api/canvas", tags=["canvas"])

# Staff who record a client's questionnaire answers for them, from the evidence: the people who advise them.
QUESTIONNAIRE_RECORDERS = {UserRole.ADVISER, UserRole.PARAPLANNER}


async def _resolve_household(
    db: AsyncSession, user: User, household_id: uuid.UUID | None
) -> uuid.UUID:
    """The household this request is about: a client's own, whatever they ask for, or for staff the
    one named, inside their firm. A client tied to no household gets none, not the staff path."""
    if user.role == UserRole.CLIENT:
        person = await db.get(Person, user.person_id) if user.person_id else None
        if person and person.household_id:
            return person.household_id
        raise HTTPException(status_code=404, detail="No household linked to this client.")
    if household_id is None:
        raise HTTPException(status_code=400, detail="household_id required for staff preview.")
    hh = await db.get(Household, household_id)
    if hh is None or hh.firm_id != user.firm_id:
        raise HTTPException(status_code=404, detail="Household not found")
    return household_id


@router.get("/me")
async def canvas_view(
    household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user), firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """The client's home page (aurea_core.client_view.home), the same read their conversation makes."""
    hid = await _resolve_household(db, user, household_id)
    view = await client_view.home(db, hid, firm=firm)
    if view is None:
        raise HTTPException(status_code=404, detail="Household not found")
    return view


def _client_item(i: dict) -> dict:
    """A line of the balance sheet as its client reads it: what it is, how much, and as of when."""
    return {k: i[k] for k in ("id", "side", "kind", "name", "amount", "currency", "person", "as_of")}


@router.get("/net-worth")
async def canvas_net_worth(
    household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user), firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """What this household owns and owes, as it is on file. A client corrects it through their adviser."""
    hid = await _resolve_household(db, user, household_id)
    sheet = await balance_sheet.for_household(db, hid, firm_id=firm.id)
    if sheet is None:
        raise HTTPException(status_code=404, detail="Household not found")
    return {"household_id": str(hid), "currency": sheet["currency"], "net_worth": sheet["net_worth"], "basis": sheet["basis"], "assets": sheet["assets"],
            "liabilities": sheet["liabilities"], "items": [_client_item(i) for i in sheet["items"]], "not_converted": [_client_item(i) for i in sheet["not_converted"]]}


@router.get("/ips")
async def canvas_ips(
    household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user), firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """The terms this client accepted. Only the terms: what the monitor finds goes to the adviser, who talks to them."""
    hid = await _resolve_household(db, user, household_id)
    view = await ips.for_household(db, hid, firm_id=firm.id)
    if view is None:
        raise HTTPException(status_code=404, detail="Household not found")
    return {"household_id": str(hid), "policies": [
        {"mandate": m["mandate"], "version": t["version"], "accepted_on": t["accepted_on"], "targets": t["targets"], "drift_band": t["drift_band"],
         "max_position": t["max_position"], "min_cash": t["min_cash"], "restricted": t["restricted"]}
        for m in view["mandates"] if (t := m["ips"])], "without": [m["mandate"] for m in view["mandates"] if not m["ips"]]}


# The client's questions go to the conversation (POST /api/threads), with the client's own tools in
# app.core.tools (CLIENT_TOOLS): the one-shot assistant that answered from a snapshot in its prompt is retired.


# ── Secure messaging (client ↔ adviser) ───────────────────────────────────────
class MessageIn(BaseModel):
    body: str
    household_id: uuid.UUID | None = None


@router.get("/retirement")
async def canvas_retirement(
    household_id: uuid.UUID | None = None,
    retirement_age: int | None = None, longevity_age: int | None = None,
    annual_income: float | None = None,
    user: User = Depends(get_current_user), firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """The client's 'Will I be okay in retirement?' projection (same engine as the adviser sees)."""
    hid = await _resolve_household(db, user, household_id)
    overrides = {k: v for k, v in {
        "retirement_age": retirement_age, "longevity_age": longevity_age, "annual_income": annual_income,
    }.items() if v is not None}
    plan = await retirement.for_household(db, hid, firm_id=firm.id, overrides=overrides)
    if not plan:
        raise HTTPException(status_code=404, detail="No plan available")
    return plan


@router.post("/goals")
async def canvas_goals(
    body: dict | None = None,
    household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user), firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """Multi-goal trade-off view for the client canvas.  Accepts same body as the staff endpoint."""
    hid = await _resolve_household(db, user, household_id)
    body = body or {}
    plan = await goal_tradeoff.for_household(
        db, hid,
        firm_id=firm.id,
        priority_overrides=body.get("priorities"),
        goal_overrides=body.get("goal_overrides"),
        annual_income=body.get("annual_income"),
    )
    if not plan:
        raise HTTPException(status_code=404, detail="No goals plan available")
    return plan


@router.get("/messages")
async def get_messages(
    household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user), firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    hid = await _resolve_household(db, user, household_id)
    msgs = await message_core.thread(db, firm.id, hid)
    # Mark the other party's messages read for the viewer.
    if user.role == UserRole.CLIENT:
        for m in msgs:
            if m.author_role != MessageAuthor.CLIENT:
                m.read_by_client = True
    else:
        message_core.mark_read_by_adviser(msgs, user.role)
    return [message_core.view(m) for m in msgs]


@router.post("/messages")
async def post_message(
    body: MessageIn, user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    hid = await _resolve_household(db, user, body.household_id)
    is_client = user.role == UserRole.CLIENT
    if not is_client and user.role not in message_core.WRITERS:
        raise HTTPException(status_code=403, detail="Only an adviser or paraplanner may write to a client.")
    try:
        if is_client:
            m = Message(firm_id=firm.id, household_id=hid, author_role=MessageAuthor.CLIENT,
                        author_name=user.full_name, body=message_core.clean(body.body),
                        read_by_client=True, read_by_adviser=False)
        else:
            from app.aurea_core import txn_monitoring
            try:
                await txn_monitoring.tipping_off(db, firm.id, hid, body.body)
            except txn_monitoring.MonitoringError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            m = message_core.from_firm(firm.id, hid, body.body, author=user.full_name)
    except message_core.MessageError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.add(m)
    await db.flush()
    return message_core.view(m)


# ── Reports (client-ready) ────────────────────────────────────────────────────
@router.get("/reports/{report_id}")
async def canvas_report(report_id: uuid.UUID, user: User = Depends(get_current_user),
                        firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    # A client reads only their own household's report; another's reads as not there.
    hid = await _resolve_household(db, user, None) if user.role == UserRole.CLIENT else None
    r = await client_view.report(db, report_id, hid, firm_id=firm.id)
    if r is None:
        raise HTTPException(status_code=404, detail="Report not available")
    return r


# ── Next-gen / heir journey ───────────────────────────────────────────────────
class StepIn(BaseModel):
    key: str
    captured: dict | None = None
    person_id: uuid.UUID | None = None


async def _resolve_heir_person(db, user, person_id, firm) -> Person:
    if user.role == UserRole.CLIENT:
        # A client's own journey, whatever they ask for. One tied to no person gets none, not the staff path.
        # A client who is not a next-gen member has no journey, and reading the page does not start one.
        p = await db.get(Person, user.person_id) if user.person_id else None
        if p is None or p.firm_id != firm.id or not p.is_next_gen:
            raise HTTPException(status_code=404, detail="No next-gen journey for this sign-in.")
        return p
    if person_id:
        # A named person is that person or nobody: it never falls back to someone else's journey.
        p = await db.get(Person, person_id)
        if p is None or p.firm_id != firm.id or not p.is_next_gen:
            raise HTTPException(status_code=404, detail="No next-gen member by that id.")
        return p
    # Staff with no id: first next-gen person in the firm.
    p = (
        await db.execute(select(Person).where(Person.firm_id == firm.id, Person.is_next_gen.is_(True)))
    ).scalars().first()
    if not p:
        raise HTTPException(status_code=404, detail="No next-gen heir found")
    return p


async def _get_or_create_journey(db, firm, person: Person) -> HeirJourney:
    # The first read creates the journey, so two at once (a page that loads twice, two tabs) can each make
    # one. The earliest is the journey; a second must not turn every later read into an error.
    j = (
        await db.execute(select(HeirJourney).where(HeirJourney.person_id == person.id)
                         .order_by(HeirJourney.created_at).limit(1))
    ).scalars().first()
    if not j:
        j = HeirJourney(firm_id=firm.id, person_id=person.id, household_id=person.household_id,
                        status=HeirJourneyStatus.INVITED, steps=default_heir_steps(), captured={})
        db.add(j)
        await db.flush()
    return j


@router.get("/heir-journey")
async def heir_journey(person_id: uuid.UUID | None = None, user: User = Depends(get_current_user),
                       firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    person = await _resolve_heir_person(db, user, person_id, firm)
    j = await _get_or_create_journey(db, firm, person)
    adviser = None
    if person.household_id:
        brain = await household_brain(db, person.household_id, firm_id=firm.id)
        if brain:
            adviser = await client_view.adviser_for(db, firm, brain)
    done = sum(1 for s in j.steps if s.get("done"))
    return {"person": {"id": str(person.id), "name": person.preferred_name or person.full_name},
            "firm": {"name": firm.name, "branding": firm.branding}, "adviser": adviser,
            "status": j.status, "steps": j.steps, "captured": j.captured,
            "progress": round(done / len(j.steps), 2) if j.steps else 0}


@router.post("/heir-journey/step")
async def heir_step(body: StepIn, user: User = Depends(get_current_user),
                    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm.attributes import flag_modified

    # The journey is the next-gen member's own: staff see where they are and do not take its steps for them.
    if user.role != UserRole.CLIENT:
        raise HTTPException(status_code=403, detail="A next-gen member completes their own journey.")
    person = await _resolve_heir_person(db, user, body.person_id, firm)
    j = await _get_or_create_journey(db, firm, person)
    # Fresh dict copies so we don't mutate the loaded value in place (which would defeat
    # SQLAlchemy's JSON change detection).
    steps = [{**s, "done": s.get("done") or s["key"] == body.key} for s in j.steps]
    j.steps = steps
    flag_modified(j, "steps")
    if body.captured:
        j.captured = {**(j.captured or {}), **body.captured}
        flag_modified(j, "captured")
    done = sum(1 for s in steps if s.get("done"))
    j.status = (HeirJourneyStatus.COMPLETED if done == len(steps)
                else HeirJourneyStatus.IN_PROGRESS)
    await db.flush()
    return {"status": j.status, "progress": round(done / len(steps), 2)}


# ── Client-initiated actions ──────────────────────────────────────────────────

class ClientRequestIn(BaseModel):
    request_type: str  # "meeting" | "goal_update" | "query" | "correction" | "other"
    notes: str = ""
    household_id: uuid.UUID | None = None


@router.post("/requests")
async def submit_client_request(
    body: ClientRequestIn,
    user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """Client submits a request — creates a Task visible to the adviser in Studio."""
    hid = await _resolve_household(db, user, body.household_id)
    titles = {
        "meeting": "Meeting request from client",
        "goal_update": "Client wants to update goals",
        "query": "Client query",
        "correction": "Client says something on file is wrong",
        "other": "Client request",
    }
    title = titles.get(body.request_type, "Client request")
    if body.notes:
        title = f"{title}: {body.notes[:80]}"
    task = Task(
        firm_id=firm.id,
        household_id=hid,
        title=title,
        source="client_request",
        subject_label=user.full_name or user.email,
    )
    db.add(task)
    await db.flush()
    return {"task_id": str(task.id), "title": task.title}


@router.get("/documents")
async def list_canvas_documents(
    household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """Documents shared with this household (client-visible only for client role)."""
    from app.models.vault import ClientDocument
    hid = await _resolve_household(db, user, household_id)
    q = select(ClientDocument).where(
        ClientDocument.firm_id == firm.id,
        ClientDocument.household_id == hid,
    )
    if user.role == UserRole.CLIENT:
        q = q.where(ClientDocument.is_client_visible.is_(True))
    rows = (await db.execute(q.order_by(ClientDocument.created_at.desc()))).scalars().all()
    return [
        {"id": str(d.id), "filename": d.filename, "doc_type": d.doc_type,
         "tags": d.tags, "size_bytes": d.size_bytes,
         "created_at": d.created_at.isoformat() if d.created_at else None}
        for d in rows
    ]


# ── I1: Household Wealth Summary PDF ─────────────────────────────────────────

@router.get("/summary/pdf")
async def download_summary_pdf(
    household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """A branded PDF of the client's home page, from the same read (client_view.home). It read keys the
    household brain does not have, so every summary printed a total of 0 and no goals or allocation."""
    from fastapi.responses import Response
    from fpdf import FPDF

    from app.aurea_core import firm_time

    hid = await _resolve_household(db, user, household_id)
    view = await client_view.home(db, hid, firm=firm)
    if view is None:
        raise HTTPException(status_code=404, detail="Household not found")
    ccy = view["currency"]

    def say(amount: float) -> str:
        # The PDF's core font is Latin-1, so a symbol it cannot draw is written as its code.
        text = markets.money(amount, ccy)
        return text if text.isascii() else f"{ccy} {text.translate({ord(c): None for c in '₹€£'})}"

    branding = firm.branding or {}
    accent = branding.get("accent", "#c8a35e")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Header bar
    r, g, b = int(accent[1:3], 16), int(accent[3:5], 16), int(accent[5:7], 16)
    pdf.set_fill_color(r, g, b)
    pdf.rect(0, 0, 210, 18, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_xy(10, 4)
    pdf.cell(0, 10, f"{firm.name}  -  Wealth Summary", ln=True)

    pdf.set_text_color(30, 30, 30)
    pdf.set_y(24)

    # Household name
    hh_name = view["household"].get("name") or "Your household"
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, hh_name, ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, f"Generated {firm_time.today(firm).strftime('%d %B %Y')}", ln=True)
    pdf.ln(4)

    total = view["total_wealth"]
    pdf.set_text_color(30, 30, 30)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, f"Managed wealth:  {say(total)}", ln=True)
    pdf.ln(2)

    if view["goals"]:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Goals (likelihoods are estimates)", ln=True)
        pdf.set_font("Helvetica", "", 10)
        for g in view["goals"]:
            status = "On track" if g["on_track"] else "To discuss"
            pdf.cell(0, 6, f"  {g['name']}  -  {status}  ({g['probability']:.0%} likelihood, target {say(g['target_amount'])})", ln=True)
        pdf.ln(3)

    if view["allocation"]:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Asset allocation", ln=True)
        pdf.set_font("Helvetica", "", 10)
        for cls, value in sorted(view["allocation"].items(), key=lambda kv: -kv[1])[:8]:
            pdf.cell(0, 6, f"  {cls.replace('_', ' ').title():<20} {say(value)}  ({value / (total or 1):.0%})", ln=True)
        pdf.ln(3)

    adv = view["adviser"]
    if adv:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 6, f"Your adviser: {adv['name']}" + (f" - {adv['title']}" if adv.get("title") else ""), ln=True)

    # Footer
    pdf.set_y(-20)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(160, 160, 160)
    pdf.cell(0, 6, f"{firm.name}  |  Confidential  |  Not financial advice", align="C")  # noqa: ascii-only

    pdf_bytes = pdf.output()
    filename = f"{hh_name.replace(' ', '_')}_Wealth_Summary.pdf"
    return Response(
        content=bytes(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
    )


# ── I2: Client Risk Tolerance Questionnaire ───────────────────────────────────

class QuestionnaireIn(BaseModel):
    answers: dict  # key → selected value
    household_id: uuid.UUID | None = None
    subject_id: uuid.UUID | None = None   # one person or entity in the household, where it is asked for itself


@router.get("/questionnaire")
async def get_questionnaire(
    household_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """Return the questions and this household's latest answers."""
    from app.aurea_core import risk_profile
    hid = await _resolve_household(db, user, household_id)
    return {"schema": risk_profile.questions(), "household_id": str(hid), "subjects": await risk_profile.subjects(db, hid),
            "subject_id": str(subject_id) if subject_id else None,
            "answers": await risk_profile.latest_answers(db, hid, firm_id=firm.id, subject_id=subject_id)}


@router.post("/questionnaire")
async def submit_questionnaire(
    body: QuestionnaireIn,
    user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """Record a completed questionnaire. The client is not shown a result here: what it indicates goes
    to their adviser, who decides, and no mandate changes until they do."""
    from app.aurea_core import risk_profile
    hid = await _resolve_household(db, user, body.household_id)
    if user.role != UserRole.CLIENT and user.role not in QUESTIONNAIRE_RECORDERS:
        raise HTTPException(status_code=403, detail="Only the client, their adviser or a paraplanner records their answers.")
    try:
        row = await risk_profile.submit(db, hid, body.answers, firm_id=firm.id, by=user.email, role=user.role, subject_id=body.subject_id)
    except risk_profile.RiskProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "saved": True, "assessment_id": str(row.id)}


# ── I3: Fee Transparency Dashboard ────────────────────────────────────────────

@router.get("/invoices/{invoice_id}/pdf")
async def download_invoice_pdf(
    invoice_id: uuid.UUID, household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user), firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """A client's copy of one of their own invoices. The household comes from the caller, not
    the request, so another household's invoice id is a 404 rather than a document."""
    from fastapi.responses import Response

    from app.aurea_core import billing_views
    from app.aurea_core.invoice_pdf import render_invoice_pdf
    from app.models.billing import Invoice

    hid = await _resolve_household(db, user, household_id)
    inv = await db.get(Invoice, invoice_id)
    if (inv is None or inv.firm_id != firm.id or inv.household_id != hid
            or inv.status not in ("issued", "debited", "credited")):
        raise HTTPException(status_code=404, detail="Invoice not found.")
    original = await db.get(Invoice, inv.credit_of_id) if inv.credit_of_id else None
    view = billing_views.invoice_view(inv, detail=True, credit_of_number=original.invoice_number if original else None)
    pdf = render_invoice_pdf(view, firm_name=firm.name, accent=(firm.branding or {}).get("accent", "#c8a35e"),
                             base_currency=firm.base_currency or "NZD")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{inv.invoice_number}.pdf"'})


@router.get("/fees")
async def get_fees(
    household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """The fee this household is charged and what it has been billed (client_view.fees)."""
    hid = await _resolve_household(db, user, household_id)
    return await client_view.fees(db, firm, hid)
