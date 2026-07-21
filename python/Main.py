## PDF Comparison Engine v2 (pdfplumber-based, broadened patterns)

import sys, subprocess, json, re, os, tempfile, requests
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


def extract_text_from_pdf(url, label):
    """CHANGED: uses pdfplumber instead of PyPDF2 - it keeps line breaks that
    match the document's visual layout, which the regexes below depend on."""
    if not url:
        raise ValueError(f"{label} file URL is empty. Unable to download.")
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(response.content)
        text = ""
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text += page_text + "\n"
        if not text.strip():
            raise ValueError(f"No text extracted from {label}. The PDF may be scanned or encrypted.")
        return text
    finally:
        os.remove(tmp_path)


# ---------------------------------------------------------------
# CHANGED: amount pattern no longer requires a currency symbol to sit
# directly in front of the number. Real invoices put "MYR" in a column
# header, far from the number in the body row - the old pattern missed
# that entirely. Decimal length is now variable (".00" as well as
# ".00000") since ERP-generated POs often print 5 decimal places.
# ---------------------------------------------------------------
AMOUNT_PATTERN = r'([\d,]+\.\d{2,5})'


def extract_amount(text, label):
    """CHANGED: a document with a discount can have its subtotal be LARGER
    than its grand total (e.g. 480,000 subtotal -> 414,720 after a 20%
    discount + 8% tax). The old max()-of-all-"total"-lines heuristic
    picked the subtotal in that case. Now "grand total" / "total amount" /
    "amount due" lines are strictly preferred over a bare "Total" line,
    since the former are unambiguous about being the final figure."""
    lines = text.splitlines()
    priority_keywords = re.compile(r'(grand\s*total|total\s*amount|amount\s*due|总计|合计)', re.IGNORECASE)
    generic_keywords = re.compile(r'\btotal\b\s*:?', re.IGNORECASE)

    priority_candidates, generic_candidates = [], []
    for line in lines:
        found = re.findall(AMOUNT_PATTERN, line)
        if not found:
            continue
        if priority_keywords.search(line):
            priority_candidates.append(float(found[-1].replace(",", "")))
        elif generic_keywords.search(line):
            generic_candidates.append(float(found[-1].replace(",", "")))

    if priority_candidates:
        return priority_candidates[-1]
    if generic_candidates:
        return generic_candidates[-1]
    all_matches = re.findall(AMOUNT_PATTERN, text)
    if all_matches:
        return float(all_matches[-1].replace(",", ""))
    raise ValueError(f"No amount found in {label}. Please check PDF format or update extraction rules.")


def extract_field(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            groups = [g for g in match.groups() if g is not None]
            if groups:
                return groups[0].strip()
    return None


def normalize_text(value):
    if not value:
        return ""
    value = value.lower()
    value = re.sub(r'[.,;:()\-]', ' ', value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value


def fuzzy_similarity(a, b):
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)
    if not a_norm or not b_norm:
        return 0.0
    return SequenceMatcher(None, a_norm, b_norm).ratio()


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
# CHANGED: added "DD-Mon-YYYY" (01-Apr-2026) and stripped ordinal
# suffixes ("24th March 2026") - both appeared in the real documents
# and neither matched the old pattern set.
# ---------------------------------------------------------------
DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y",
]


def parse_date(raw_date):
    if not raw_date:
        return None
    raw_date = raw_date.strip()
    raw_date = re.sub(r'(\d{1,2})(st|nd|rd|th)\b', r'\1', raw_date, flags=re.IGNORECASE)  # "24th" -> "24"
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw_date, fmt).date()
        except ValueError:
            continue
    return None


def evaluate_field(label, po_val, q_val, is_match_fn, describe_fn):
    if po_val is None and q_val is None:
        return True, None
    if po_val is None or q_val is None:
        return False, f"{label} present on only one document (PO: {po_val}, Quotation: {q_val})"
    if is_match_fn(po_val, q_val):
        return True, None
    return False, f"{label} mismatch ({describe_fn(po_val, q_val)})"


TEXT_MATCH_THRESHOLD = 0.85
AMOUNT_TOLERANCE = 0.01
QUANTITY_TOLERANCE = 0
DISCOUNT_TOLERANCE = 0.01
TAX_TOLERANCE = 0.01

# ---------------------------------------------------------------
# CHANGED: real quotations/POs rarely say "Company:" or "Bill To:"
# literally - this one uses "To:" (quotation) and the ship-to block
# (PO). Patterns broadened to cover common variants, and a fallback
# grabs the company-suffix line ("Sdn Bhd" / "Ltd" / "Berhad" / "Inc")
# within a couple of lines of the label, since the buyer name and
# buyer company are often on separate lines.
# ---------------------------------------------------------------
COMPANY_SUFFIX = r'(?:Sdn\.?\s?Bhd\.?|Berhad|Bhd\.?|Ltd\.?|LLC|Inc\.?|Corp\.?|Pte\.?\s?Ltd\.?)'

