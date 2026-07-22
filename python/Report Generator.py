import sys
import subprocess
import base64
import io
import re
from datetime import datetime
from zoneinfo import ZoneInfo

# All timestamps in this report (reviewed_at, Generated line, output
# filename, generated_at) are in Malaysia time, regardless of the time
# zone the server executing this script happens to be set to.
MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")


def now_my():
    return datetime.now(MY_TZ)


try:
    import reportlab
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "reportlab"])

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


def get_input_value(name):
    val = globals().get(name, None)
    if val is not None:
        return val
    if "inputs" in globals():
        return globals()["inputs"].get(name)
    if "input" in globals():
        return globals()["input"].get(name)
    return None


ticket_record_id = get_input_value("ticket_record_id") or ""
comparison_record = get_input_value("comparison_record") or {}
existing_reports = get_input_value("existing_reports") or []

final_buyer_company = get_input_value("final_buyer_company")
final_seller_company = get_input_value("final_seller_company")
final_delivery_address = get_input_value("final_delivery_address")
final_unit_price = get_input_value("final_unit_price")
final_total_amount = get_input_value("final_total_amount")

final_date = get_input_value("final_date")
final_quantity = get_input_value("final_quantity")
final_description = get_input_value("final_description")
final_discount = get_input_value("final_discount")
final_tax = get_input_value("final_tax")

reviewer_name = get_input_value("reviewer_name") or ""


def build_updated_record(original, overrides):
    updated = dict(original)

    # Bridge field-name gap with the comparison engine: it outputs
    # po_amount/q_amount, not po_total_amount/q_total_amount, and never
    # outputs seller company or unit price at all. Carry over whatever the
    # comparison engine actually gave us before applying reviewer overrides,
    # so the report doesn't render blank rows for fields nobody typed in.
    if "po_total_amount" not in updated and "po_amount" in updated:
        updated["po_total_amount"] = updated["po_amount"]
        updated["q_total_amount"] = updated.get("q_amount")

    if overrides.get("final_buyer_company"):
        updated["po_buyer_company"] = overrides["final_buyer_company"]
        updated["q_buyer_company"] = overrides["final_buyer_company"]
        updated["buyer_company_match"] = True

    if overrides.get("final_seller_company"):
        updated["po_seller_company"] = overrides["final_seller_company"]
        updated["q_seller_company"] = overrides["final_seller_company"]
        updated["seller_company_match"] = True

    if overrides.get("final_delivery_address"):
        updated["po_delivery_address"] = overrides["final_delivery_address"]
        updated["q_delivery_address"] = overrides["final_delivery_address"]
        updated["address_match"] = True

    if overrides.get("final_unit_price") is not None:
        updated["po_unit_price"] = overrides["final_unit_price"]
        updated["q_unit_price"] = overrides["final_unit_price"]
        updated["unit_price_match"] = True

    if overrides.get("final_total_amount") is not None:
        updated["po_total_amount"] = overrides["final_total_amount"]
        updated["q_total_amount"] = overrides["final_total_amount"]
        updated["total_amount_match"] = True

    if overrides.get("final_date"):
        updated["po_date"] = overrides["final_date"]
        updated["q_date"] = overrides["final_date"]
        updated["date_match"] = True

    if overrides.get("final_quantity") is not None:
        updated["po_quantity"] = overrides["final_quantity"]
        updated["q_quantity"] = overrides["final_quantity"]
        updated["quantity_match"] = True

    if overrides.get("final_description"):
        updated["po_description"] = overrides["final_description"]
        updated["q_description"] = overrides["final_description"]
        updated["description_match"] = True
        updated["description_similarity"] = 1.0

    if overrides.get("final_discount") is not None:
        updated["po_discount"] = overrides["final_discount"]
        updated["q_discount"] = overrides["final_discount"]
        updated["discount_match"] = True

    if overrides.get("final_tax") is not None:
        updated["po_tax"] = overrides["final_tax"]
        updated["q_tax"] = overrides["final_tax"]
        updated["tax_match"] = True

    updated["is_match"] = 1
    updated["status_text"] = "Success"
    updated["reviewed_by"] = overrides.get("reviewer_name", "")
    updated["reviewed_at"] = now_my().isoformat()

    updated["ai_recommendation"] = (
        f"Reviewed and confirmed by {overrides.get('reviewer_name', '')} on "
        f"{now_my().strftime('%Y-%m-%d %H:%M')}. "
        f"All fields verified correct."
    )

    return updated


overrides = {
    "final_buyer_company": final_buyer_company,
    "final_seller_company": final_seller_company,
    "final_delivery_address": final_delivery_address,
    "final_unit_price": final_unit_price,
    "final_total_amount": final_total_amount,
    "final_date": final_date,
    "final_quantity": final_quantity,
    "final_description": final_description,
    "final_discount": final_discount,
    "final_tax": final_tax,
    "reviewer_name": reviewer_name,
}

updated_comparison_record = build_updated_record(comparison_record, overrides)


def generate_report_id(ticket_record_id, existing_reports):
    same_ticket = [
        r for r in existing_reports
        if r.get("ticket_record_id") == ticket_record_id
    ]

    if same_ticket:
        base_id = re.sub(
            r"_V\d+$",
            "",
            same_ticket[0].get("report_id", "")
        )
        version_count = len(same_ticket)
        return f"{base_id}_V{version_count}", version_count + 1

    all_base_nums = []

    for r in existing_reports:
        m = re.match(r"RPT_(\d+)", r.get("report_id", ""))
        if m:
            all_base_nums.append(int(m.group(1)))

    next_num = (max(all_base_nums) + 1) if all_base_nums else 1

    return f"RPT_{next_num:02d}", 1


