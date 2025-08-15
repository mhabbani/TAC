# pages/04_Accounting.py
# TAC – Accounting & Receipts (Admin)
# ------------------------------------------------------------
# - Read-only link to the existing Registration sheet
# - Separate Accounting spreadsheet:
#     * Accounting_Master  : one row per Registration_ID
#     * Payments_Ledger    : one row per payment/receipt
# - Supports Completed or Installments (1–6)
# - Prevents overpayment
# - Generates PDF receipts for download
# - Preflight check for secrets & permissions
# ------------------------------------------------------------

import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
import io
import math

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# (Optional) PDF generation
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

# ---------------------------
# STREAMLIT CONFIG
# ---------------------------
st.set_page_config(page_title="TAC – Accounting & Receipts", layout="wide")
st.title("💳 TAC – Accounting & Receipts (Admin)")

# ---------------------------
# CONSTANTS / SCHEMA
# ---------------------------
ACC_MASTER_WS = "Accounting_Master"
ACC_LEDGER_WS = "Payments_Ledger"

ACC_MASTER_COLS = [
    "Registration_ID", "Student_Name", "Course", "Phone",
    "PaymentPlan", "InstallmentCount", "Total_Fee",
    "Paid_To_Date", "Remaining", "Status",
    "LastPaymentDate", "LastReceiptID"
]

ACC_LEDGER_COLS = [
    "Receipt_ID", "Registration_ID", "Payment_Date",
    "Amount", "Method", "Note", "Entered_By",
    "Installment_Number"  # 1..6 or blank for full payment
]

# Map your registration sheet columns here (right side are the exact headers in the registration sheet)
COLUMN_MAP = {
    "Registration_ID": "Registration_ID",       # If missing in your sheet, we’ll generate a temporary one
    "Student_Name": "Student Name",
    "Course": "Course",
    "Phone": "Phone",
    "PaymentPlan": "Payment Plan",              # e.g., "Full" or "Installments"
    "InstallmentCount": "Installments Count",   # numeric (1..6)
    "Total_Fee": "Total Fee"                    # numeric
}

# ---------------------------
# SECRETS & SAFETY
# ---------------------------
def need(section: str, key: str) -> str:
    """Fail-fast getter for st.secrets."""
    val = st.secrets.get(section, {}).get(key)
    if not val:
        st.error(
            f"Missing secret: [{section}].{key}. "
            "Add it to .streamlit/secrets.toml (local) or the Streamlit Cloud Secrets UI."
        )
        st.stop()
    return val

REG_SHEET_NAME = need("tac", "registration_spreadsheet_name")    # e.g., "TAC-Registeration"
REG_WORKSHEET  = need("tac", "registration_worksheet_name")      # e.g., "Sheet1" or "Form Responses 1"
ACC_SPREADSHEET = need("tac", "accounting_spreadsheet_name")     # e.g., "TAC-Accounting"

CREDS_DICT = st.secrets.get("gcp_service_account", None)
if not CREDS_DICT:
    st.error("Missing service account: [gcp_service_account] block in secrets.")
    st.stop()

# ---------------------------
# GOOGLE AUTH
# ---------------------------
@st.cache_resource(show_spinner=False)
def get_gspread_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_DICT, scope)
    return gspread.authorize(creds)

gc = get_gspread_client()

# ---------------------------
# SHEET HELPERS
# ---------------------------
def open_sheet(spreadsheet_name: str):
    try:
        return gc.open(spreadsheet_name)
    except gspread.SpreadsheetNotFound:
        # Create the spreadsheet if it doesn't exist (service account must have Drive permission)
        sh = gc.create(spreadsheet_name)
        return sh

def ensure_worksheet(sh, title: str, cols: list):
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=max(len(cols), 10))
        ws.update([cols])
        return ws

    # Ensure header matches (if not, rewrite header but keep data positions intact)
    existing_header = ws.row_values(1)
    if existing_header != cols:
        # Recreate header row cleanly
        ws.delete_rows(1)
        ws.insert_row(cols, 1)
    return ws

def ws_to_df(ws) -> pd.DataFrame:
    rows = ws.get_all_values()
    if not rows:
        return pd.DataFrame(columns=[])
    header, data = rows[0], rows[1:]
    return pd.DataFrame(data, columns=header)

