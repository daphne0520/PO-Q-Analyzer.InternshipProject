## PDF Comparison Engine

import sys
import subprocess
import json
import re
import os
import tempfile
import requests

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



# Allowed amount difference tolerance
# Used to handle rounding differences
AMOUNT_TOLERANCE = 0.01


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


    # Extract buyer company information
    buyer_company = extract_field(
        po_text,
        [
            r'Company:\s?([^\n]+)',
            r'Bill To:\s?([^\n]+)'
        ]
    ) or "Unknown"


    # Extract delivery address
    delivery_address = extract_field(
        po_text,
        [
            r'Ship To:\s?([^\n\r]+(?:[\r\n]+[^\n\r]+){0,2})'
        ]
    )


    delivery_address = (
        delivery_address.replace("\n", ", ").strip()
        if delivery_address
        else "Not Found"
    )


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

    is_match = (
        1
        if abs(po_amount - q_amount) <= AMOUNT_TOLERANCE
        else 0
    )


    status_text = (
        "Success"
        if is_match == 1
        else "Failed"
    )


    if is_match == 1:

        ai_recommendation = (
            f"PO and Quotation matched successfully. "
            f"Approved Total: USD {po_amount:.2f}"
        )

    else:

        difference = po_amount - q_amount

        ai_recommendation = (
            f"Mismatch detected! "
            f"PO Total: {po_amount:.2f}, "
            f"Quotation Total: {q_amount:.2f}, "
            f"Difference: {difference:+.2f}"
        )


    output = {

        "buyer_company": buyer_company,

        "delivery_address": delivery_address,

        "po_amount": po_amount,

        "q_amount": q_amount,

        "is_match": is_match,

        "status_text": status_text,

        "ai_recommendation": ai_recommendation,

    }


except Exception as e:

    # 8. Error handling
    # Returns explicit error status instead of treating failures
    # as simple amount mismatches

    output = {

        "buyer_company": "Unknown",

        "delivery_address": "Not Found",

        "po_amount": 0.0,

        "q_amount": 0.0,

        "is_match": 0,

        "status_text": "Error",

        "ai_recommendation": (
            f"Processing failed: {str(e)}"
        ),

    }