report_id, report_version = generate_report_id(
    ticket_record_id,
    existing_reports,
)


def generate_pdf_report(data: dict) -> str:
    buf = io.BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
    )

    styles = getSampleStyleSheet()

    brand_dark = colors.HexColor("#083346")
    green = colors.HexColor("#166534")
    gray = colors.HexColor("#475569")

    title_style = ParagraphStyle(
        "TitleX",
        parent=styles["Title"],
        textColor=brand_dark,
        fontSize=17,
        spaceAfter=2,
    )

    sub_style = ParagraphStyle(
        "SubX",
        parent=styles["Normal"],
        textColor=gray,
        fontSize=9,
        spaceAfter=4,
    )

    reviewed_style = ParagraphStyle(
        "RevX",
        parent=styles["Normal"],
        textColor=colors.HexColor("#0F6E56"),
        fontSize=9,
        spaceAfter=10,
    )

    section_style = ParagraphStyle(
        "SectionX",
        parent=styles["Heading2"],
        textColor=brand_dark,
        fontSize=11,
        spaceBefore=14,
        spaceAfter=6,
    )

    normal = styles["Normal"]

    story = []

    story.append(
        Paragraph(
            "AI PO / Quotation Comparison Report (Reviewed)",
            title_style,
        )
    )

    story.append(
        Paragraph(
            f"Generated: {now_my().strftime('%Y-%m-%d %H:%M')} (Malaysia Time, UTC+8) | Report ID: {report_id}",
            sub_style,
        )
    )

    story.append(
        Paragraph(
            f"Reviewed by {data.get('reviewed_by','N/A')} on {data.get('reviewed_at','N/A')}",
            reviewed_style,
        )
    )

    status_table = Table(
        [[
            Paragraph(
                "<b>Status: Success (Reviewed &amp; Confirmed)</b>",
                ParagraphStyle(
                    "st",
                    textColor=colors.white,
                    fontSize=11,
                ),
            )
        ]],
        colWidths=[160 * mm],
    )

    status_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), green),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))

    story.append(status_table)
    story.append(Spacer(1, 10))

    table_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
        ("TEXTCOLOR", (0, 0), (-1, 0), brand_dark),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ])

    story.append(Paragraph("Final Confirmed Details", section_style))

    def format_discount(val):
        # Always display as a percentage, however the value was stored
        # (comparison engine and reviewer overrides may both hand us a
        # plain percent number like 20, or a fraction like 0.2).
        if val is None or val == "":
            return ""
        try:
            num = float(val)
        except (TypeError, ValueError):
            return str(val)
        if -1 <= num <= 1 and num != 0:
            num *= 100
        return f"{num:g}%"

    cell_style = ParagraphStyle(
        "cell",
        parent=normal,
        fontSize=9,
        leading=12,
    )

    def cell(value):
        # Wrap every value in a Paragraph so long text (address,
        # description) wraps inside the column instead of overflowing
        # past the table border.
        return Paragraph(str(value) if value is not None else "", cell_style)

    header_style = ParagraphStyle(
        "cellHeader",
        parent=cell_style,
        textColor=brand_dark,
        fontName="Helvetica-Bold",
    )

    rows = [
        [Paragraph("Field", header_style), Paragraph("Confirmed Value", header_style)],
        [cell("Buyer company"), cell(data.get("po_buyer_company", ""))],
        [cell("Seller company"), cell(data.get("po_seller_company", ""))],
        [cell("Delivery address"), cell(data.get("po_delivery_address", ""))],
        [cell("Date"), cell(data.get("po_date", ""))],
        [cell("Quantity"), cell(data.get("po_quantity", ""))],
        [cell("Description"), cell(data.get("po_description", ""))],
        [cell("Unit price"), cell(data.get("po_unit_price", ""))],
        [cell("Discount"), cell(format_discount(data.get("po_discount")))],
        [cell("Tax"), cell(data.get("po_tax", ""))],
        [cell("Total amount"), cell(data.get("po_total_amount", ""))],
    ]

    t = Table(rows, colWidths=[50 * mm, 122 * mm])
    t.setStyle(table_style)

    story.append(t)

    story.append(Paragraph("Reviewer Note", section_style))
    story.append(
        HRFlowable(
            width="100%",
            color=colors.HexColor("#E2E8F0"),
            thickness=0.5,
        )
    )

    story.append(Spacer(1, 6))

    story.append(
        Paragraph(
            data.get("ai_recommendation", ""),
            ParagraphStyle(
                "rec",
                parent=normal,
                fontSize=10,
                leading=15,
            ),
        )
    )

    doc.build(story)

    pdf_bytes = buf.getvalue()
    buf.close()

    return base64.b64encode(pdf_bytes).decode("utf-8"), len(pdf_bytes)


try:
    pdf_base64, pdf_size = generate_pdf_report(updated_comparison_record)

    file_name = f"{report_id}_{now_my().strftime('%Y%m%d_%H%M%S')}.pdf"

    output = {
        "updated_comparison_record": updated_comparison_record,
        "report_id": report_id,
        "ticket_record_id": ticket_record_id,
        "report_file_base64": pdf_base64,
        "report_file_name": file_name,
        "report_version": report_version,
        "status_text": "Success",
        "is_reviewed": True,
        "generated_at": now_my().isoformat(),
        "report_generated": True,
    }

except Exception as e:
    output = {
        "updated_comparison_record": updated_comparison_record,
        "report_id": report_id,
        "ticket_record_id": ticket_record_id,
        "report_file_base64": "",
        "report_file_name": "",
        "report_version": report_version,
        "status_text": "Success",
        "is_reviewed": True,
        "generated_at": now_my().isoformat(),
        "report_generated": False,
        "error": str(e),
    }
