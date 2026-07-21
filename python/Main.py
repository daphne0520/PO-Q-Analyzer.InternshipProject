## PDF Comparison Engine

import sys
import subprocess
import json
import re
import os
import tempfile
import requests
from difflib import SequenceMatcher
from datetime import datetime

# 1. Automatically install PyPDF2 library if it is not available
try:
    import PyPDF2
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "PyPDF2"])
    import PyPDF2


# 2. Flexible input parameter reader
# Supports global variables, "input", and "inputs" formats
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


# 3. Parse uploaded file information and extract URL + filename
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


# 4. Download PDF files and extract text content
# Uses unique temporary files to prevent conflicts during concurrent execution
def extract_text_from_pdf(url, label):
    if not url:
        raise ValueError(f"{label} file URL is empty. Unable to download.")

    response = requests.get(url, timeout=15)
    response.raise_for_status()

    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")

    try:
        with os.fdopen(fd, "wb") as f:
            f.write(response.content)

        text = ""

        with open(tmp_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)

            for page in reader.pages:
                text += page.extract_text() or ""

        if not text.strip():
            raise ValueError(
                f"No text extracted from {label}. "
                "The PDF may be scanned or encrypted."
            )

        return text

    finally:
        # Always remove temporary files after processing
        os.remove(tmp_path)


# 5. Robust amount extraction
# Priority:
# 1. Extract amounts from lines containing Total-related keywords
# 2. Fallback to the last detected amount in the document

AMOUNT_PATTERN = r'(?:\$|USD|RM|MYR)\s?([\d,]+\.\d{2})'


def extract_amount(text, label):
    lines = text.splitlines()

    total_keywords = re.compile(
        r'(grand\s*total|total\s*amount|amount\s*due|总计|合计|total)',
        re.IGNORECASE
    )

    candidates = []

    for line in lines:
        if total_keywords.search(line):

            found = re.findall(
                AMOUNT_PATTERN,
                line,
                re.IGNORECASE
            )

            if found:
                candidates.append(
                    float(found[-1].replace(",", ""))
                )

    if candidates:
        # If multiple total values exist, select the highest value
        # (usually Grand Total >= Subtotal)
        return max(candidates)

    # Fallback: use the last amount found in the document
    all_matches = re.findall(
        AMOUNT_PATTERN,
        text,
        re.IGNORECASE
    )

    if all_matches:
        return float(all_matches[-1].replace(",", ""))

    raise ValueError(
        f"No amount found in {label}. "
        "Please check PDF format or update extraction rules."
    )


# 6. Extract buyer company name and delivery address
def extract_field(text, patterns):

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            groups = [
                g for g in match.groups()
                if g is not None
            ]

            if groups:
                return groups[0].strip()

    return None


# ---------------------------------------------------------------
# NEW: fuzzy text matching helpers
# ---------------------------------------------------------------
# Company names / addresses / descriptions rarely match
# character-for-character between a PO and a Quotation
# ("ABC Sdn Bhd" vs "ABC Sdn. Bhd."), so exact string equality
# gives false "mismatch" results.
# We normalize the text first, then score similarity with
# difflib.SequenceMatcher (stdlib, no extra dependency).

def normalize_text(value):
    if not value:
        return ""
    value = value.lower()
    value = re.sub(r'[.,;:()\-]', ' ', value)   # drop common punctuation
    value = re.sub(r'\s+', ' ', value).strip()  # collapse whitespace
    return value


def fuzzy_similarity(a, b):
    """Returns a 0.0-1.0 similarity score between two strings."""
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)
    if not a_norm or not b_norm:
        return 0.0
    return SequenceMatcher(None, a_norm, b_norm).ratio()


