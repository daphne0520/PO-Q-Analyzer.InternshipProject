## PDF Comparison Engine v2 (refined)
# Fixes applied after testing against a real ViTrox/NationGate PO + Quotation:
#   1. Switched PyPDF2 -> pdfplumber (PyPDF2's extraction scrambled the
#      PO's table into unreadable, out-of-order text; pdfplumber preserved
#      reading order and layout well enough to regex against).
#   2. Amount extraction no longer requires a currency symbol glued to the
#      number (e.g. "MYR414,720.00") - real invoices put the currency in a
#      column header, not on the totals line. Also now prefers an explicit
#      "Grand Total" line over a generic "Total" line, since a pre-discount
#      subtotal can be numerically larger than the grand total and was
#      previously winning by mistake.
#   3. Company-name extraction now looks 1-3 lines below a label (Ven Name /
#      Ship To / To / Bill To / From), since real docs often put a person's
#      name on the label line and the company name on the next line - and
#      falls back to the first "Sdn Bhd / Ltd / Inc"-style line in the
#      document (the letterhead) if nothing is found near the label.
#   4. Address extraction no longer requires a colon directly after
#      "Ship To" and pulls in the following continuation lines.
#   5. Date parsing now handles ordinal suffixes ("24th March 2026") and
#      dash-separated month names ("01-Apr-2026").
#   6. Quantity/unit price: tries pdfplumber's table extraction first
#      (works when the PDF has ruled table lines); falls back to a regex
#      for borderless tables like this PO ("UNIT 12.00 MYR 34,560.00").
#
# KNOWN REMAINING LIMITATION (not a code bug - a data gap):
#   This specific quotation's front page never states a delivery address at
#   all (only the vendor's own address + the buyer's email). No amount of
#   extraction logic can find a field that was never printed. If that
#   should NOT block an overall match, change the `is_match` rule below so
#   address is advisory rather than required.

import sys
import subprocess
import json
import re
import os
import tempfile
import requests
from difflib import SequenceMatcher
from datetime import datetime

try:
    import pdfplumber
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pdfplumber"])
    import pdfplumber


def get_input_value(name):
    val = globals().get(name, None)
    if val is not None:
        return val
    if "inputs" in globals():
        return globals()["inputs"].get(name)
    if "input" in globals():
        return globals()["input"].get(name)
    return None


po_file_val = get_input_value("po_file")
quotation_file_val = get_input_value("quotation_file")


def get_file_url_and_name(file_input):
    if not file_input:
        return None, ""
    try:
        data = json.loads(file_input) if isinstance(file_input, str) else file_input
        if isinstance(data, list) and len(data) > 0:
            return data[0].get("url"), data[0].get("filename", "")
    except Exception:
        pass
    return None, ""


po_url, po_filename = get_file_url_and_name(po_file_val)
q_url, q_filename = get_file_url_and_name(quotation_file_val)


def extract_text_and_tables_from_pdf(url, label):
    if not url:
        raise ValueError(f"{label} file URL is empty. Unable to download.")

    response = requests.get(url, timeout=15)
    response.raise_for_status()

    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(response.content)

        text = ""
        tables = []
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
                tables.extend(page.extract_tables() or [])

        if not text.strip():
            raise ValueError(
                f"No text extracted from {label}. The PDF may be scanned or encrypted."
            )
        return text, tables
    finally:
        os.remove(tmp_path)


# ---------------------------------------------------------------
# Amount extraction: prefer "grand total", then "total amount" /
# "amount due", then a plain "total" line; only fall back to any
# number in the document as a last resort.
# ---------------------------------------------------------------
AMOUNT_PATTERN = r'([\d,]+\.\d{2,5})'

KEYWORD_TIERS = [
    re.compile(r'grand\s*total', re.IGNORECASE),
    re.compile(r'(total\s*amount|amount\s*due|总计|合计)', re.IGNORECASE),
    re.compile(r'\btotal\b', re.IGNORECASE),
]


def extract_amount(text, label):
    lines = text.splitlines()
    for keyword_re in KEYWORD_TIERS:
        candidates = []
        for line in lines:
            if keyword_re.search(line):
                found = re.findall(AMOUNT_PATTERN, line)
                if found:
                    candidates.append(float(found[-1].replace(",", "")))
        if candidates:
            return max(candidates)

    all_matches = re.findall(AMOUNT_PATTERN, text)
    if all_matches:
        return float(all_matches[-1].replace(",", ""))

    raise ValueError(
        f"No amount found in {label}. Please check PDF format or update extraction rules."
    )