def upsert_master_row(ws_master, row_dict: dict):
    """Upsert row in Accounting_Master by Registration_ID"""
    df = ws_to_df(ws_master)
    # Guarantee header
    if df.empty:
        ws_master.update([ACC_MASTER_COLS, [row_dict.get(c, "") for c in ACC_MASTER_COLS]])
        return

    if "Registration_ID" not in df.columns:
        ws_master.clear()
        ws_master.update([ACC_MASTER_COLS])
        df = pd.DataFrame(columns=ACC_MASTER_COLS)

    reg_id = row_dict["Registration_ID"]
    matches = df.index[df["Registration_ID"] == reg_id].tolist()
    values = [row_dict.get(c, "") for c in ACC_MASTER_COLS]

    # Compute range letters (A..)
    last_col_letter = chr(64 + len(ACC_MASTER_COLS))  # supports up to 26 columns
    if matches:
        rownum = matches[0] + 2  # +1 header, +1 to convert 0-based index
        ws_master.update(f"A{rownum}:{last_col_letter}{rownum}", [values])
    else:
        ws_master.append_row(values)

def append_ledger_row(ws_ledger, row_dict: dict):
    # Ensure header
    first_row = ws_ledger.row_values(1)
    if first_row != ACC_LEDGER_COLS:
        ws_ledger.clear()
        ws_ledger.update([ACC_LEDGER_COLS])
    values = [row_dict.get(c, "") for c in ACC_LEDGER_COLS]
    ws_ledger.append_row(values)

# ---------------------------
# PREFLIGHT CHECKS
# ---------------------------
with st.expander("Preflight (troubleshooting)"):
    st.write("• Checking access to Registration and Accounting spreadsheets...")
    ok = True
    try:
        _ = gc.open(REG_SHEET_NAME)
        st.success(f"Registration spreadsheet OK: {REG_SHEET_NAME}")
    except Exception as e:
        ok = False
        st.error(f"Cannot open Registration spreadsheet [{REG_SHEET_NAME}]. Share it with the service account. Details: {e}")

    try:
        _ = open_sheet(ACC_SPREADSHEET)
        st.success(f"Accounting spreadsheet OK / will be created if missing: {ACC_SPREADSHEET}")
    except Exception as e:
        ok = False
        st.error(f"Cannot open/create Accounting spreadsheet [{ACC_SPREADSHEET}]. Details: {e}")

    if not ok:
        st.stop()

# ---------------------------
# OPEN / ENSURE ACCOUNTING SHEETS
# ---------------------------
acc_sh = open_sheet(ACC_SPREADSHEET)
ws_master = ensure_worksheet(acc_sh, ACC_MASTER_WS, ACC_MASTER_COLS)
ws_ledger = ensure_worksheet(acc_sh, ACC_LEDGER_WS, ACC_LEDGER_COLS)

# ---------------------------
# LOAD REGISTRATIONS (READ-ONLY)
# ---------------------------
@st.cache_data(show_spinner=True, ttl=60)
def load_registration_df() -> pd.DataFrame:
    reg_sh = gc.open(REG_SHEET_NAME)
    reg_ws = reg_sh.worksheet(REG_WORKSHEET)
    df = pd.DataFrame(reg_ws.get_all_records())
    return df

with st.spinner("Loading registrations..."):
    reg_df = load_registration_df()

if reg_df.empty:
    st.warning("No registrations found. Check the Registration sheet name/worksheet and sharing with the service account.")
    st.stop()

# If Registration_ID is missing in your registrations, derive a temporary one (timestamp + last4 phone)
if "Registration_ID" not in reg_df.columns:
    ts = reg_df.get("Timestamp", pd.Series(range(len(reg_df)))).astype(str).str.replace(r"\D", "", regex=True)
    phone = reg_df.get(COLUMN_MAP["Phone"], pd.Series(range(len(reg_df)))).astype(str).str[-4:]
    reg_df["Registration_ID"] = ts + "-" + phone

# Build selector labels
label_series = (
    reg_df["Registration_ID"].astype(str)
    + " — "
    + reg_df.get(COLUMN_MAP["Student_Name"], "").astype(str)
    + " | "
    + reg_df.get(COLUMN_MAP["Course"], "").astype(str)
)
reg_df["_label"] = label_series