# ---------------------------------------------------------------
# NEW: generic numeric field extraction (quantity / discount / tax)
# ---------------------------------------------------------------
def extract_number(text, patterns):
    """Tries each regex pattern in order, returns the first number found
    (as float) or None if the field simply isn't present in the document
    (used for optional fields like discount)."""
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
# NEW: date extraction + parsing
# ---------------------------------------------------------------
DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
]


def parse_date(raw_date):
    if not raw_date:
        return None
    raw_date = raw_date.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw_date, fmt).date()
        except ValueError:
            continue
    return None  # unrecognized format - caller falls back to raw string compare


# ---------------------------------------------------------------
# NEW: generic "compare if present" helper
# ---------------------------------------------------------------
# Used for fields that may legitimately be absent (discount) or that we
# want to skip validating rather than hard-fail on if extraction misses
# (quantity / tax / date) - if BOTH sides are missing, that's not a
# mismatch (field simply doesn't apply to this document pair).

def evaluate_field(label, po_val, q_val, is_match_fn, describe_fn):
    if po_val is None and q_val is None:
        return True, None  # not present on either side - nothing to compare
    if po_val is None or q_val is None:
        return False, f"{label} present on only one document (PO: {po_val}, Quotation: {q_val})"
    if is_match_fn(po_val, q_val):
        return True, None
    return False, f"{label} mismatch ({describe_fn(po_val, q_val)})"


# Similarity threshold: how close two strings need to be to count
# as a "match". 0.85 tolerates minor punctuation/spacing/abbreviation
# differences while still catching genuinely different companies.
TEXT_MATCH_THRESHOLD = 0.85

# Allowed amount / quantity / tax difference tolerance
AMOUNT_TOLERANCE = 0.01
QUANTITY_TOLERANCE = 0
DISCOUNT_TOLERANCE = 0.01
TAX_TOLERANCE = 0.01