# ---------------------------------------------------------------
# Company name extraction (buyer or seller depending on labels passed)
# ---------------------------------------------------------------
COMPANY_SUFFIX_RE = re.compile(r'(sdn\.?\s*bhd\.?|bhd\.?|pte\.?\s*ltd\.?|ltd\.?|inc\.?|corp\.?|llc)', re.IGNORECASE)


def extract_company_near_label(text, label_patterns, lookahead=3):
    lines = text.splitlines()
    fallback_remainder = None
    for i, line in enumerate(lines):
        for lp in label_patterns:
            m = re.search(lp, line, re.IGNORECASE)
            if not m:
                continue
            remainder = line[m.end():].strip(" :\t")
            if remainder and COMPANY_SUFFIX_RE.search(remainder):
                return remainder
            for j in range(i, min(i + lookahead, len(lines))):
                if COMPANY_SUFFIX_RE.search(lines[j]):
                    candidate = re.sub(
                        r'^(Ven\s*Name|Ship\s*To|Bill\s*To|To|From)\s*:?\s*',
                        "", lines[j], flags=re.IGNORECASE,
                    )
                    return candidate.strip()
            if remainder and fallback_remainder is None:
                fallback_remainder = remainder
    for line in lines[:15]:
        if COMPANY_SUFFIX_RE.search(line):
            return line.strip()
    return fallback_remainder


# ---------------------------------------------------------------
# Address extraction: label may or may not have a trailing colon;
# pulls in continuation lines until a clearly unrelated field starts.
# ---------------------------------------------------------------
def extract_address(text, label_patterns):
    lines = text.splitlines()
    stop_re = re.compile(r'^(SO\s*#|PO\s*#|Payment\s*Term|Shipment\s*Method)', re.IGNORECASE)
    for i, line in enumerate(lines):
        for lp in label_patterns:
            m = re.search(lp, line, re.IGNORECASE)
            if m:
                remainder = line[m.end():].strip(" :\t")
                collected = [remainder] if remainder else []
                j, count = i + 1, 0
                while j < len(lines) and count < 2:
                    nxt = lines[j].strip()
                    if not nxt:
                        break
                    # Truncate at an embedded unrelated field, even if it's
                    # not at the very start of the line (columns often run
                    # together on one line in borderless layouts).
                    cut = re.search(r'(SO\s*#|PO\s*#|Payment\s*Term|Shipment\s*Method)', nxt, re.IGNORECASE)
                    if cut:
                        nxt = nxt[:cut.start()].strip().rstrip(",")
                        if nxt:
                            collected.append(nxt)
                        break
                    collected.append(nxt)
                    j += 1
                    count += 1
                if collected:
                    return ", ".join(c for c in collected if c)
    return None


def fuzzy_similarity(a, b):
    def norm(v):
        if not v:
            return ""
        v = v.lower()
        v = re.sub(r'[.,;:()\-]', ' ', v)
        v = re.sub(r'\s+', ' ', v).strip()
        return v
    a_n, b_n = norm(a), norm(b)
    if not a_n or not b_n:
        return 0.0
    return SequenceMatcher(None, a_n, b_n).ratio()