selected_label = st.selectbox("Select a registration", sorted(reg_df["_label"].tolist()))
sel_row = reg_df[reg_df["_label"] == selected_label].iloc[0].to_dict()

# Extract mapped fields
def get_val(src: dict, logical_key: str, default: str = "") -> str:
    actual = COLUMN_MAP.get(logical_key, logical_key)
    return str(src.get(actual, default)).strip()

reg_row = {
    "Registration_ID": sel_row["Registration_ID"],
    "Student_Name": get_val(sel_row, "Student_Name"),
    "Course": get_val(sel_row, "Course"),
    "Phone": get_val(sel_row, "Phone"),
    "PaymentPlan": get_val(sel_row, "PaymentPlan") or "Installments",
    "InstallmentCount": None,
    "Total_Fee": 0.0,
}

# Parse InstallmentCount & Total_Fee safely
try:
    inst_raw = get_val(sel_row, "InstallmentCount") or "6"
    reg_row["InstallmentCount"] = int(str(inst_raw).split()[0])
except Exception:
    reg_row["InstallmentCount"] = 6

try:
    fee_raw = get_val(sel_row, "Total_Fee") or "0"
    reg_row["Total_Fee"] = float(str(fee_raw).replace(",", ""))
except Exception:
    reg_row["Total_Fee"] = 0.0

# Load current master state for this Registration_ID
master_df = ws_to_df(ws_master)
existing = None
if not master_df.empty and "Registration_ID" in master_df.columns:
    m = master_df[master_df["Registration_ID"] == reg_row["Registration_ID"]]
    if not m.empty:
        existing = m.iloc[0].to_dict()

paid_to_date = 0.0
if existing and existing.get("Paid_To_Date"):
    try:
        paid_to_date = float(str(existing["Paid_To_Date"]).replace(",", ""))
    except Exception:
        paid_to_date = 0.0

remaining = max(reg_row["Total_Fee"] - paid_to_date, 0.0)
status = (
    existing.get("Status")
    if existing and existing.get("Status")
    else ("Unpaid" if paid_to_date == 0 else "Installments")
)

# ---------------------------
# METRICS
# ---------------------------
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Total Fee", f"{reg_row['Total_Fee']:,.2f}")
with c2:
    st.metric("Paid to Date", f"{paid_to_date:,.2f}")
with c3:
    st.metric("Remaining", f"{remaining:,.2f}")

st.divider()

# ---------------------------
# PAYMENT UI
# ---------------------------
st.subheader("Record a Payment")
admin_name = st.text_input("Entered By (Admin)", value="")
payment_type = st.radio("Payment Type", ["Completed", "Installments"], index=(0 if remaining == 0 else 1))

installment_no = None
if payment_type == "Installments":
    st.caption("Select the installment being paid now (1–6). Only one at a time.")
    i1, i2, i3, i4, i5, i6 = st.columns(6)
    checks = []
    with i1: checks.append(st.checkbox("1"))
    with i2: checks.append(st.checkbox("2"))
    with i3: checks.append(st.checkbox("3"))
    with i4: checks.append(st.checkbox("4"))
    with i5: checks.append(st.checkbox("5"))
    with i6: checks.append(st.checkbox("6"))

    if sum(checks) > 1:
        st.error("Please select only ONE installment at a time.")
        st.stop()
    if sum(checks) == 1:
        installment_no = checks.index(True) + 1

# Suggested installment amount (optional): equal split
suggested_inst_amount = 0.0
if reg_row["InstallmentCount"] and reg_row["InstallmentCount"] > 0:
    suggested_inst_amount = reg_row["Total_Fee"] / reg_row["InstallmentCount"]

pay_amount = st.number_input(
    "Payment Amount",
    min_value=0.0,
    max_value=max(remaining, 0.0),
    step=10.0,
    format="%.2f",
    value=float(min(remaining, suggested_inst_amount)) if payment_type == "Installments" and remaining > 0 else 0.0,
    help="Overpayment is blocked automatically."
)

pay_method = st.selectbox("Payment Method", ["Cash", "Transfer", "POS", "Other"])
pay_note = st.text_input("Note (optional)")

