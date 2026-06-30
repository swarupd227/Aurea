"""Unit tests for the Acquire & Onboard engines — document intelligence, AML screening,
book reconciliation. Pure functions, no database."""
from app.aurea_core.document_intel import extract
from app.aurea_core.screening import screen, screen_parties
from app.aurea_core.book_recon import name_similarity, reconcile
from app.aurea_core import sample_docs


def test_passport_extraction():
    text = sample_docs.passport("Daniel Okonkwo")
    res = extract("passport", text)
    assert res.fields["full_name"] == "Daniel Okonkwo"
    assert "date_of_birth" in res.fields
    assert res.confidence > 0.9
    assert res.missing == []


def test_trust_deed_extracts_trustee_list():
    text = sample_docs.trust_deed("Sokolov Family Trust", settlor="Viktor Sokolov",
                                  trustees=["Viktor Sokolov", "Meridian Trustees Ltd"],
                                  beneficiaries=["children"])
    res = extract("trust_deed", text)
    assert "Viktor Sokolov" in res.fields["trustees"]
    assert isinstance(res.fields["trustees"], list)


def test_extraction_flags_missing_fields():
    res = extract("passport", "Full Name: Jane Doe\n")  # only a name
    assert "document_number" in res.missing
    assert res.confidence < 0.9


def test_screening_clear_review_blocked():
    assert screen("Daniel Okonkwo").status == "clear"
    # PEP → review
    pep = screen("Viktor Sokolov")
    assert pep.status == "review"
    assert pep.hits and pep.hits[0].category == "pep"
    # Sanctions → blocked
    sanc = screen("Imelda Castellanos")
    assert sanc.status == "blocked"
    assert sanc.hits[0].severity == "high"


def test_screen_parties_aggregates_worst_status():
    res = screen_parties(["Daniel Okonkwo", "Viktor Sokolov"])  # one clean, one PEP
    assert res["status"] == "review"
    assert res["n_hits"] >= 1


def test_name_similarity_handles_the_prefix():
    assert name_similarity("Patel Household", "The Patel Household") > 0.95
    assert name_similarity("Patel Household", "Henderson Family") < 0.5


def test_reconcile_merge_create_and_conflict():
    res = reconcile(
        inbound_clients=[{"name": "Patel Household"}, {"name": "New Family"}],
        existing_households=[{"id": "h1", "name": "The Patel Household"}],
        inbound_securities=[{"symbol": "AAPL", "name": "Apple"}, {"symbol": "TSLA", "name": "Tesla"}],
        master_symbols={"AAPL": "Apple Inc."},
        inbound_holdings=[
            {"client": "Patel Household", "symbol": "AAPL", "quantity": 250},  # conflict
            {"client": "New Family", "symbol": "TSLA", "quantity": 100},
        ],
        existing_by_client_symbol={"patel household|aapl": 200.0},
    )
    actions = {m["inbound"]: m["action"] for m in res.client_mappings}
    assert actions["Patel Household"] == "merge"
    assert actions["New Family"] == "create"

    sec = {m["inbound"]: m["action"] for m in res.security_mappings}
    assert sec["AAPL"] == "map"
    assert sec["TSLA"] == "create"

    assert len(res.holding_conflicts) == 1
    assert res.holding_conflicts[0]["symbol"] == "AAPL"
    assert res.holding_conflicts[0]["delta"] == 50