try:

    # Extract text from PO and Quotation PDFs
    po_text = extract_text_from_pdf(
        po_url,
        "PO"
    )

    q_text = extract_text_from_pdf(
        q_url,
        "Quotation"
    )

    # ---- Buyer company: extract from BOTH documents ----
    buyer_company_patterns = [
        r'Company:\s?([^\n]+)',
        r'Bill To:\s?([^\n]+)'
    ]

    po_buyer_company = extract_field(po_text, buyer_company_patterns) or "Unknown"
    q_buyer_company = extract_field(q_text, buyer_company_patterns) or "Unknown"

    # ---- Delivery address: extract from BOTH documents ----
    address_patterns = [
        r'Ship To:\s?([^\n\r]+(?:[\r\n]+[^\n\r]+){0,2})'
    ]

    po_delivery_address_raw = extract_field(po_text, address_patterns)
    q_delivery_address_raw = extract_field(q_text, address_patterns)

    po_delivery_address = (
        po_delivery_address_raw.replace("\n", ", ").strip()
        if po_delivery_address_raw
        else "Not Found"
    )
    q_delivery_address = (
        q_delivery_address_raw.replace("\n", ", ").strip()
        if q_delivery_address_raw
        else "Not Found"
    )

    # ---- Fuzzy comparison for text fields ----
    buyer_company_similarity = fuzzy_similarity(po_buyer_company, q_buyer_company)
    buyer_company_match = buyer_company_similarity >= TEXT_MATCH_THRESHOLD

    address_similarity = fuzzy_similarity(po_delivery_address, q_delivery_address)
    address_match = address_similarity >= TEXT_MATCH_THRESHOLD

    # Extract total amounts
    po_amount = extract_amount(
        po_text,
        "PO"
    )

    q_amount = extract_amount(
        q_text,
        "Quotation"
    )

    # 7. Compare PO and Quotation amounts
    # Uses tolerance-based comparison instead of exact equality
    # to avoid false mismatches caused by rounding
    amount_match = abs(po_amount - q_amount) <= AMOUNT_TOLERANCE

    # ---------------------------------------------------------------
    # NEW: additional fields - date, quantity, description, discount, tax
    # ---------------------------------------------------------------
    # NOTE: like the rest of this engine, these extract ONE document-level
    # value per field (not per line-item). If your PO/Quotation PDFs list
    # multiple line items each with their own qty/description/price, this
    # will only catch the first match per pattern - let me know if you need
    # it to loop over line items instead, that's a bigger restructure.

    # -- Validation date (document date on each PDF) --
    date_patterns = [
        r'(?:Date|PO\s*Date|Quotation\s*Date)\s?:\s?([0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{2,4})',
        r'(?:Date|PO\s*Date|Quotation\s*Date)\s?:\s?([0-9]{4}-[0-9]{2}-[0-9]{2})',
        r'(?:Date|PO\s*Date|Quotation\s*Date)\s?:\s?([0-9]{1,2}\s?[A-Za-z]+\s?[0-9]{4})',
    ]
    po_date_raw = extract_field(po_text, date_patterns)
    q_date_raw = extract_field(q_text, date_patterns)
    po_date = parse_date(po_date_raw)
    q_date = parse_date(q_date_raw)

    def dates_equal(a, b):
        return a == b

    date_match, date_note = evaluate_field(
        "Validation date", po_date or po_date_raw, q_date or q_date_raw,
        lambda a, b: (po_date == q_date) if (po_date and q_date) else (po_date_raw == q_date_raw),
        lambda a, b: f"PO: {po_date_raw}, Quotation: {q_date_raw}"
    )

    # -- Quantity --
    quantity_patterns = [
        r'(?:Qty|Quantity)\s?:?\s?([\d,]+(?:\.\d+)?)',
    ]
    po_quantity = extract_number(po_text, quantity_patterns)
    q_quantity = extract_number(q_text, quantity_patterns)

    quantity_match, quantity_note = evaluate_field(
        "Quantity", po_quantity, q_quantity,
        lambda a, b: abs(a - b) <= QUANTITY_TOLERANCE,
        lambda a, b: f"PO: {a}, Quotation: {b}"
    )

    # -- Description (item / product description) --
    description_patterns = [
        r'Description\s?:\s?([^\n]+)',
        r'Item\s?:\s?([^\n]+)',
    ]
    po_description = extract_field(po_text, description_patterns)
    q_description = extract_field(q_text, description_patterns)
    description_similarity = fuzzy_similarity(po_description, q_description) if (po_description and q_description) else 0.0

    description_match, description_note = evaluate_field(
        "Description", po_description, q_description,
        lambda a, b: fuzzy_similarity(a, b) >= TEXT_MATCH_THRESHOLD,
        lambda a, b: f"PO: '{a}', Quotation: '{b}', Similarity: {description_similarity:.0%}"
    )

    # -- Discount (optional - "if any") --
    discount_patterns = [
        r'Discount\s?:?\s?(?:RM|MYR|\$|USD)?\s?([\d,]+\.?\d*)\s?%?',
    ]
    po_discount = extract_number(po_text, discount_patterns)
    q_discount = extract_number(q_text, discount_patterns)

    discount_match, discount_note = evaluate_field(
        "Discount", po_discount, q_discount,
        lambda a, b: abs(a - b) <= DISCOUNT_TOLERANCE,
        lambda a, b: f"PO: {a}, Quotation: {b}"
    )

    # -- Tax (SST / GST / VAT) --
    tax_patterns = [
        r'(?:Tax|SST|GST|VAT)\s?:?\s?(?:RM|MYR|\$|USD)?\s?([\d,]+\.\d{2})',
    ]
    po_tax = extract_number(po_text, tax_patterns)
    q_tax = extract_number(q_text, tax_patterns)

    tax_match, tax_note = evaluate_field(
        "Tax", po_tax, q_tax,
        lambda a, b: abs(a - b) <= TAX_TOLERANCE,
        lambda a, b: f"PO: {a}, Quotation: {b}"
    )

    # ---- Overall match: ALL fields must match ----
    is_match = 1 if (
        amount_match and buyer_company_match and address_match
        and date_match and quantity_match and description_match
        and discount_match and tax_match
    ) else 0

    status_text = (
        "Success"
        if is_match == 1
        else "Failed"
    )

    # ---- Build a recommendation that explains WHICH field(s) failed ----
    mismatch_notes = []

    if not amount_match:
        difference = po_amount - q_amount
        mismatch_notes.append(
            f"Amount mismatch (PO: {po_amount:.2f}, Quotation: {q_amount:.2f}, "
            f"Diff: {difference:+.2f})"
        )

    if not buyer_company_match:
        mismatch_notes.append(
            f"Buyer company mismatch (PO: '{po_buyer_company}', "
            f"Quotation: '{q_buyer_company}', "
            f"Similarity: {buyer_company_similarity:.0%})"
        )

    if not address_match:
        mismatch_notes.append(
            f"Delivery address mismatch (PO: '{po_delivery_address}', "
            f"Quotation: '{q_delivery_address}', "
            f"Similarity: {address_similarity:.0%})"
        )

    for matched, note in [
        (date_match, date_note),
        (quantity_match, quantity_note),
        (description_match, description_note),
        (discount_match, discount_note),
        (tax_match, tax_note),
    ]:
        if not matched and note:
            mismatch_notes.append(note)

    if is_match == 1:
        ai_recommendation = (
            f"PO and Quotation matched successfully. "
            f"Approved Total: USD {po_amount:.2f}"
        )
    else:
        ai_recommendation = "Mismatch detected! " + "; ".join(mismatch_notes)

    output = {

        "po_buyer_company": po_buyer_company,
        "q_buyer_company": q_buyer_company,
        "buyer_company_match": buyer_company_match,
        "buyer_company_similarity": round(buyer_company_similarity, 2),

        "po_delivery_address": po_delivery_address,
        "q_delivery_address": q_delivery_address,
        "address_match": address_match,
        "address_similarity": round(address_similarity, 2),

        "po_amount": po_amount,
        "q_amount": q_amount,
        "amount_match": amount_match,

        "po_date": po_date_raw,
        "q_date": q_date_raw,
        "date_match": date_match,

        "po_quantity": po_quantity,
        "q_quantity": q_quantity,
        "quantity_match": quantity_match,

        "po_description": po_description,
        "q_description": q_description,
        "description_match": description_match,
        "description_similarity": round(description_similarity, 2),

        "po_discount": po_discount,
        "q_discount": q_discount,
        "discount_match": discount_match,

        "po_tax": po_tax,
        "q_tax": q_tax,
        "tax_match": tax_match,

        "is_match": is_match,

        "status_text": status_text,

        "ai_recommendation": ai_recommendation,

    }


except Exception as e:

    # 8. Error handling
    # Returns explicit error status instead of treating failures
    # as simple amount mismatches

    output = {

        "po_buyer_company": "Unknown",
        "q_buyer_company": "Unknown",
        "buyer_company_match": False,
        "buyer_company_similarity": 0.0,

        "po_delivery_address": "Not Found",
        "q_delivery_address": "Not Found",
        "address_match": False,
        "address_similarity": 0.0,

        "po_amount": 0.0,
        "q_amount": 0.0,
        "amount_match": False,

        "po_date": None,
        "q_date": None,
        "date_match": False,

        "po_quantity": None,
        "q_quantity": None,
        "quantity_match": False,

        "po_description": None,
        "q_description": None,
        "description_match": False,
        "description_similarity": 0.0,

        "po_discount": None,
        "q_discount": None,
        "discount_match": False,

        "po_tax": None,
        "q_tax": None,
        "tax_match": False,

        "is_match": 0,

        "status_text": "Error",

        "ai_recommendation": (
            f"Processing failed: {str(e)}"
        ),

    }
