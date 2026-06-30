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
from app.aurea_core import planning, retirement
from app.aurea_core.graph import household_brain
from app.core.db import get_db, utcnow
from app.core.security import get_current_user
from app.llm import usage as llm_usage
from app.llm.service import firm_llm_creds, llm_service
from app.models.client_experience import HeirJourney, Message, default_heir_steps
from app.models.engagement import ClientReport, Task
from app.models.enums import HeirJourneyStatus, MessageAuthor, ReportStatus, UserRole
from app.models.graph import Person, RelationshipEdge
from app.models.identity import User
from app.models.tenant import Firm
from sqlalchemy import func


router = APIRouter(prefix="/api/canvas", tags=["canvas"])


async def _resolve_household(
    db: AsyncSession, user: User, household_id: uuid.UUID | None
) -> uuid.UUID:
    if user.role == UserRole.CLIENT and user.person_id:
        person = await db.get(Person, user.person_id)
        if person and person.household_id:
            return person.household_id
        raise HTTPException(status_code=404, detail="No household linked to this client.")
    if household_id is None:
        raise HTTPException(status_code=400, detail="household_id required for staff preview.")
    return household_id


async def _adviser_for(db: AsyncSession, firm: Firm, brain: dict) -> dict | None:
    """Find the named adviser via an 'adviser' relationship edge to a household member."""
    member_ids = {p["id"] for p in brain["persons"]} | {e["id"] for e in brain["entities"]}
    edges = (
        await db.execute(
            select(RelationshipEdge).where(
                RelationshipEdge.firm_id == firm.id, RelationshipEdge.kind == "adviser"
            )
        )
    ).scalars().all()
    for e in edges:
        if str(e.to_id) in member_ids or str(e.from_id) in member_ids:
            adviser_user_id = e.from_id if e.from_type == "user" else e.to_id
            u = await db.get(User, adviser_user_id)
            if u:
                return {"name": u.full_name, "title": u.title, "email": u.email}
    return None


@router.get("/me")
async def canvas_view(
    household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user), firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    hid = await _resolve_household(db, user, household_id)
    brain = await household_brain(db, hid)
    if not brain:
        raise HTTPException(status_code=404, detail="Household not found")

    total = brain["totals"]["total_value"]
    allocation = brain["totals"]["by_asset_class"]
    # Goal projections — the 'am I on track?' answer.
    goals = []
    for g in brain["goals"]:
        a = g.get("assumptions") or {}
        proj = planning.project_goal(
            current_value=total * a.get("funding_share", 1.0 / max(len(brain["goals"]), 1)),
            allocation=allocation, annual_contribution=a.get("annual_contribution", 0),
            annual_withdrawal=a.get("annual_withdrawal", 0), years=a.get("years", 15),
            target_amount=g["target_amount"],
        )
        goals.append({"name": g["name"], "kind": g["kind"], "target_amount": g["target_amount"],
                      "on_track": proj.on_track, "probability": proj.probability_of_success,
                      "projected_median": proj.projected_median})

    adviser = await _adviser_for(db, firm, brain)
    on_track_count = sum(1 for g in goals if g["on_track"])

    # Client-ready reports for this household (surfaced to the client).
    reports = (
        await db.execute(
            select(ClientReport).where(ClientReport.household_id == hid,
                                       ClientReport.status == ReportStatus.CLIENT_READY)
            .order_by(ClientReport.created_at.desc()).limit(5)
        )
    ).scalars().all()
    # Unread message count + next-gen members.
    unread = (
        await db.execute(
            select(func.count(Message.id)).where(Message.household_id == hid,
                                                 Message.read_by_client.is_(False),
                                                 Message.author_role != MessageAuthor.CLIENT)
        )
    ).scalar_one()
    nextgen = [p for p in brain["persons"] if p.get("is_next_gen")]

    return {
        "firm": {"name": firm.name, "branding": firm.branding},
        "adviser": adviser,
        "household": brain["household"],
        "members": brain["persons"],
        "total_wealth": total,
        "allocation": allocation,
        "data_confidence": brain["totals"]["data_confidence"],
        "goals": goals,
        "headline": (
            "You're on track." if goals and on_track_count == len(goals)
            else f"{on_track_count} of {len(goals)} goals on track." if goals
            else "Your plan is being set up."
        ),
        "values": brain["household"].get("values", {}),
        "reports": [{"id": str(r.id), "title": r.title, "period": r.period,
                     "published_at": r.published_at.isoformat() if r.published_at else None} for r in reports],
        "unread_messages": unread,
        "next_gen": [{"id": p["id"], "name": p.get("preferred_name") or p["full_name"]} for p in nextgen],
    }