def extract_field(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            groups = [g for g in match.groups() if g is not None]
            if groups:
                return groups[0].strip()
    return None


def extract_number(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            groups = [g for g in match.groups() if g is not None]
            if groups:
                raw = groups[0].replace(",", "").replace("%", "").strip()
                try:
                    return float(raw)
                except ValueError:
                    continue
    return None


# ---------------------------------------------------------------
# Dates: handle ordinal suffixes and dash-separated month names
# ---------------------------------------------------------------
DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
    "%d-%b-%Y", "%d-%B-%Y",
]


def parse_date(raw_date):
    if not raw_date:
        return None
    raw = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', raw_date.strip(), flags=re.IGNORECASE)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


DATE_PATTERNS = [
    r'(?:Date|PO\s*Date|Quotation\s*Date)\s?:?\s?([0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{2,4})',
    r'(?:Date|PO\s*Date|Quotation\s*Date)\s?:?\s?([0-9]{4}-[0-9]{2}-[0-9]{2})',
    r'(?:Date|PO\s*Date|Quotation\s*Date)\s?:?\s?([0-9]{1,2}-[A-Za-z]{3,9}-[0-9]{4})',
    r'(?:Date|PO\s*Date|Quotation\s*Date)\s?:?\s?([0-9]{1,2}(?:st|nd|rd|th)?\s?[A-Za-z]+\s?[0-9]{4})',
]


# ---------------------------------------------------------------
# Quantity / unit price: try ruled tables first, fall back to a
# regex for borderless tables (e.g. "UNIT 12.00 MYR 34,560.00")
# ---------------------------------------------------------------
def _normalize_header_cell(cell):
    if not cell:
        return ""
    return re.sub(r'\s+', ' ', str(cell)).strip().lower()


def extract_column_from_tables(tables, header_aliases):
    """Generic ruled-table column extractor. header_aliases is a list of
    substrings to match against a (whitespace-normalized) header cell,
    e.g. ["qty", "quantity"] or ["unit price"]."""
    for table in tables:
        header_row = table[0] if table else None
        if not header_row:
            continue
        norm_header = [_normalize_header_cell(c) for c in header_row]
        col_idx = None
        for idx, cell in enumerate(norm_header):
            if any(alias in cell for alias in header_aliases):
                col_idx = idx
                break
        if col_idx is None:
            continue
        for row in table[1:]:
            if col_idx < len(row) and row[col_idx]:
                try:
                    return float(str(row[col_idx]).replace(",", ""))
                except ValueError:
                    continue
    return None


def extract_quantity_from_tables(tables):
    return extract_column_from_tables(tables, ["qty", "quantity"])


def extract_quantity_fallback(text):
    m = re.search(r'\b(?:UNIT|PCS|SET|LOT|NOS)\b\s+([\d,]+\.?\d*)\s+[A-Z]{3}\s+[\d,]+\.\d+', text)
    if m:
        return float(m.group(1).replace(",", ""))
    m2 = re.search(r'(?:Qty|Quantity)\s?:?\s?([\d,]+(?:\.\d+)?)', text, re.IGNORECASE)
    if m2:
        return float(m2.group(1).replace(",", ""))
    return None


# ---------------------------------------------------------------
# Unit price: same two-tier strategy as quantity - ruled table first,
# then a regex for borderless single-line-item tables.
# ---------------------------------------------------------------
def extract_unit_price_from_tables(tables):
    return extract_column_from_tables(tables, ["unit price"])


def extract_unit_price_fallback(text):
    # e.g. "UNIT 12.00 MYR 34,560.00000 ST0 0 0.00 414,720.00000"
    #                        ^^^^^^^^^^^ unit price is the number right
    #                        after the currency code, before tax columns.
    m = re.search(r'\b(?:UNIT|PCS|SET|LOT|NOS)\b\s+[\d,]+\.?\d*\s+[A-Z]{3}\s+([\d,]+\.\d+)', text)
    if m:
        return float(m.group(1).replace(",", ""))
    m2 = re.search(r'Unit\s*Price\s?:?\s?(?:RM|MYR|\$|USD)?\s?([\d,]+\.\d{2})', text, re.IGNORECASE)
    if m2:
        return float(m2.group(1).replace(",", ""))
    return None


TEXT_MATCH_THRESHOLD = 0.85
AMOUNT_TOLERANCE = 0.01
QUANTITY_TOLERANCE = 0
DISCOUNT_TOLERANCE = 0.01
TAX_TOLERANCE = 0.01


def evaluate_field(label, po_val, q_val, is_match_fn, describe_fn):
    if po_val is None and q_val is None:
        return True, None
    if po_val is None or q_val is None:
        return False, f"{label} present on only one document (PO: {po_val}, Quotation: {q_val})"
    if is_match_fn(po_val, q_val):
        return True, None
    return False, f"{label} mismatch ({describe_fn(po_val, q_val)})"


try:
    po_text, po_tables = extract_text_and_tables_from_pdf(po_url, "PO")
    q_text, q_tables = extract_text_and_tables_from_pdf(q_url, "Quotation")

    buyer_labels = [r'Ship\s*To\b', r'\bTo:', r'Bill\s*To']
    seller_labels = [r'Ven\s*Name', r'From:']

    po_buyer_company = extract_company_near_label(po_text, buyer_labels) or "Unknown"
    q_buyer_company = extract_company_near_label(q_text, buyer_labels) or "Unknown"

    po_seller_company = extract_company_near_label(po_text, seller_labels) or "Unknown"
    q_seller_company = extract_company_near_label(q_text, seller_labels) or "Unknown"

    po_delivery_address = extract_address(po_text, [r'Address\s*:']) or "Not Found"
    q_delivery_address = extract_address(q_text, [r'Address\s*:']) or "Not Found"

    buyer_company_similarity = fuzzy_similarity(po_buyer_company, q_buyer_company)
    buyer_company_match = buyer_company_similarity >= TEXT_MATCH_THRESHOLD

    seller_company_similarity = fuzzy_similarity(po_seller_company, q_seller_company)
    seller_company_match = seller_company_similarity >= TEXT_MATCH_THRESHOLD

    address_similarity = fuzzy_similarity(po_delivery_address, q_delivery_address)
    address_match = address_similarity >= TEXT_MATCH_THRESHOLD

    po_amount = extract_amount(po_text, "PO")
    q_amount = extract_amount(q_text, "Quotation")
    amount_match = abs(po_amount - q_amount) <= AMOUNT_TOLERANCE

    # Aliases so downstream consumers that expect "total_amount" naming
    # (rather than "amount") find the same figure under either key.
    po_total_amount, q_total_amount = po_amount, q_amount
    total_amount_match = amount_match

    po_unit_price = extract_unit_price_from_tables(po_tables) or extract_unit_price_fallback(po_text)
    q_unit_price = extract_unit_price_from_tables(q_tables) or extract_unit_price_fallback(q_text)

    unit_price_match, unit_price_note = evaluate_field(
        "Unit price", po_unit_price, q_unit_price,
        lambda a, b: abs(a - b) <= AMOUNT_TOLERANCE,
        lambda a, b: f"PO: {a}, Quotation: {b}",
    )

    po_date_raw = extract_field(po_text, DATE_PATTERNS)
    q_date_raw = extract_field(q_text, DATE_PATTERNS)
    po_date = parse_date(po_date_raw)
    q_date = parse_date(q_date_raw)

    date_match, date_note = evaluate_field(
        "Validation date", po_date or po_date_raw, q_date or q_date_raw,
        lambda a, b: (po_date == q_date) if (po_date and q_date) else (po_date_raw == q_date_raw),
        lambda a, b: f"PO: {po_date_raw}, Quotation: {q_date_raw}",
    )

    po_quantity = extract_quantity_from_tables(po_tables) or extract_quantity_fallback(po_text)
    q_quantity = extract_quantity_from_tables(q_tables) or extract_quantity_fallback(q_text)

    quantity_match, quantity_note = evaluate_field(
        "Quantity", po_quantity, q_quantity,
        lambda a, b: abs(a - b) <= QUANTITY_TOLERANCE,
        lambda a, b: f"PO: {a}, Quotation: {b}",
    )

    description_patterns = [r'Description\s?:\s?([^\n]+)', r'Item\s?:\s?([^\n]+)']
    po_description = extract_field(po_text, description_patterns)
    q_description = extract_field(q_text, description_patterns)
    description_similarity = fuzzy_similarity(po_description, q_description) if (po_description and q_description) else 0.0

    description_match, description_note = evaluate_field(
        "Description", po_description, q_description,
        lambda a, b: fuzzy_similarity(a, b) >= TEXT_MATCH_THRESHOLD,
        lambda a, b: f"PO: '{a}', Quotation: '{b}', Similarity: {description_similarity:.0%}",
    )

    discount_patterns = [r'Discount\s?:?\s?(?:RM|MYR|\$|USD)?\s?([\d,]+\.?\d*)\s?%?']
    po_discount = extract_number(po_text, discount_patterns)
    q_discount = extract_number(q_text, discount_patterns)

    discount_match, discount_note = evaluate_field(
        "Discount", po_discount, q_discount,
        lambda a, b: abs(a - b) <= DISCOUNT_TOLERANCE,
        lambda a, b: f"PO: {a}, Quotation: {b}",
    )

    tax_patterns = [r'(?:SST|GST|VAT|Tax)\D{0,15}?([\d,]+\.\d{2})']
    po_tax = extract_number(po_text, tax_patterns)
    q_tax = extract_number(q_text, tax_patterns)

    tax_match, tax_note = evaluate_field(
        "Tax", po_tax, q_tax,
        lambda a, b: abs(a - b) <= TAX_TOLERANCE,
        lambda a, b: f"PO: {a}, Quotation: {b}",
    )

    is_match = 1 if (
        amount_match and buyer_company_match and seller_company_match and address_match
        and date_match and quantity_match and description_match
        and discount_match and tax_match and unit_price_match
    ) else 0

    status_text = "Success" if is_match == 1 else "Failed"

    mismatch_notes = []
    if not amount_match:
        mismatch_notes.append(
            f"Amount mismatch (PO: {po_amount:.2f}, Quotation: {q_amount:.2f}, Diff: {po_amount - q_amount:+.2f})"
        )
    if not buyer_company_match:
        mismatch_notes.append(
            f"Buyer company mismatch (PO: '{po_buyer_company}', Quotation: '{q_buyer_company}', Similarity: {buyer_company_similarity:.0%})"
        )
    if not seller_company_match:
        mismatch_notes.append(
            f"Seller company mismatch (PO: '{po_seller_company}', Quotation: '{q_seller_company}', Similarity: {seller_company_similarity:.0%})"
        )
    if not address_match:
        mismatch_notes.append(
            f"Delivery address mismatch (PO: '{po_delivery_address}', Quotation: '{q_delivery_address}', Similarity: {address_similarity:.0%})"
        )
    for matched, note in [
        (date_match, date_note), (quantity_match, quantity_note),
        (description_match, description_note), (discount_match, discount_note),
        (tax_match, tax_note), (unit_price_match, unit_price_note),
    ]:
        if not matched and note:
            mismatch_notes.append(note)

    ai_recommendation = (
        f"PO and Quotation matched successfully. Approved Total: MYR {po_amount:.2f}"
        if is_match == 1 else "Mismatch detected! " + "; ".join(mismatch_notes)
    )

    output = {
        "po_buyer_company": po_buyer_company, "q_buyer_company": q_buyer_company,
        "buyer_company_match": buyer_company_match, "buyer_company_similarity": round(buyer_company_similarity, 2),
        "po_seller_company": po_seller_company, "q_seller_company": q_seller_company,
        "seller_company_match": seller_company_match, "seller_company_similarity": round(seller_company_similarity, 2),
        "po_delivery_address": po_delivery_address, "q_delivery_address": q_delivery_address,
        "address_match": address_match, "address_similarity": round(address_similarity, 2),
        "po_amount": po_amount, "q_amount": q_amount, "amount_match": amount_match,
        "po_total_amount": po_total_amount, "q_total_amount": q_total_amount, "total_amount_match": total_amount_match,
        "po_unit_price": po_unit_price, "q_unit_price": q_unit_price, "unit_price_match": unit_price_match,
        "po_date": po_date_raw, "q_date": q_date_raw, "date_match": date_match,
        "po_quantity": po_quantity, "q_quantity": q_quantity, "quantity_match": quantity_match,
        "po_description": po_description, "q_description": q_description,
        "description_match": description_match, "description_similarity": round(description_similarity, 2),
        "po_discount": po_discount, "q_discount": q_discount, "discount_match": discount_match,
        "po_tax": po_tax, "q_tax": q_tax, "tax_match": tax_match,
        "is_match": is_match, "status_text": status_text, "ai_recommendation": ai_recommendation,
    }

except Exception as e:
    output = {
        "po_buyer_company": "Unknown", "q_buyer_company": "Unknown", "buyer_company_match": False, "buyer_company_similarity": 0.0,
        "po_seller_company": "Unknown", "q_seller_company": "Unknown", "seller_company_match": False, "seller_company_similarity": 0.0,
        "po_delivery_address": "Not Found", "q_delivery_address": "Not Found", "address_match": False, "address_similarity": 0.0,
        "po_amount": 0.0, "q_amount": 0.0, "amount_match": False,
        "po_total_amount": 0.0, "q_total_amount": 0.0, "total_amount_match": False,
        "po_unit_price": None, "q_unit_price": None, "unit_price_match": False,
        "po_date": None, "q_date": None, "date_match": False,
        "po_quantity": None, "q_quantity": None, "quantity_match": False,
        "po_description": None, "q_description": None, "description_match": False, "description_similarity": 0.0,
        "po_discount": None, "q_discount": None, "discount_match": False,
        "po_tax": None, "q_tax": None, "tax_match": False,
        "is_match": 0, "status_text": "Error",
        "ai_recommendation": f"Processing failed: {str(e)}",
    }

if __name__ == "__main__":
    import json as _json
    print(_json.dumps(output, indent=2, default=str))