buyer_company_patterns = [
    rf'Bill To:\s?([^\n]+{COMPANY_SUFFIX}[^\n]*)',
    rf'Customer:\s?([^\n]+{COMPANY_SUFFIX}[^\n]*)',
    rf'Company:\s?([^\n]+)',
    rf'To:\s?[^\n]*\n([^\n]*{COMPANY_SUFFIX}[^\n]*)',   # "To: <person>\n<company>"
    rf'To:\s?([^\n]*{COMPANY_SUFFIX}[^\n]*)',            # "To: <company>" on one line
    rf'Ship\s*To\b\s+([^\n]*{COMPANY_SUFFIX}[^\n]*)',    # "Ship To <company>" same line (PO)
]

address_patterns = [
    # "Address:" label specifically, so the buyer company name and any
    # "SO #:" style stray label line above it doesn't get swept in
    r'Ship\s*To\b[^\n]*\n(?:[^\n]*\n)?\s*Address:?\s?([^\n\r]+(?:[\r\n]+[^\n\r]+){0,2})',
    r'Delivery\s*Address:?\s?([^\n\r]+(?:[\r\n]+[^\n\r]+){0,2})',
    r'Ship\s*To:?\s?([^\n\r]+(?:[\r\n]+[^\n\r]+){0,2})',
]

date_patterns = [
    r'(?:Date|PO\s*Date|Quotation\s*Date)\s?:\s?([0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{2,4})',
    r'(?:Date|PO\s*Date|Quotation\s*Date)\s?:\s?([0-9]{4}-[0-9]{2}-[0-9]{2})',
    r'(?:Date|PO\s*Date|Quotation\s*Date)\s?:\s?([0-9]{1,2}(?:st|nd|rd|th)?\s?[A-Za-z]+\s?[0-9]{4})',
    r'(?:Date|PO\s*Date|Quotation\s*Date)\s?:\s?([0-9]{1,2}-[A-Za-z]{3,9}-[0-9]{4})',
]

quantity_patterns = [
    r'(?:Qty|Quantity)\s?:?\s?([\d,]+(?:\.\d+)?)',
    r'\bUNIT\s+([\d,]+(?:\.\d+)?)\s+[A-Z]{3}\b',   # table row: "UNIT 12.00 MYR"
]

description_patterns = [
    r'Description\s?:\s?([^\n]+)',
    r'Item\s?:\s?([^\n]+)',
]

discount_patterns = [
    r'Discount\s?:?\s?(?:RM|MYR|\$|USD)?\s?([\d,]+\.?\d*)\s?%?',
    r'Special\s*Discount\s*\(-?([\d.]+)%\)',
]

tax_patterns = [
    r'(?:Tax|SST|GST|VAT)\s?-?\s?(\d+(?:\.\d+)?)\s?%.{0,20}?([\d,]+\.\d{2,5})',
]


def extract_tax(text):
    for pattern in tax_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(2).replace(",", ""))
            except (IndexError, ValueError):
                continue
    return None


po_file_val = get_input_value("po_file")
quotation_file_val = get_input_value("quotation_file")
po_url, po_filename = get_file_url_and_name(po_file_val)
q_url, q_filename = get_file_url_and_name(quotation_file_val)

