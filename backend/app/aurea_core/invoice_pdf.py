"""The client's copy of an invoice, as a PDF.

A bill the client cannot check is a bill they will dispute, so the document shows its
working: the bands, the balance each account contributed, the date it was valued on and how
old the oldest price was, and - plainly - that a snapshot was used. Credit notes show what
they offset. Helvetica is a Latin-1 font, so text is made safe rather than silently dropped.
"""
from __future__ import annotations

from app.core.db import utcnow


def _a(text) -> str:
    return str(text if text is not None else "").encode("latin-1", "replace").decode("latin-1")


def render_invoice_pdf(view: dict, *, firm_name: str, accent: str = "#c8a35e", base_currency: str = "NZD") -> bytes:
    from fpdf import FPDF

    calc = view.get("calculation") or {}
    cur = view["currency"]
    credit = view["kind"] == "credit_note"

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    try:
        r, g, b = int(accent[1:3], 16), int(accent[3:5], 16), int(accent[5:7], 16)
    except (ValueError, IndexError):
        r, g, b = 200, 163, 94
    pdf.set_fill_color(r, g, b)
    pdf.rect(0, 0, 210, 18, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_xy(10, 4)
    pdf.cell(0, 10, _a(f"{firm_name}  -  {'Credit note' if credit else 'Fee invoice'}"), ln=True)

    pdf.set_text_color(30, 30, 30)
    pdf.set_y(24)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, _a(view.get("household_name") or view["mandate_name"]), ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, _a(f"{view.get('number') or 'Not yet issued'}   |   Period {view['period_start']} to {view['period_end']}"), ln=True)
    if credit and view.get("credit_of"):
        pdf.cell(0, 6, _a(f"Offsets {view['credit_of']}" + (f" - {view['credit_reason']}" if view.get("credit_reason") else "")), ln=True)
    pdf.ln(4)

    pdf.set_text_color(30, 30, 30)
    pdf.set_font("Helvetica", "B", 13)
    label = "Credited" if credit else "Fee for the period"
    pdf.cell(0, 8, _a(f"{label}:  {cur} {abs(view['amount']):,.2f}"), ln=True)
    pdf.ln(2)

    if not credit:
        sched = calc.get("schedule") or {}
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "How this was worked out", ln=True)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, _a(f"  Fee schedule: {sched.get('name', '-')}"), ln=True)
        pdf.cell(0, 6, _a(f"  Billed {calc.get('billing_frequency', '')} in {calc.get('billing_method', '')}"), ln=True)
        value = calc.get("billable_value") or 0
        pdf.cell(0, 6, _a(f"  Balance billed on: {cur} {value:,.2f}"), ln=True)
        if calc.get("group_value"):
            pdf.cell(0, 6, _a(f"  Household balance (combined, to reach the breakpoint): {cur} {calc['group_value']:,.2f}"), ln=True)
        for row in calc.get("breakdown") or []:
            pdf.cell(0, 6, _a(f"    {row['band']}: {cur} {row['amount']:,.2f}"), ln=True)
        if calc.get("annual_fee") is not None:
            pdf.cell(0, 6, _a(f"  Annual fee {cur} {calc['annual_fee']:,.2f} ({calc.get('effective_bps', 0):g} bps), "
                              f"one of {calc.get('periods_per_year', '')} periods"), ln=True)
        frac = calc.get("period_fraction")
        if frac is not None and frac < 1:
            pdf.cell(0, 6, _a(f"  In force for {frac:.0%} of the period - prorated"), ln=True)
        pdf.ln(3)

        accounts = calc.get("accounts") or []
        if accounts:
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 8, "Accounts included", ln=True)
            pdf.set_font("Helvetica", "", 10)
            for a in accounts:
                pdf.cell(0, 6, _a(f"  {a['account_name']}: {cur} {a['value']:,.2f}  (cash {a['cash']:,.2f})"), ln=True)
            pdf.ln(3)

        val = calc.get("valuation") or {}
        if val:
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 8, "Valuation", ln=True)
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(0, 6, _a(f"  A single valuation taken on {val.get('basis_date')}; the oldest price used is "
                              f"{val.get('oldest_price_date') or 'not recorded'}."), ln=True)
            if val.get("synthetic_price_symbols"):
                pdf.cell(0, 6, _a("  Valued on estimated prices: " + ", ".join(val["synthetic_price_symbols"])), ln=True)
            if val.get("declared_price_symbols"):
                pdf.cell(0, 6, _a("  Valued on the manager's stated valuation, not a market price: "
                                  + ", ".join(val["declared_price_symbols"])), ln=True)
            if val.get("unpriced_symbols"):
                pdf.cell(0, 6, _a("  No market price, valued at the recorded amount: " + ", ".join(val["unpriced_symbols"])), ln=True)
            pdf.ln(3)

        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Payment", ln=True)
        pdf.set_font("Helvetica", "", 10)
        status = {"posted": "Taken from the account's cash.", "insufficient_cash": "Not yet paid - the account's cash did not cover it.",
                  "nothing_due": "Nothing due."}.get(view.get("debit_status") or "", "Not yet taken.")
        pdf.cell(0, 6, _a("  " + status), ln=True)

    pdf.set_y(-20)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(160, 160, 160)
    pdf.cell(0, 6, _a(f"{firm_name}  |  Issued {utcnow():%d %B %Y}  |  Confidential"), align="C")
    return bytes(pdf.output())