@router.post("/assistant")
async def canvas_assistant(
    body: dict, household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user), firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """The adviser-branded 'am I okay?' assistant, grounded in the client's own plan."""
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Empty question")
    hid = await _resolve_household(db, user, household_id or (
        uuid.UUID(body["household_id"]) if body.get("household_id") else None))
    brain = await household_brain(db, hid)
    if not brain:
        raise HTTPException(status_code=404, detail="Household not found")

    total = brain["totals"]["total_value"]
    allocation = brain["totals"]["by_asset_class"]
    stress = planning.stress_test(allocation, ["covid_2020"])
    adviser = await _adviser_for(db, firm, brain)
    adviser_name = adviser["name"] if adviser else "your adviser"

    context = (
        f"Total wealth ${total:,.0f}; allocation {allocation}; goals "
        f"{[g['name'] for g in brain['goals']]}; in a COVID-style shock the portfolio would move "
        f"{stress['covid_2020']['impact_pct']:.0%}."
    )

    def fallback() -> str:
        return (
            f"Here's the short version: your total wealth is about ${total:,.0f}, diversified across "
            f"{len(allocation)} asset classes. Your plan is built to withstand market dips, and "
            f"{adviser_name} is watching it closely. If anything needs a decision, {adviser_name} "
            "will be in touch — you're not on your own here."
        )

    gp = await llm_usage.gateway_params(db, firm, base_max_tokens=500)
    result = await llm_service.generate(
        task="narrative",
        system=(f"You are the client-facing assistant for {firm.name}, speaking warmly in the firm's "
                f"voice on behalf of the client's named adviser, {adviser_name}. Reassure honestly, "
                "never guarantee returns, and hand off to the adviser for advice. Use only the "
                "provided figures."),
        prompt=f"Client question: {question}\n\nClient's plan snapshot: {context}",
        firm_model_config=firm.model_config_json or {},
        creds=firm_llm_creds(firm), fallback=fallback, max_tokens=gp["max_tokens"],
        pii_terms=gp["pii_terms"], pii_categories=gp["pii_categories"], redact=gp["redact"],
        allow_fallback=gp["allow_fallback"], force_fallback=gp["force_fallback"],
    )
    try:
        await llm_usage.record(db, firm.id, agent="assistant", task="narrative", result=result)
    except Exception:
        pass
    return {"answer": result.text, "adviser": adviser, "is_fallback": result.is_fallback}


# ── Secure messaging (client ↔ adviser) ───────────────────────────────────────
class MessageIn(BaseModel):
    body: str
    household_id: uuid.UUID | None = None


def _msg_dict(m: Message) -> dict:
    return {"id": str(m.id), "author_role": m.author_role, "author_name": m.author_name,
            "body": m.body, "from_agent": m.source_recommendation_id is not None,
            "created_at": m.created_at.isoformat() if m.created_at else None}


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
    plan = await retirement.for_household(db, hid, overrides=overrides)
    if not plan:
        raise HTTPException(status_code=404, detail="No plan available")
    return plan


@router.get("/messages")
async def get_messages(
    household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user), firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    hid = await _resolve_household(db, user, household_id)
    msgs = (
        await db.execute(select(Message).where(Message.household_id == hid).order_by(Message.created_at.asc()))
    ).scalars().all()
    # Mark the other party's messages read for the viewer.
    is_client = user.role == UserRole.CLIENT
    for m in msgs:
        if is_client and m.author_role != MessageAuthor.CLIENT:
            m.read_by_client = True
        elif not is_client and m.author_role == MessageAuthor.CLIENT:
            m.read_by_adviser = True
    return [_msg_dict(m) for m in msgs]