try:
    po_text = extract_text_from_pdf(po_url, "PO")
    q_text = extract_text_from_pdf(q_url, "Quotation")

    po_buyer_company = extract_field(po_text, buyer_company_patterns) or "Unknown"
    q_buyer_company = extract_field(q_text, buyer_company_patterns) or "Unknown"

    def _clean_address(raw):
        if not raw:
            return "Not Found"
        # CHANGED: the capture window can run into the next unrelated field
        # on the same line (e.g. "...Malaysia Payment Term: 90 Days") since
        # PDF text extraction puts side-by-side table cells on one line.
        # Trim at the first such trailing label.
        raw = re.split(r'\bPayment\s*Term\b|\bShipment\s*Method\b|\bSO\s*#', raw, flags=re.IGNORECASE)[0]
        return raw.replace("\n", ", ").strip().rstrip(",").strip()

    po_delivery_address_raw = extract_field(po_text, address_patterns)
    q_delivery_address_raw = extract_field(q_text, address_patterns)
    po_delivery_address = _clean_address(po_delivery_address_raw)
    q_delivery_address = _clean_address(q_delivery_address_raw)

    buyer_company_similarity = fuzzy_similarity(po_buyer_company, q_buyer_company)
    buyer_company_match = buyer_company_similarity >= TEXT_MATCH_THRESHOLD

    address_similarity = fuzzy_similarity(po_delivery_address, q_delivery_address)
    address_match = address_similarity >= TEXT_MATCH_THRESHOLD

    po_amount = extract_amount(po_text, "PO")
    q_amount = extract_amount(q_text, "Quotation")
    amount_match = abs(po_amount - q_amount) <= AMOUNT_TOLERANCE

    po_date_raw = extract_field(po_text, date_patterns)
    q_date_raw = extract_field(q_text, date_patterns)
    po_date = parse_date(po_date_raw)
    q_date = parse_date(q_date_raw)
    date_match, date_note = evaluate_field(
        "Validation date", po_date or po_date_raw, q_date or q_date_raw,
        lambda a, b: (po_date == q_date) if (po_date and q_date) else (po_date_raw == q_date_raw),
        lambda a, b: f"PO: {po_date_raw}, Quotation: {q_date_raw}"
    )

    po_quantity = extract_number(po_text, quantity_patterns)
    q_quantity = extract_number(q_text, quantity_patterns)
    quantity_match, quantity_note = evaluate_field(
        "Quantity", po_quantity, q_quantity,
        lambda a, b: abs(a - b) <= QUANTITY_TOLERANCE,
        lambda a, b: f"PO: {a}, Quotation: {b}"
    )

    po_description = extract_field(po_text, description_patterns)
    q_description = extract_field(q_text, description_patterns)
    description_similarity = fuzzy_similarity(po_description, q_description) if (po_description and q_description) else 0.0
    description_match, description_note = evaluate_field(
        "Description", po_description, q_description,
        lambda a, b: fuzzy_similarity(a, b) >= TEXT_MATCH_THRESHOLD,
        lambda a, b: f"PO: '{a}', Quotation: '{b}', Similarity: {description_similarity:.0%}"
    )

    po_discount = extract_number(po_text, discount_patterns)
    q_discount = extract_number(q_text, discount_patterns)
    discount_match, discount_note = evaluate_field(
        "Discount", po_discount, q_discount,
        lambda a, b: abs(a - b) <= DISCOUNT_TOLERANCE,
        lambda a, b: f"PO: {a}, Quotation: {b}"
    )

    po_tax = extract_tax(po_text)
    q_tax = extract_tax(q_text)
    tax_match, tax_note = evaluate_field(
        "Tax", po_tax, q_tax,
        lambda a, b: abs(a - b) <= TAX_TOLERANCE,
        lambda a, b: f"PO: {a}, Quotation: {b}"
    )

    is_match = 1 if (amount_match and buyer_company_match and address_match
                      and date_match and quantity_match and description_match
                      and discount_match and tax_match) else 0
    status_text = "Success" if is_match == 1 else "Failed"

    mismatch_notes = []
    if not amount_match:
        difference = po_amount - q_amount
        mismatch_notes.append(f"Amount mismatch (PO: {po_amount:.2f}, Quotation: {q_amount:.2f}, Diff: {difference:+.2f})")
    if not buyer_company_match:
        mismatch_notes.append(f"Buyer company mismatch (PO: '{po_buyer_company}', Quotation: '{q_buyer_company}', Similarity: {buyer_company_similarity:.0%})")
    if not address_match:
        mismatch_notes.append(f"Delivery address mismatch (PO: '{po_delivery_address}', Quotation: '{q_delivery_address}', Similarity: {address_similarity:.0%})")
    for matched, note in [(date_match, date_note), (quantity_match, quantity_note),
                          (description_match, description_note), (discount_match, discount_note),
                          (tax_match, tax_note)]:
        if not matched and note:
            mismatch_notes.append(note)

    ai_recommendation = (f"PO and Quotation matched successfully. Approved Total: USD {po_amount:.2f}"
                         if is_match == 1 else "Mismatch detected! " + "; ".join(mismatch_notes))

    output = {
        "po_buyer_company": po_buyer_company, "q_buyer_company": q_buyer_company,
        "buyer_company_match": buyer_company_match, "buyer_company_similarity": round(buyer_company_similarity, 2),
        "po_delivery_address": po_delivery_address, "q_delivery_address": q_delivery_address,
        "address_match": address_match, "address_similarity": round(address_similarity, 2),
        "po_amount": po_amount, "q_amount": q_amount, "amount_match": amount_match,
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
        "po_delivery_address": "Not Found", "q_delivery_address": "Not Found", "address_match": False, "address_similarity": 0.0,
        "po_amount": 0.0, "q_amount": 0.0, "amount_match": False,
        "po_date": None, "q_date": None, "date_match": False,
        "po_quantity": None, "q_quantity": None, "quantity_match": False,
        "po_description": None, "q_description": None, "description_match": False, "description_similarity": 0.0,
        "po_discount": None, "q_discount": None, "discount_match": False,
        "po_tax": None, "q_tax": None, "tax_match": False,
        "is_match": 0, "status_text": "Error",
        "ai_recommendation": f"Processing failed: {str(e)}",
    }
