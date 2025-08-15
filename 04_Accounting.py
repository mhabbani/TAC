import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
import io

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Optional (for PDF receipts)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

# ---------------------------
# CONFIG
# ---------------------------
st.set_page_config(page_title="TAC – Accounting & Receipts", layout="wide")

REG_SHEET_NAME = st.secrets["tac"]["TAC-Registeration"]          # e.g., "TAC-Registeration"
REG_WORKSHEET  = st.secrets["tac"]["registration_worksheet_name"]           # e.g., "Form Responses 1" or "Sheet1"
ACC_SPREADSHEET = st.secrets["tac"]["accounting_spreadsheet_name"]          # e.g., "TAC-Accounting"

# Master & Ledger worksheet names (created if missing)
ACC_MASTER_WS = "Accounting_Master"
ACC_LEDGER_WS = "Payments_Ledger"

# Columns for accounting master (1 row per Registration_ID)
ACC_MASTER_COLS = [
    "Registration_ID", "Student_Name", "Course", "Phone",
    "PaymentPlan", "InstallmentCount", "Total_Fee",
    "Paid_To_Date", "Remaining", "Status",
    "LastPaymentDate", "LastReceiptID"
]

# Columns for payments ledger (1 row per payment/receipt)
ACC_LEDGER_COLS = [
    "Receipt_ID", "Registration_ID", "Payment_Date",
    "Amount", "Method", "Note", "Entered_By",
    "Installment_Number"  # 1..6 or blank for full payment
]

# ---------------------------
# GSHEETS AUTH
# ---------------------------
@st.cache_resource(show_spinner=False)
def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = st.secrets["gcp_service_account"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

gc = get_gspread_client()

def open_sheet(spreadsheet_name):
    try:
        return gc.open(spreadsheet_name)
    except gspread.SpreadsheetNotFound:
        # create if not exists (service account must have Drive access)
        return gc.create(spreadsheet_name)

def ensure_worksheet(sh, title, cols):
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=max(len(cols), 10))
        ws.update([cols])
    # Ensure header row is correct/orderly
    existing = ws.row_values(1)
    if existing != cols:
        # rewrite header preserving data: easiest approach is to enforce our header
        ws.delete_rows(1)
        ws.insert_row(cols, 1)
    return ws

@st.cache_data(show_spinner=False, ttl=60)
def load_registration_df():
    reg_sh = gc.open(REG_SHEET_NAME)
    reg_ws = reg_sh.worksheet(REG_WORKSHEET)
    df = pd.DataFrame(reg_ws.get_all_records())
    return df

def to_df(ws):
    rows = ws.get_all_values()
    if not rows:
        return pd.DataFrame()
    header, data = rows[0], rows[1:]
    return pd.DataFrame(data, columns=header)

def upsert_master_row(ws_master, row_dict):
    """Upsert row in Accounting_Master by Registration_ID"""
    df = to_df(ws_master)
    if df.empty:
        # write header then append
        ws_master.update([ACC_MASTER_COLS, [row_dict.get(c, "") for c in ACC_MASTER_COLS]])
        return

    if "Registration_ID" not in df.columns:
        ws_master.clear()
        ws_master.update([ACC_MASTER_COLS])
        df = pd.DataFrame(columns=ACC_MASTER_COLS)

    reg_id = row_dict["Registration_ID"]
    match_idx = df.index[df["Registration_ID"] == reg_id].tolist()
    values = [row_dict.get(c, "") for c in ACC_MASTER_COLS]

    if match_idx:
        # Update the existing row (1-based rows, +1 header)
        r = match_idx[0] + 2
        ws_master.update(f"A{r}:{chr(64+len(ACC_MASTER_COLS))}{r}", [values])
    else:
        ws_master.append_row(values)

def append_ledger_row(ws_ledger, row_dict):
    values = [row_dict.get(c, "") for c in ACC_LEDGER_COLS]
    # Ensure header exists
    if ws_ledger.row_count == 0:
        ws_ledger.update([ACC_LEDGER_COLS])
    else:
        first_row = ws_ledger.row_values(1)
        if first_row != ACC_LEDGER_COLS:
            ws_ledger.clear()
            ws_ledger.update([ACC_LEDGER_COLS])
    ws_ledger.append_row(values)