@router.post("/messages")
async def post_message(
    body: MessageIn, user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    hid = await _resolve_household(db, user, body.household_id)
    is_client = user.role == UserRole.CLIENT
    m = Message(
        firm_id=firm.id, household_id=hid,
        author_role=MessageAuthor.CLIENT if is_client else MessageAuthor.ADVISER,
        author_name=user.full_name, body=body.body.strip(),
        read_by_client=is_client, read_by_adviser=not is_client,
    )
    db.add(m)
    await db.flush()
    return _msg_dict(m)


# ── Reports (client-ready) ────────────────────────────────────────────────────
@router.get("/reports/{report_id}")
async def canvas_report(report_id: uuid.UUID, user: User = Depends(get_current_user),
                        firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    r = await db.get(ClientReport, report_id)
    if not r or r.firm_id != firm.id or r.status != ReportStatus.CLIENT_READY:
        raise HTTPException(status_code=404, detail="Report not available")
    # A client may only read their own household's report.
    if user.role == UserRole.CLIENT and user.person_id:
        person = await db.get(Person, user.person_id)
        if not person or person.household_id != r.household_id:
            raise HTTPException(status_code=403, detail="Not your report")
    return {"id": str(r.id), "title": r.title, "period": r.period, "sections": r.sections,
            "data": r.data, "status": r.status}


# ── Next-gen / heir journey ───────────────────────────────────────────────────
class StepIn(BaseModel):
    key: str
    captured: dict | None = None
    person_id: uuid.UUID | None = None


async def _resolve_heir_person(db, user, person_id, firm) -> Person:
    if user.role == UserRole.CLIENT and user.person_id:
        p = await db.get(Person, user.person_id)
        if p:
            return p
    if person_id:
        p = await db.get(Person, person_id)
        if p and p.firm_id == firm.id:
            return p
    # Staff with no id: first next-gen person in the firm.
    p = (
        await db.execute(select(Person).where(Person.firm_id == firm.id, Person.is_next_gen.is_(True)))
    ).scalars().first()
    if not p:
        raise HTTPException(status_code=404, detail="No next-gen heir found")
    return p


async def _get_or_create_journey(db, firm, person: Person) -> HeirJourney:
    j = (
        await db.execute(select(HeirJourney).where(HeirJourney.person_id == person.id))
    ).scalar_one_or_none()
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
        brain = await household_brain(db, person.household_id)
        if brain:
            adviser = await _adviser_for(db, firm, brain)
    done = sum(1 for s in j.steps if s.get("done"))
    return {"person": {"id": str(person.id), "name": person.preferred_name or person.full_name},
            "firm": {"name": firm.name, "branding": firm.branding}, "adviser": adviser,
            "status": j.status, "steps": j.steps, "captured": j.captured,
            "progress": round(done / len(j.steps), 2) if j.steps else 0}


@router.post("/heir-journey/step")
async def heir_step(body: StepIn, user: User = Depends(get_current_user),
                    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm.attributes import flag_modified

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
    request_type: str  # "meeting" | "goal_update" | "query" | "other"
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
    """Generate a branded PDF snapshot of the household's wealth picture."""
    from fastapi.responses import Response
    from fpdf import FPDF

    hid = await _resolve_household(db, user, household_id)
    brain = await household_brain(db, hid)
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
    hh_name = brain.get("household", {}).get("name", "Your Household")
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, hh_name, ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, f"Generated {utcnow().strftime('%d %B %Y')}", ln=True)
    pdf.ln(4)

    # Total wealth
    total = brain.get("total_wealth", 0)
    currency = firm.base_currency or "NZD"
    pdf.set_text_color(30, 30, 30)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, f"Total Wealth:  {currency} {total:,.0f}", ln=True)
    pdf.ln(2)

    # Goals
    goals = brain.get("goals") or []
    if goals:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Goals", ln=True)
        pdf.set_font("Helvetica", "", 10)
        for g in goals:
            status = "On track" if g.get("on_track") else "Needs review"
            prob = g.get("probability", 0)
            pdf.cell(0, 6, f"  {g['name']}  -  {status}  ({int(prob*100)}% probability)", ln=True)
        pdf.ln(3)

    # Allocation
    alloc = brain.get("allocation") or []
    if alloc:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Asset Allocation", ln=True)
        pdf.set_font("Helvetica", "", 10)
        for a in alloc[:8]:
            pct = a.get("pct", 0)
            pdf.cell(0, 6, f"  {a.get('label',''):<20} {pct:.1f}%", ln=True)
        pdf.ln(3)

    # Adviser
    adv = brain.get("adviser")
    if adv:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 6, f"Your adviser: {adv.get('name','')} - {adv.get('title','')}", ln=True)

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

QUESTIONNAIRE_KEYS = [
    "investment_horizon",   # short / medium / long
    "loss_tolerance",       # low / medium / high
    "income_stability",     # variable / stable / very_stable
    "liquidity_needs",      # high / medium / low
    "esg_priority",         # none / some / strong
    "experience",           # beginner / intermediate / experienced
]


class QuestionnaireIn(BaseModel):
    answers: dict  # key → selected value
    household_id: uuid.UUID | None = None


