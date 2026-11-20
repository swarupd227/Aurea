"""Dynamics 365 as the CRM (B-163): contacts, opportunities and activities in through the Dataverse Web API; an
activity out as a command.

Mock mode (the connector's default) reads a fixture shaped as Dataverse returns it: ``contactid``, ``fullname``,
``emailaddress1``; ``opportunityid``, ``estimatedvalue``, ``salesstage``/``stepname``, ``closeprobability``,
``estimatedclosedate``, ``_parentcontactid_value``, ``_ownerid_value``; ``activitypointer`` rows with
``activitytypecode``, ``scheduledstart``/``actualend``, ``description`` and ``_regardingobjectid_value``; notes as
``annotation`` rows. A command the mock sink took comes back on the next mock pull as D365 would return it, so the
round trip is the live one's. Live mode (``use_mock`` off) reads the firm's own Dataverse environment with an OAuth
client-credentials token from the connector's stored settings. No secret is in the code or in any message or log.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from app.foundation import contract as c
from app.foundation.adapters.base import Adapter, AdapterError, Pulled

KEY = "crm.dynamics"
API = "api/data/v9.2"
LOOKUP = "Microsoft.Dynamics.CRM.lookuplogicalname"

# Dataverse option sets, as the CRM's own forms label them.
LEAD_SOURCE = {1: "advertisement", 2: "employee referral", 3: "referral", 4: "partner", 5: "public relations",
               6: "seminar", 7: "trade show", 8: "web", 9: "word of mouth", 10: "other"}
SALES_STAGE = {0: "lead", 1: "qualified", 2: "proposal", 3: "proposal"}          # Qualify, Develop, Propose, Close
STEP_STAGE = {"1-qualify": "lead", "2-develop": "qualified", "3-propose": "proposal", "4-close": "proposal"}
LOST_STATUS = {4: "Canceled in Dynamics 365", 5: "Out-sold in Dynamics 365"}
ACTIVITY_TYPE = {"phonecall": "call", "email": "email", "appointment": "meeting", "task": "task", "annotation": "note"}
ENTITY_SET = {"call": "phonecalls", "email": "emails", "meeting": "appointments", "task": "tasks", "note": "annotations"}

SELECT = {
    "systemusers": "systemuserid,internalemailaddress,fullname",
    "contacts": "contactid,fullname,emailaddress1,telephone1,leadsourcecode,description,statecode,_ownerid_value,"
                "modifiedon",
    "opportunities": "opportunityid,name,_parentcontactid_value,_ownerid_value,estimatedvalue,closeprobability,"
                     "estimatedclosedate,salesstage,stepname,statecode,statuscode,createdon,actualclosedate,modifiedon",
    "activitypointers": "activityid,activitytypecode,subject,description,scheduledstart,actualend,createdon,"
                        "_regardingobjectid_value,_ownerid_value,modifiedon",
    "annotations": "annotationid,notetext,subject,_objectid_value,objecttypecode,createdon,_ownerid_value,modifiedon",
}


# ── The fixture: one adviser's book as Dataverse returns it ───────────────────────────────────────

_U1, _U2 = "6f1b2c3d-0001-4000-8000-00000000a001", "6f1b2c3d-0002-4000-8000-00000000a002"
_C = ["9a0c1e21-1111-4f00-9000-0000000000c1", "9a0c1e21-2222-4f00-9000-0000000000c2",
      "9a0c1e21-3333-4f00-9000-0000000000c3", "9a0c1e21-4444-4f00-9000-0000000000c4"]
_O = ["b7d2f3a4-1111-4e00-8000-0000000000e1", "b7d2f3a4-2222-4e00-8000-0000000000e2",
      "b7d2f3a4-3333-4e00-8000-0000000000e3", "b7d2f3a4-4444-4e00-8000-0000000000e4"]


def fixture() -> dict[str, list[dict]]:
    """A realistic Dataverse read: two advisers, four contacts, four opportunities, their calls, emails, meetings
    and notes. Fixed instants, so a second pull reads exactly the first."""
    return {
        "systemusers": [
            {"systemuserid": _U1, "internalemailaddress": "d365.adviser@contoso.example", "fullname": "Mere Tane"},
            {"systemuserid": _U2, "internalemailaddress": "d365.associate@contoso.example", "fullname": "Liam Foster"},
        ],
        "contacts": [
            {"contactid": _C[0], "fullname": "Aroha Ngata", "emailaddress1": "aroha.ngata@example.com",
             "telephone1": "+64 21 555 0101", "leadsourcecode": 3, "description": "Sold an engineering practice.",
             "statecode": 0, "_ownerid_value": _U1, "modifiedon": "2026-11-12T09:15:00Z"},
            {"contactid": _C[1], "fullname": "Daniel Okafor", "emailaddress1": "d.okafor@example.com",
             "telephone1": None, "leadsourcecode": 6, "description": None,
             "statecode": 0, "_ownerid_value": _U1, "modifiedon": "2026-11-03T14:00:00Z"},
            {"contactid": _C[2], "fullname": "Hannah Whitfield", "emailaddress1": "hannah.w@example.com",
             "telephone1": "+64 27 555 0303", "leadsourcecode": 8, "description": "Inherited a share portfolio.",
             "statecode": 0, "_ownerid_value": _U2, "modifiedon": "2026-10-28T08:30:00Z"},
            {"contactid": _C[3], "fullname": "Ravi Menon", "emailaddress1": "ravi.menon@example.com",
             "telephone1": None, "leadsourcecode": None, "description": None,
             "statecode": 0, "_ownerid_value": _U2, "modifiedon": "2026-09-30T10:00:00Z"},
        ],
        "opportunities": [
            {"opportunityid": _O[0], "name": "Aroha Ngata - business sale proceeds", "_parentcontactid_value": _C[0],
             "_ownerid_value": _U1, "estimatedvalue": 1850000.0, "closeprobability": 60,
             "estimatedclosedate": "2026-12-15", "salesstage": 2, "stepname": "3-Propose", "statecode": 0,
             "statuscode": 1, "createdon": "2026-10-01T09:00:00Z", "actualclosedate": None,
             "modifiedon": "2026-11-12T09:15:00Z"},
            {"opportunityid": _O[1], "name": "Daniel Okafor - retirement rollover", "_parentcontactid_value": _C[1],
             "_ownerid_value": _U1, "estimatedvalue": 420000.0, "closeprobability": None,
             "estimatedclosedate": None, "salesstage": 0, "stepname": "1-Qualify", "statecode": 0,
             "statuscode": 1, "createdon": "2026-10-20T11:00:00Z", "actualclosedate": None,
             "modifiedon": "2026-11-03T14:00:00Z"},
            {"opportunityid": _O[2], "name": "Hannah Whitfield - inherited portfolio", "_parentcontactid_value": _C[2],
             "_ownerid_value": _U2, "estimatedvalue": None, "closeprobability": 40,
             "estimatedclosedate": "2027-01-31", "salesstage": 1, "stepname": "2-Develop", "statecode": 0,
             "statuscode": 1, "createdon": "2026-09-15T08:00:00Z", "actualclosedate": None,
             "modifiedon": "2026-10-28T08:30:00Z"},
            {"opportunityid": _O[3], "name": "Ravi Menon - new relationship", "_parentcontactid_value": _C[3],
             "_ownerid_value": _U2, "estimatedvalue": 300000.0, "closeprobability": 10,
             "estimatedclosedate": None, "salesstage": 0, "stepname": "1-Qualify", "statecode": 2,
             "statuscode": 5, "createdon": "2026-08-01T08:00:00Z", "actualclosedate": "2026-09-30T10:00:00Z",
             "modifiedon": "2026-09-30T10:00:00Z"},
        ],
        "activitypointers": [
            {"activityid": "c3e4f5a6-0001-4d00-9000-0000000000a1", "activitytypecode": "appointment",
             "subject": "Discovery meeting", "description": "Discovery meeting: proceeds expected in December.",
             "scheduledstart": "2026-11-12T09:00:00Z", "actualend": "2026-11-12T10:00:00Z",
             "createdon": "2026-11-05T09:00:00Z", "_regardingobjectid_value": _O[0],
             f"_regardingobjectid_value@{LOOKUP}": "opportunity", "_ownerid_value": _U1,
             "modifiedon": "2026-11-12T10:05:00Z"},
            {"activityid": "c3e4f5a6-0002-4d00-9000-0000000000a2", "activitytypecode": "phonecall",
             "subject": "Intro call", "description": "Introduced by their accountant.",
             "scheduledstart": None, "actualend": "2026-10-01T15:30:00Z", "createdon": "2026-10-01T15:30:00Z",
             "_regardingobjectid_value": _C[0], f"_regardingobjectid_value@{LOOKUP}": "contact",
             "_ownerid_value": _U1, "modifiedon": "2026-10-01T15:30:00Z"},
            {"activityid": "c3e4f5a6-0003-4d00-9000-0000000000a3", "activitytypecode": "email",
             "subject": "Rollover options", "description": "Sent the rollover fact sheet.",
             "scheduledstart": None, "actualend": "2026-11-03T13:50:00Z", "createdon": "2026-11-03T13:50:00Z",
             "_regardingobjectid_value": _O[1], f"_regardingobjectid_value@{LOOKUP}": "opportunity",
             "_ownerid_value": _U1, "modifiedon": "2026-11-03T13:50:00Z"},
            {"activityid": "c3e4f5a6-0004-4d00-9000-0000000000a4", "activitytypecode": "appointment",
             "subject": "Portfolio review", "description": "Walk through the inherited holdings.",
             "scheduledstart": "2026-12-02T02:00:00Z", "actualend": None, "createdon": "2026-11-18T08:00:00Z",
             "_regardingobjectid_value": _O[2], f"_regardingobjectid_value@{LOOKUP}": "opportunity",
             "_ownerid_value": _U2, "modifiedon": "2026-11-18T08:00:00Z"},
            {"activityid": "c3e4f5a6-0005-4d00-9000-0000000000a5", "activitytypecode": "phonecall",
             "subject": "Follow-up", "description": "Chose to stay with their bank.",
             "scheduledstart": None, "actualend": "2026-09-30T09:45:00Z", "createdon": "2026-09-30T09:45:00Z",
             "_regardingobjectid_value": _O[3], f"_regardingobjectid_value@{LOOKUP}": "opportunity",
             "_ownerid_value": _U2, "modifiedon": "2026-09-30T09:45:00Z"},
            # Regarding an account (a company), which the contract has no place for: skipped and counted.
            {"activityid": "c3e4f5a6-0006-4d00-9000-0000000000a6", "activitytypecode": "task",
             "subject": "Renew sponsorship", "description": "Renew the chamber of commerce sponsorship.",
             "scheduledstart": None, "actualend": "2026-11-10T00:00:00Z", "createdon": "2026-11-01T00:00:00Z",
             "_regardingobjectid_value": "0d0d0d0d-0000-4000-8000-00000000acc1",
             f"_regardingobjectid_value@{LOOKUP}": "account", "_ownerid_value": _U1,
             "modifiedon": "2026-11-10T00:00:00Z"},
        ],
        "annotations": [
            {"annotationid": "d4e5f6a7-0001-4c00-8000-0000000000b1", "subject": "Note",
             "notetext": "Prefers email; travelling until 20 November.", "_objectid_value": _C[2],
             "objecttypecode": "contact", "createdon": "2026-10-28T08:30:00Z", "_ownerid_value": _U2,
             "modifiedon": "2026-10-28T08:30:00Z"},
        ],
    }


# ── Dataverse rows to the contract ──────────────────────────────────────────────────────────────

def _instant(v: Any) -> datetime | None:
    if not v:
        return None
    at = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)   # Dataverse speaks UTC


def _day(v: Any) -> date | None:
    if not v:
        return None
    return date.fromisoformat(str(v)[:10])


def _stage(o: dict) -> str:
    if o.get("statecode") == 1:
        return "won"
    if o.get("statecode") == 2:
        return "lost"
    step = str(o.get("stepname") or "").strip().lower()
    if step in STEP_STAGE:
        return STEP_STAGE[step]
    return SALES_STAGE.get(o.get("salesstage"), "lead")


def to_contract(raw: dict[str, list[dict]]) -> Pulled:
    """Dataverse rows as contract records. What the contract has no place for is skipped and named."""
    emails = {u["systemuserid"]: u.get("internalemailaddress") for u in raw.get("systemusers") or []}
    out = Pulled()
    contacts: set[str] = set()
    parent: dict[str, str] = {}
    for r in raw.get("contacts") or []:
        if r.get("statecode") not in (0, None):
            out.skipped.append(f"contact {r.get('contactid')}: inactive in Dynamics 365")
            continue
        contacts.add(r["contactid"])
        out.records.append(c.Contact(
            source=KEY, external_id=r["contactid"], as_of=_instant(r.get("modifiedon")), full_name=r.get("fullname") or "",
            email=r.get("emailaddress1"), phone=r.get("telephone1"),
            lead_source=LEAD_SOURCE.get(r.get("leadsourcecode")) if r.get("leadsourcecode") is not None else None,
            notes=r.get("description"), owner_email=emails.get(r.get("_ownerid_value")), contact_type="prospect"))
    for r in raw.get("opportunities") or []:
        contact = r.get("_parentcontactid_value")
        if contact not in contacts:
            out.skipped.append(f"opportunity {r.get('opportunityid')}: no contact on file for it")
            continue
        parent[r["opportunityid"]] = contact
        stage = _stage(r)
        prob = r.get("closeprobability")
        out.records.append(c.Opportunity(
            source=KEY, external_id=r["opportunityid"], as_of=_instant(r.get("modifiedon")), contact_ref=contact,
            title=r.get("name") or "", stage=stage,
            estimated_aum=float(r["estimatedvalue"]) if r.get("estimatedvalue") is not None else None,
            probability_pct=int(prob) if prob is not None else None,
            expected_close_date=_day(r.get("estimatedclosedate")),
            lost_reason=LOST_STATUS.get(r.get("statuscode"), "Lost in Dynamics 365") if stage == "lost" else None,
            opened_at=_instant(r.get("createdon")),
            closed_at=_instant(r.get("actualclosedate")) if stage in ("won", "lost") else None))
    for r in raw.get("activitypointers") or []:
        kind = ACTIVITY_TYPE.get(str(r.get("activitytypecode") or "").lower())
        about, what = r.get("_regardingobjectid_value"), str(r.get(f"_regardingobjectid_value@{LOOKUP}") or "")
        contact, opp = (parent.get(about), about) if what == "opportunity" else (about, None)
        if kind is None or contact not in contacts or (opp and opp not in parent):
            out.skipped.append(f"activity {r.get('activityid')}: regarding {what or 'nothing'} the contract has no place for")
            continue
        at = _instant(r.get("actualend")) or _instant(r.get("scheduledstart")) or _instant(r.get("createdon"))
        out.records.append(c.Activity(
            source=KEY, external_id=r["activityid"], as_of=_instant(r.get("modifiedon")), contact_ref=contact,
            opportunity_ref=opp, activity_type=kind, occurred_at=at,
            detail=r.get("description") or r.get("subject") or "", logged_by_email=emails.get(r.get("_ownerid_value"))))
    for r in raw.get("annotations") or []:
        about = r.get("_objectid_value")
        if str(r.get("objecttypecode") or "") != "contact" or about not in contacts:
            out.skipped.append(f"note {r.get('annotationid')}: not on a contact")
            continue
        out.records.append(c.Activity(
            source=KEY, external_id=r["annotationid"], as_of=_instant(r.get("modifiedon")), contact_ref=about,
            activity_type="note", occurred_at=_instant(r.get("createdon")),
            detail=r.get("notetext") or r.get("subject") or "", logged_by_email=emails.get(r.get("_ownerid_value"))))
    return out


# ── Live: the firm's Dataverse environment ──────────────────────────────────────────────────────

class DataverseClient:
    """The Dataverse Web API with an OAuth client-credentials token. Settings come from the connector's stored
    configuration (``base_url`` the environment, ``tenant_id``, ``client_id``, ``client_secret``); none is logged or
    put in an error, which names only what was asked and the status the service answered."""

    LOGIN = "https://login.microsoftonline.com"

    def __init__(self, config: dict, *, transport=None):
        cfg = config or {}
        self.base = str(cfg.get("base_url") or "").rstrip("/")
        self.tenant = str(cfg.get("tenant_id") or "").strip()
        self.client_id = str(cfg.get("client_id") or "").strip()
        self._secret = str(cfg.get("client_secret") or "")
        self.transport = transport
        self._token: str | None = None
        missing = [n for n, v in (("Environment URL", self.base), ("Tenant ID", self.tenant), ("Client ID", self.client_id),
                                  ("Client secret", self._secret)) if not v]
        if missing:
            raise AdapterError(f"{', '.join(missing)} not set on the Dynamics 365 connector.")
        if not self.base.startswith("https://"):
            raise AdapterError("The Dynamics 365 environment URL must be https.")

    def _client(self):
        import httpx

        return httpx.AsyncClient(timeout=30, transport=self.transport)

    async def token(self) -> str:
        if self._token:
            return self._token
        async with self._client() as http:
            r = await http.post(f"{self.LOGIN}/{self.tenant}/oauth2/v2.0/token", data={
                "grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self._secret,
                "scope": f"{self.base}/.default"})
        if r.status_code != 200:
            raise AdapterError(f"Dynamics 365 sign-in refused ({r.status_code}).")
        self._token = r.json().get("access_token")
        if not self._token:
            raise AdapterError("Dynamics 365 sign-in returned no token.")
        return self._token

    async def _headers(self) -> dict:
        return {"Authorization": f"Bearer {await self.token()}", "Accept": "application/json", "OData-Version": "4.0",
                "OData-MaxVersion": "4.0", "Prefer": f'odata.include-annotations="{LOOKUP}"'}

    async def read(self, entity_set: str) -> list[dict]:
        rows: list[dict] = []
        url: str | None = f"{self.base}/{API}/{entity_set}?$select={SELECT[entity_set]}"
        if entity_set == "annotations":
            url += "&$filter=objecttypecode eq 'contact'"
        async with self._client() as http:
            while url:
                r = await http.get(url, headers=await self._headers())
                if r.status_code != 200:
                    raise AdapterError(f"Dynamics 365 refused the read of {entity_set} ({r.status_code}).")
                body = r.json()
                rows.extend(body.get("value") or [])
                url = body.get("@odata.nextLink")
        return rows

    async def read_all(self) -> dict[str, list[dict]]:
        return {name: await self.read(name) for name in SELECT}

    async def create_activity(self, body: dict, kind: str) -> str:
        entity_set = ENTITY_SET[kind]
        async with self._client() as http:
            r = await http.post(f"{self.base}/{API}/{entity_set}", json=body,
                                headers={**await self._headers(), "Content-Type": "application/json",
                                         "Prefer": "return=representation"})
        if r.status_code not in (200, 201):
            raise AdapterError(f"Dynamics 365 refused the {kind} ({r.status_code}).")
        made = r.json()
        return str(made.get("annotationid") if kind == "note" else made.get("activityid"))


def activity_body(payload: dict) -> dict:
    """A command's activity as Dataverse takes it: regarding the opportunity if one is named, else the contact."""
    kind = payload["activity_type"]
    if kind == "note":
        return {"subject": "Note", "notetext": payload["detail"],
                "objectid_contact@odata.bind": f"/contacts({payload['contact_external_id']})"}
    body = {"subject": payload["detail"][:200], "description": payload["detail"]}
    at = payload.get("occurred_at")
    if kind == "meeting":
        body.update({"scheduledstart": at, "scheduledend": at})
    else:
        body["actualend"] = at
    if payload.get("opportunity_external_id"):
        body["regardingobjectid_opportunity@odata.bind"] = f"/opportunities({payload['opportunity_external_id']})"
    else:
        body["regardingobjectid_contact@odata.bind"] = f"/contacts({payload['contact_external_id']})"
    return body