def generate_receipt_pdf(receipt_id, reg_row, pay_amount, pay_method, remaining, installment_no, entered_by):
    # Simple PDF builder with ReportLab
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    y = h - 20*mm
    def line(txt, dy=8*mm):
        nonlocal y
        c.drawString(20*mm, y, txt)
        y -= dy

    c.setFont("Helvetica-Bold", 16)
    line("Together Academic Center (TAC) – Payment Receipt", 12*mm)
    c.setFont("Helvetica", 11)
    line(f"Receipt ID: {receipt_id}")
    line(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    line(f"Student: {reg_row.get('Student_Name','')}")
    line(f"Course: {reg_row.get('Course','')}")
    line(f"Phone: {reg_row.get('Phone','')}")
    line(f"Registration ID: {reg_row.get('Registration_ID','')}")
    line("")

    c.setFont("Helvetica-Bold", 12)
    line("Payment Details", 10*mm)
    c.setFont("Helvetica", 11)
    line(f"Amount Paid: {pay_amount}")
    line(f"Method: {pay_method}")
    if installment_no:
        line(f"Installment Number: {installment_no}")
    line(f"Remaining Balance: {remaining}")
    line(f"Entered By: {entered_by}")
    line("")

    c.setFont("Helvetica-Oblique", 10)
    line("Thank you for your payment.", 12*mm)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf

# ---------------------------
# MAIN UI
# ---------------------------
st.title("💳 TAC – Accounting & Receipts (Admin)")

# Open / ensure Accounting spreadsheet
acc_sh = open_sheet(ACC_SPREADSHEET)
ws_master = ensure_worksheet(acc_sh, ACC_MASTER_WS, ACC_MASTER_COLS)
ws_ledger = ensure_worksheet(acc_sh, ACC_LEDGER_WS, ACC_LEDGER_COLS)

# Load registrations (read-only)
with st.spinner("Loading registrations..."):
    reg_df = load_registration_df()

if reg_df.empty:
    st.warning("No registrations found. Please ensure the registration sheet has data.")
    st.stop()

# Assume your registration sheet has a unique Registration_ID (recommended).
# If not, create one by concatenating timestamp + phone for selection display.
# Map/rename as needed below to match your actual form columns.
# ---- Edit these mappings to fit your registration columns ----
COLUMN_MAP = {
    "Registration_ID": "Registration_ID",  # must exist or be created offline
    "Student_Name": "Student Name",
    "Course": "Course",
    "Phone": "Phone",
    "PaymentPlan": "Payment Plan",         # "Full" or "Installments"
    "InstallmentCount": "Installments Count",  # 1..6 (string/int)
    "Total_Fee": "Total Fee"
}
# If Registration_ID doesn't exist, create an in-memory temp ID:
if "Registration_ID" not in reg_df.columns:
    reg_df["Registration_ID"] = (
        reg_df.get("Timestamp", pd.Series(range(len(reg_df)))).astype(str).str.replace(r"\D","",regex=True)
        + "-" + reg_df.get("Phone", pd.Series(range(len(reg_df)))).astype(str).str[-4:]
    )

# Build a compact selector label
reg_df["_label"] = (
    reg_df["Registration_ID"].astype(str)
    + " — "
    + reg_df.get(COLUMN_MAP["Student_Name"], "").astype(str)
    + " | "
    + reg_df.get(COLUMN_MAP["Course"], "").astype(str)
)

selected_label = st.selectbox("Select a registration", sorted(reg_df["_label"].tolist()))
sel_row = reg_df[reg_df["_label"] == selected_label].iloc[0].to_dict()

# Pull mapped fields safely
def getv(src, key):
    return str(src.get(COLUMN_MAP.get(key, key), "")).strip()

reg_row = {
    "Registration_ID": sel_row["Registration_ID"],
    "Student_Name": getv(sel_row, "Student_Name"),
    "Course": getv(sel_row, "Course"),
    "Phone": getv(sel_row, "Phone"),
    "PaymentPlan": getv(sel_row, "PaymentPlan") or "Installments",
    "InstallmentCount": int(str(getv(sel_row, "InstallmentCount") or "6").split()[0]) if str(getv(sel_row, "InstallmentCount") or "").strip() else 6,
    "Total_Fee": float(str(getv(sel_row, "Total_Fee") or "0").replace(",","") or 0),
}

# Fetch current master state (if any)
master_df = to_df(ws_master)
existing = None
if not master_df.empty and "Registration_ID" in master_df.columns:
    m = master_df[master_df["Registration_ID"] == reg_row["Registration_ID"]]
    if not m.empty:
        existing = m.iloc[0].to_dict()

# Compute derived fields
paid_to_date = float(existing["Paid_To_Date"]) if existing and existing.get("Paid_To_Date","") else 0.0
remaining = max(reg_row["Total_Fee"] - paid_to_date, 0.0)
status = existing["Status"] if existing and existing.get("Status") else ("Unpaid" if paid_to_date == 0 else "Installments")

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Total Fee", f"{reg_row['Total_Fee']:,.2f}")
with col2:
    st.metric("Paid to Date", f"{paid_to_date:,.2f}")
with col3:
    st.metric("Remaining", f"{remaining:,.2f}")

st.write("---")

# Payment controls
st.subheader("Record a Payment")
admin_name = st.text_input("Entered By (Admin)", value="")
payment_type = st.radio("Payment Type", ["Completed", "Installments"], index=(0 if remaining == 0 else 1))

installment_no = None
if payment_type == "Installments":
    st.caption("Select the installment being paid now (1–6).")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    checks = []
    with c1: checks.append(st.checkbox("1"))
    with c2: checks.append(st.checkbox("2"))
    with c3: checks.append(st.checkbox("3"))
    with c4: checks.append(st.checkbox("4"))
    with c5: checks.append(st.checkbox("5"))
    with c6: checks.append(st.checkbox("6"))

    # Ensure exactly one installment is selected at a time
    if sum(checks) > 1:
        st.error("Please select only ONE installment at a time.")
        st.stop()
    if sum(checks) == 1:
        installment_no = checks.index(True) + 1

pay_amount = st.number_input("Payment Amount", min_value=0.0, step=10.0, format="%.2f")
pay_method = st.selectbox("Payment Method", ["Cash", "Transfer", "POS", "Other"])
pay_note = st.text_input("Note (optional)")

if st.button("Save & Generate Receipt", type="primary", disabled=(pay_amount <= 0 or (payment_type=="Installments" and not installment_no))):
    # Recompute remaining after this payment
    new_paid_to_date = paid_to_date + pay_amount
    new_remaining = max(reg_row["Total_Fee"] - new_paid_to_date, 0.0)
    new_status = "Completed" if new_remaining == 0 else ("Installments" if payment_type=="Installments" else "Completed")

    # Upsert master row
    master_row = {
        "Registration_ID": reg_row["Registration_ID"],
        "Student_Name": reg_row["Student_Name"],
        "Course": reg_row["Course"],
        "Phone": reg_row["Phone"],
        "PaymentPlan": reg_row["PaymentPlan"],
        "InstallmentCount": reg_row["InstallmentCount"],
        "Total_Fee": f"{reg_row['Total_Fee']:.2f}",
        "Paid_To_Date": f"{new_paid_to_date:.2f}",
        "Remaining": f"{new_remaining:.2f}",
        "Status": new_status,
        "LastPaymentDate": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "LastReceiptID": ""  # filled below after we have it
    }

    # Append to ledger
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

    # Update master (with receipt id)
    master_row["LastReceiptID"] = receipt_id
    upsert_master_row(ws_master, master_row)

    # Build receipt PDF
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

    # Refresh metrics
    st.rerun()

st.write("---")
st.caption("Note: The registration sheet remains read‑only. All financial records are in the separate TAC‑Accounting spreadsheet.")