@router.get("/questionnaire")
async def get_questionnaire(
    household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """Return questionnaire schema + current saved answers for this mandate."""
    from app.models.graph import Mandate
    hid = await _resolve_household(db, user, household_id)
    mandate = (await db.execute(
        select(Mandate).where(Mandate.firm_id == firm.id,
                              Mandate.household_id == hid if hasattr(Mandate, "household_id") else Mandate.person_id.isnot(None))
        .limit(1)
    )).scalars().first()
    saved = (mandate.suitability or {}).get("questionnaire", {}) if mandate else {}
    schema = [
        {"key": "investment_horizon", "question": "What is your investment time horizon?",
         "options": ["short (< 3 years)", "medium (3–7 years)", "long (> 7 years)"]},
        {"key": "loss_tolerance", "question": "How comfortable are you with temporary losses?",
         "options": ["low — I'd want to sell", "medium — I'd hold on", "high — I'd buy more"]},
        {"key": "income_stability", "question": "How stable is your income?",
         "options": ["variable", "stable", "very stable"]},
        {"key": "liquidity_needs", "question": "How soon might you need access to your funds?",
         "options": ["within 1 year", "1–5 years", "5+ years"]},
        {"key": "esg_priority", "question": "How important is ESG/values alignment?",
         "options": ["not important", "somewhat important", "very important"]},
        {"key": "experience", "question": "How would you describe your investment experience?",
         "options": ["beginner", "intermediate", "experienced"]},
    ]
    return {"schema": schema, "answers": saved, "household_id": str(hid)}


@router.post("/questionnaire")
async def submit_questionnaire(
    body: QuestionnaireIn,
    user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """Save questionnaire answers into the household's mandate suitability profile."""
    from sqlalchemy.orm.attributes import flag_modified as _flag
    from app.models.graph import Mandate, Person
    hid = await _resolve_household(db, user, body.household_id)
    # Find all persons in this household, then find a mandate.
    persons = (await db.execute(
        select(Person).where(Person.firm_id == firm.id, Person.household_id == hid)
    )).scalars().all()
    person_ids = [p.id for p in persons]
    mandate = None
    if person_ids:
        mandate = (await db.execute(
            select(Mandate).where(Mandate.firm_id == firm.id, Mandate.person_id.in_(person_ids))
            .limit(1)
        )).scalars().first()
    if not mandate:
        return {"ok": True, "saved": False, "reason": "no_mandate"}
    suit = dict(mandate.suitability or {})
    suit["questionnaire"] = body.answers
    suit["questionnaire_completed_at"] = utcnow().isoformat()
    mandate.suitability = suit
    _flag(mandate, "suitability")
    await db.flush()
    return {"ok": True, "saved": True, "mandate_id": str(mandate.id)}


# ── I3: Fee Transparency Dashboard ────────────────────────────────────────────

@router.get("/fees")
async def get_fees(
    household_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """Return fee schedule and estimated fees for this household based on AUM."""
    from app.models.graph import Mandate, Person, Account
    from app.models.portfolio import Holding
    from app.models.tenant import FirmSegment
    from sqlalchemy import or_

    hid = await _resolve_household(db, user, household_id)

    # Compute total AUM for this household via persons → mandates → accounts → holdings.
    persons = (await db.execute(
        select(Person).where(Person.firm_id == firm.id, Person.household_id == hid)
    )).scalars().all()
    person_ids = [p.id for p in persons]

    total_aum = 0.0
    if person_ids:
        aum_row = (await db.execute(
            select(func.coalesce(func.sum(Holding.market_value), 0))
            .join(Account, Account.id == Holding.account_id)
            .join(Mandate, Mandate.id == Account.mandate_id)
            .where(Mandate.firm_id == firm.id, Mandate.person_id.in_(person_ids))
        )).scalar_one()
        total_aum = float(aum_row)

    # Get all firm segments with fee tiers.
    segments = (await db.execute(
        select(FirmSegment).where(FirmSegment.firm_id == firm.id, FirmSegment.is_active.is_(True))
        .order_by(FirmSegment.min_aum_usd)
    )).scalars().all()

    # Determine applicable segment by AUM.
    applicable = None
    for seg in segments:
        if seg.min_aum_usd is None or total_aum >= (seg.min_aum_usd or 0):
            applicable = seg

    fee_bps = (applicable.fee_tier_bps if applicable and applicable.fee_tier_bps else 75)
    annual_fee = total_aum * fee_bps / 10000
    monthly_fee = annual_fee / 12
    ytd_months = utcnow().month
    ytd_fee = monthly_fee * ytd_months

    return {
        "aum": total_aum,
        "currency": firm.base_currency or "NZD",
        "fee_bps": fee_bps,
        "fee_pct": round(fee_bps / 100, 4),
        "annual_fee": round(annual_fee, 2),
        "monthly_fee": round(monthly_fee, 2),
        "ytd_fee": round(ytd_fee, 2),
        "applicable_segment": {
            "slug": applicable.slug, "label": applicable.label, "fee_bps": applicable.fee_tier_bps,
            "min_aum": applicable.min_aum_usd,
        } if applicable else None,
        "fee_schedule": [
            {"slug": s.slug, "label": s.label, "fee_bps": s.fee_tier_bps,
             "fee_pct": round((s.fee_tier_bps or 0) / 100, 4), "min_aum": s.min_aum_usd}
            for s in segments if s.fee_tier_bps
        ],
    }