class DynamicsAdapter(Adapter):
    key = KEY
    name = "Dynamics 365"
    domains = frozenset({"crm"})
    capabilities = {"tax_lots": False, "transactions_days": None, "events": False}
    commands = frozenset({"crm.activity.create"})
    # A test can hand the live client a transport; nothing else sets it.
    transport = None

    async def _mock_raw(self, session, firm, connector) -> dict[str, list[dict]]:
        """The fixture, plus every activity the mock sink took, as Dynamics 365 would return it once recorded."""
        from sqlalchemy import select

        from app.models.foundation import FoundationCommand

        raw = fixture()
        sent = (await session.execute(select(FoundationCommand).where(
            FoundationCommand.firm_id == firm.id, FoundationCommand.source == KEY,
            FoundationCommand.kind == "crm.activity.create", FoundationCommand.status.in_(("pending", "confirmed")),
            FoundationCommand.external_id.is_not(None)))).scalars().all()
        for cmd in sent:
            p = cmd.payload or {}
            email = p.get("logged_by_email")
            uid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"mailto:{email}")) if email else None
            if uid and all(u["systemuserid"] != uid for u in raw["systemusers"]):
                raw["systemusers"].append({"systemuserid": uid, "internalemailaddress": email, "fullname": email})
            stamp = (cmd.sent_at or cmd.created_at)
            stamp = stamp.isoformat() if stamp else p.get("occurred_at")
            if p.get("activity_type") == "note":
                raw["annotations"].append({"annotationid": cmd.external_id, "subject": "Note", "notetext": p.get("detail"),
                                           "_objectid_value": p.get("contact_external_id"), "objecttypecode": "contact",
                                           "createdon": p.get("occurred_at"), "_ownerid_value": uid, "modifiedon": stamp})
                continue
            typecode = {v: k for k, v in ACTIVITY_TYPE.items()}[p.get("activity_type") or "task"]
            opp = p.get("opportunity_external_id")
            raw["activitypointers"].append({
                "activityid": cmd.external_id, "activitytypecode": typecode, "subject": (p.get("detail") or "")[:200],
                "description": p.get("detail"),
                "scheduledstart": p.get("occurred_at") if typecode == "appointment" else None,
                "actualend": None if typecode == "appointment" else p.get("occurred_at"),
                "createdon": p.get("occurred_at"),
                "_regardingobjectid_value": opp or p.get("contact_external_id"),
                f"_regardingobjectid_value@{LOOKUP}": "opportunity" if opp else "contact",
                "_ownerid_value": uid, "modifiedon": stamp})
        return raw

    async def pull(self, session, firm, connector, domain: str) -> Pulled:
        if domain != "crm":
            raise AdapterError(f"Dynamics 365 supplies the CRM, not {domain}.")
        if connector is None or connector.use_mock:
            pulled = to_contract(await self._mock_raw(session, firm, connector))
            pulled.mode = "mock"
            return pulled
        pulled = to_contract(await DataverseClient(connector.config or {}, transport=self.transport).read_all())
        pulled.mode = "live"
        return pulled

    async def send(self, session, firm, connector, command) -> None:
        """Deliver an activity. The mock sink records it as Dynamics 365 would and returns its id, and the next mock
        pull brings it back; live, Dataverse creates it and returns its id. Either way the command stays pending
        until the sync reads the record back."""
        if command.kind not in self.commands:
            raise AdapterError(f"Dynamics 365 takes no {command.kind}.")
        if connector is None or connector.use_mock:
            command.external_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"d365-mock:{command.id}"))
            return
        client = DataverseClient(connector.config or {}, transport=self.transport)
        command.external_id = await client.create_activity(activity_body(command.payload or {}),
                                                           (command.payload or {}).get("activity_type") or "task")