btn_disabled = (pay_amount <= 0) or (payment_type == "Installments" and not installment_no)
if st.button("Save & Generate Receipt", type="primary", disabled=btn_disabled):
    # Final guard against overpayment
    if pay_amount > remaining + 1e-6:
        st.error("Payment amount exceeds remaining balance.")
        st.stop()

    new_paid_to_date = paid_to_date + pay_amount
    new_remaining = max(reg_row["Total_Fee"] - new_paid_to_date, 0.0)
    new_status = "Completed" if math.isclose(new_remaining, 0.0, abs_tol=1e-6) else (
        "Installments" if payment_type == "Installments" else "Completed"
    )

    # Prepare master row upsert
    master_row = {
        "Registration_ID": reg_row["Registration_ID"],
        "Student_Name": reg_row["Student_Name"],
        "Course": reg_row["Course"],
        "Phone": reg_row["Phone"],
        "PaymentPlan": reg_row["PaymentPlan"],
        "InstallmentCount": str(reg_row["InstallmentCount"]),
        "Total_Fee": f"{reg_row['Total_Fee']:.2f}",
        "Paid_To_Date": f"{new_paid_to_date:.2f}",
        "Remaining": f"{new_remaining:.2f}",
        "Status": new_status,
        "LastPaymentDate": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "LastReceiptID": ""  # fill after receipt is created
    }

    # Append ledger entry
    receipt_id = f"RCPT-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    ledger_row = {
        "Receipt_ID": receipt_id,
        "Registration_ID": reg_row["Registration_ID"],
        "Payment_Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "Amount": f"{pay_amount:.2f}",
        "Method": pay_method,
        "Note": pay_note,
        "Entered_By": admin_name,
        "Installment_Number": installment_no if installment_no else ""
    }
    append_ledger_row(ws_ledger, ledger_row)

    # Update master with receipt id
    master_row["LastReceiptID"] = receipt_id
    upsert_master_row(ws_master, master_row)

    # Generate receipt PDF
    pdf_buf = generate_receipt_pdf(
        receipt_id=receipt_id,
        reg_row=reg_row,
        pay_amount=f"{pay_amount:.2f}",
        pay_method=pay_method,
        remaining=f"{new_remaining:.2f}",
        installment_no=installment_no,
        entered_by=admin_name or "Admin"
    )

    st.success(f"Payment recorded. Receipt {receipt_id} generated.")
    st.download_button(
        label="Download Receipt (PDF)",
        data=pdf_buf,
        file_name=f"{receipt_id}.pdf",
        mime="application/pdf"
    )

    # Rerun to refresh metrics
    st.rerun()

st.caption("Notes: Registration sheet remains read-only. All financial records are stored in the separate TAC-Accounting spreadsheet.")

# ---------------------------
# PDF RECEIPT FUNC
# ---------------------------
def generate_receipt_pdf(receipt_id: str, reg_row: dict, pay_amount: str,
                         pay_method: str, remaining: str, installment_no, entered_by: str) -> io.BytesIO:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    y = h - 20*mm

    def line(txt, dy=8*mm, font="Helvetica", size=11, bold=False, italic=False):
        nonlocal y
        if bold and italic:
            c.setFont("Helvetica-BoldOblique", size)
        elif bold:
            c.setFont("Helvetica-Bold", size)
        elif italic:
            c.setFont("Helvetica-Oblique", size)
        else:
            c.setFont(font, size)
        c.drawString(20*mm, y, txt)
        y -= dy

    line("Together Academic Center (TAC) – Payment Receipt", dy=12*mm, bold=True, size=16)
    line(f"Receipt ID: {receipt_id}")
    line(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    line(f"Student: {reg_row.get('Student_Name','')}")
    line(f"Course: {reg_row.get('Course','')}")
    line(f"Phone: {reg_row.get('Phone','')}")
    line(f"Registration ID: {reg_row.get('Registration_ID','')}")
    line("")

    line("Payment Details", dy=10*mm, bold=True, size=12)
    line(f"Amount Paid: {pay_amount}")
    line(f"Method: {pay_method}")
    if installment_no:
        line(f"Installment Number: {installment_no}")
    line(f"Remaining Balance: {remaining}")
    line(f"Entered By: {entered_by or 'Admin'}")
    line("")
    line("Thank you for your payment.", italic=True, size=10, dy=12*mm)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf
