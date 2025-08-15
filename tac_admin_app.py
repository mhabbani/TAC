# ------------------------------------------------------------------------------
# author : Mohamed Habbani
# version : v1.0.3
# date : 2025-08-15 16:20 EDT
#
# File: tac_admin_app.py
# TAC Admin Panel (Arabic RTL) + Accounting & Receipts inside the same spreadsheet
# ------------------------------------------------------------------------------
# - Preserves old admin features (login, RTL UI, filters, analytics, sharing view)
# - Uses your original Google auth pattern (secrets -> fallback to credentials.json)
# - Reads registrations from existing spreadsheet WITHOUT modifying it
# - Adds two worksheets INSIDE the same registration spreadsheet (created if missing):
#     * "Accounting"       -> one row per Registration_ID (master)
#     * "Payments_Ledger"  -> one row per payment/receipt
# - NEW RULES:
#     * Full payment = 90
#     * Installment = 15 (you can check multiple installments; amount = 15 × count)
#     * Already-paid installments are locked (checked & disabled)
# - Prevents overpayment and generates PDF receipts
# - Robust worksheet selection (secrets → common names → first sheet) via ONE sidebar selectbox
#   stored in session_state to avoid duplicate widget keys
# ------------------------------------------------------------------------------

import streamlit as st
import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import uuid
import io
import math

# (Optional) PDF
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

# =========================
# GOOGLE SHEETS AUTH (as old)
# =========================
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds_section = st.secrets.get("gcp_service_account") or st.secrets.get("google_service_account")
    if creds_section:
        creds_dict = dict(creds_section)
        if isinstance(creds_dict.get("private_key"), str):
            # Support \n in secrets
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        raise KeyError("No credentials found in secrets")
except Exception:
    # Fallback to local file if secrets are not set
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)

client = gspread.authorize(creds)

# =========================
# CONFIG / CONSTANTS
# =========================
st.set_page_config(page_title="TAC Admin Panel", layout="wide")

# Names can come from secrets if provided; else defaults to your previous values
REG_SPREADSHEET_NAME = (
    st.secrets.get("tac", {}).get("registration_spreadsheet_name")
    or "TAC-Registeration"
)
REG_WORKSHEET_NAME = st.secrets.get("tac", {}).get("registration_worksheet_name")  # optional; old code used .sheet1

ACCOUNTING_MASTER_WS = "Accounting"
PAYMENTS_LEDGER_WS   = "Payments_Ledger"

# Business rules
FULL_PRICE = 90.0
INSTALLMENT_PRICE = 15.0
MAX_INSTALLMENTS = 6

# Master & ledger schemas (English headers for clean accounting data)
ACC_MASTER_COLS = [
    "Registration_ID", "Student_Name", "Course", "Phone",
    "PaymentPlan", "InstallmentCount", "Total_Fee",
    "Paid_To_Date", "Remaining", "Status",
    "LastPaymentDate", "LastReceiptID"
]
ACC_LEDGER_COLS = [
    "Receipt_ID", "Registration_ID", "Payment_Date",
    "Amount", "Method", "Note", "Entered_By",
    "Installment_Number"  # "1" or "1,2,3" or blank for full
]

# Map registration columns (RIGHT side are headers in your registration sheet)
# NOTE: Your registration sheet is in Arabic; keep your existing columns:
REG_COLUMN_MAP = {
    "Registration_ID": "Registration_ID",          # if not present, we derive one
    "Student_Name": "الاسم",
    "Course": "الكورس",
    "Phone": "رقم اتصال ولي الأمر",
    "PaymentPlan": "خطة الدفع",                    # e.g., "كامل" / "أقساط" (optional)
    "InstallmentCount": "عدد الأقساط",             # (optional) numeric 1..6
    "Total_Fee": "الرسوم الكلية"                   # (optional) numeric
}

# =========================
# UI (RTL styling) - as old
# =========================
st.markdown("""
    <style>
    body, .css-18e3th9, .css-1d391kg {
        direction: rtl;
        text-align: right;
        font-family: 'Baloo Bhaijaan', sans-serif;
    }
    </style>
""", unsafe_allow_html=True)

st.title("🛡️ لوحة التحكم الإدارية - TAC Admin")

# =========================
# LOGIN (as old)
# =========================
USERS = {
    "admin": {"role": "admin", "password": "adminpass"},
    "salma": {"role": "power", "password": "stac@2025"},
    "sara":  {"role": "power", "password": "stac@2025"},
    "amal":  {"role": "power", "password": "amal123"}
}

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""

if not st.session_state.logged_in:
    username = st.text_input("اسم المستخدم")
    password = st.text_input("كلمة المرور", type="password")
    if st.button("تسجيل الدخول"):
        if username in USERS and USERS[username]["password"] == password:
            st.session_state.logged_in = True
            st.session_state.username = username
            st.success(f"مرحبًا {username} 👋")
        else:
            st.error("❌ اسم المستخدم أو كلمة المرور غير صحيحة")
    st.stop()

role = USERS[st.session_state.username]["role"]
st.success(f"مرحبًا {st.session_state.username} 👋 - الصلاحية: {role}")

# =========================
# HELPERS
# =========================
def open_reg_spreadsheet():
    """Open the registration spreadsheet by name."""
    return client.open(REG_SPREADSHEET_NAME)

def choose_registration_worksheet_once(sh):
    """
    Create ONE sidebar selectbox for picking the registration worksheet.
    Store the choice in session_state ('reg_ws_title') and reuse it everywhere.
    """
    try:
        titles = [ws.title for ws in sh.worksheets()]
    except Exception as e:
        st.error(f"تعذر قراءة أوراق العمل: {e}")
        st.stop()

    # Build candidate order
    desired = REG_WORKSHEET_NAME
    candidates = [desired, "Form Responses 1", "Sheet1", "الردود على النموذج 1"]
    chosen = None
    for name in candidates:
        if name and name in titles:
            chosen = name
            break
    if not chosen:
        if not titles:
            st.error("لا توجد أي أوراق عمل داخل الملف.")
            st.stop()
        chosen = titles[0]

    # If not set yet, initialize session state
    if "reg_ws_title" not in st.session_state:
        st.session_state.reg_ws_title = chosen

    # Render ONE selectbox (unique key) and update the state
    current_idx = titles.index(st.session_state.reg_ws_title) if st.session_state.reg_ws_title in titles else 0
    selected = st.sidebar.selectbox("اختر ورقة التسجيل", titles, index=current_idx, key="reg_ws_choice_unique")
    st.session_state.reg_ws_title = selected

    st.caption(f"📄 Using worksheet: **{st.session_state.reg_ws_title}**")
    return st.session_state.reg_ws_title

def get_current_registration_worksheet(sh):
    """Open the worksheet currently stored in session_state without creating widgets."""
    title = st.session_state.get("reg_ws_title")
    if not title:
        return sh.sheet1
    return sh.worksheet(title)

def ensure_worksheet(sh, title: str, cols: list):
    """Ensure worksheet exists with the expected header (creates if missing)."""
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=max(len(cols), 10))
        ws.update([cols])
        return ws
    # Ensure header matches
    existing_header = ws.row_values(1)
    if existing_header != cols:
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
    """Upsert row in Accounting master by Registration_ID."""
    df = ws_to_df(ws_master)
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

    last_col_letter = chr(64 + len(ACC_MASTER_COLS))  # up to 26 cols
    if matches:
        rownum = matches[0] + 2  # +1 header, +1 indexing
        ws_master.update(f"A{rownum}:{last_col_letter}{rownum}", [values])
    else:
        ws_master.append_row(values)

def append_ledger_row(ws_ledger, row_dict: dict):
    first_row = ws_ledger.row_values(1)
    if first_row != ACC_LEDGER_COLS:
        ws_ledger.clear()
        ws_ledger.update([ACC_LEDGER_COLS])
    ws_ledger.append_row([row_dict.get(c, "") for c in ACC_LEDGER_COLS])

def generate_receipt_pdf(receipt_id: str, reg_row: dict, pay_amount: str,
                         pay_method: str, remaining: str, installment_no, entered_by: str) -> io.BytesIO:
    """Generate a simple PDF receipt."""
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
        line(f"Installment Number(s): {installment_no}")
    line(f"Remaining Balance: {remaining}")
    line(f"Entered By: {entered_by or 'Admin'}")
    line("")
    line("Thank you for your payment.", italic=True, size=10, dy=12*mm)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf

def _to_float(x, default=0.0):
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return default

def _parse_paid_installments_from_ledger(ledger_df: pd.DataFrame, reg_id: str) -> set:
    """Return a set of integers for installments already paid for this Registration_ID."""
    if ledger_df.empty:
        return set()
    sub = ledger_df[ledger_df.get("Registration_ID", "") == reg_id]
    paid = set()
    for val in sub.get("Installment_Number", []):
        s = str(val).strip()
        if not s:
            continue
        parts = [p.strip() for p in s.split(",")]
        for p in parts:
            if p.isdigit():
                paid.add(int(p))
    return paid

# =========================
# LOAD REGISTRATIONS (robust, with ONE selector)
# =========================
try:
    reg_sh = open_reg_spreadsheet()
    # Create the SINGLE sidebar selector and store in session_state:
    choose_registration_worksheet_once(reg_sh)
    # Open the currently chosen worksheet without creating more widgets:
    reg_ws = get_current_registration_worksheet(reg_sh)
    df = pd.DataFrame(reg_ws.get_all_records())
except Exception as e:
    st.error(f"❌ فشل في تحميل البيانات: {e}")
    st.stop()

# =========================
# SIDEBAR NAV
# =========================
if role == "admin":
    page = st.sidebar.radio("القائمة", ["لوحة المشرف", "المحاسبة والمدفوعات"])
else:
    page = st.sidebar.radio("القائمة", ["بيانات التسجيل"])

# =========================
# ADMIN PAGE (as old)
# =========================
if role == "admin" and page == "لوحة المشرف":
    st.subheader("👤 لوحة المشرف")

    # Sharing info
    with st.expander("🔗 مشاركة Google Sheet"):
        try:
            perms = reg_ws.spreadsheet.list_permissions()
            for p in perms:
                email = p.get("emailAddress", "—")
                role_perm = p.get("role", "—")
                st.write(f"📧 {email} — 🛡️ {role_perm}")
        except Exception:
            st.error("لم يتم الحصول على بيانات المشاركة")

    st.subheader("📋 معاينة نموذج التسجيل")

    col1, col2, col3 = st.columns(3)
    row_limit = col1.selectbox("عدد السجلات المعروضة", ["الكل", 5, 10, 20, 50])
    search_name = col2.text_input("🔍 البحث بالاسم أو الكورس")
    # Handle potential absence of Arabic columns gracefully
    age_col = "العمر" if "العمر" in df.columns else None
    addr_col = "العنوان" if "العنوان" in df.columns else None

    if age_col:
        age_filter = col3.selectbox("📊 تصفية حسب العمر", ["الكل"] + sorted(df[age_col].dropna().astype(str).unique().tolist()))
    else:
        age_filter = "الكل"
        col3.info("لا يوجد عمود 'العمر'")

    if addr_col:
        country_filter = st.selectbox(
            "🌍 تصفية حسب الدولة",
            ["الكل"] + sorted(df[addr_col].dropna().apply(lambda x: str(x).split("-")[0].strip()).unique().tolist())
        )
    else:
        country_filter = "الكل"
        st.info("لا يوجد عمود 'العنوان'")

    filtered_df = df.copy()
    if search_name:
        name_col = "الاسم" if "الاسم" in df.columns else None
        course_col = "الكورس" if "الكورس" in df.columns else None
        if name_col:
            m1 = filtered_df[name_col].astype(str).str.contains(search_name, case=False, na=False)
        else:
            m1 = False
        if course_col:
            m2 = filtered_df[course_col].astype(str).str.contains(search_name, case=False, na=False)
        else:
            m2 = False
        filtered_df = filtered_df[m1 | m2] if (name_col or course_col) else filtered_df

    if age_filter != "الكل" and age_col:
        filtered_df = filtered_df[filtered_df[age_col].astype(str) == str(age_filter)]
    if country_filter != "الكل" and addr_col:
        filtered_df = filtered_df[filtered_df[addr_col].astype(str).str.startswith(country_filter)]

    if row_limit == "الكل":
        st.dataframe(filtered_df)
    else:
        st.dataframe(filtered_df.tail(int(row_limit)))

    # TEXTUAL ANALYTICS (unchanged)
    st.subheader("📊 تحليلات نصية للتسجيل")
    chart_type = st.selectbox("اختر نوع التحليل", [
        "عدد المسجلين لكل كورس",
        "نسبة صلة القرابة",
        "تحليل الأعمار",
        "الإخوة (نفس رقم ولي الأمر)",
        "المسجلين في أكثر من دورة"
    ])

    total = len(df) if len(df) else 1  # avoid div/0

    if chart_type == "عدد المسجلين لكل كورس":
        st.markdown("### 📘 توزيع الكورسات:")
        if "الكورس" in df.columns:
            for course, count in df["الكورس"].value_counts().items():
                percent = round((count / total) * 100, 2)
                st.markdown(f"- **{course}**: {count} طالب ({percent}%)")
        else:
            st.info("لا يوجد عمود 'الكورس'")

    elif chart_type == "نسبة صلة القرابة":
        st.markdown("### 🧑‍🤝‍🧑 صلات القرابة:")
        if "صلة القرابة" in df.columns:
            for rel, count in df["صلة القرابة"].value_counts().items():
                percent = round((count / total) * 100, 2)
                st.markdown(f"- **{rel}**: {count} ({percent}%)")
        else:
            st.info("لا يوجد عمود 'صلة القرابة'")

    elif chart_type == "تحليل الأعمار":
        st.markdown("### 🎂 توزيع الأعمار:")
        if "العمر" in df.columns:
            for age, count in df["العمر"].value_counts().sort_index().items():
                percent = round((count / total) * 100, 2)
                st.markdown(f"- **{age} سنة**: {count} ({percent}%)")
        else:
            st.info("لا يوجد عمود 'العمر'")

    elif chart_type == "الإخوة (نفس رقم ولي الأمر)":
        parent_col = "رقم اتصال ولي الأمر"
        if parent_col in df.columns:
            siblings = df.groupby(parent_col).filter(lambda x: len(x) > 1)
            st.dataframe(siblings[["الاسم", parent_col]] if "الاسم" in siblings.columns else siblings)
            st.info(f"👨‍👩‍👧‍👦 عدد الأسر التي سجلت أكثر من طفل: {siblings[parent_col].nunique()}")
        else:
            st.info("لا يوجد عمود 'رقم اتصال ولي الأمر'")

    elif chart_type == "المسجلين في أكثر من دورة":
        if "الاسم" in df.columns and "الكورس" in df.columns:
            multi = df.groupby("الاسم").filter(lambda x: len(x) > 1)
            st.dataframe(multi[["الاسم", "الكورس"]])
            st.info(f"🔁 عدد الطلاب المسجلين في أكثر من دورة: {multi['الاسم'].nunique()}")
        else:
            st.info("أعمدة مطلوبة غير موجودة: 'الاسم' و/أو 'الكورس'")

    st.markdown("### 💰 الانتقال إلى صفحة مراقبة الحسابات والمدفوعات")
    st.info("استخدم القائمة الجانبية → 'المحاسبة والمدفوعات'.")

# =========================
# ACCOUNTING PAGE (admins only)
# =========================
if role == "admin" and page == "المحاسبة والمدفوعات":
    st.subheader("💳 المحاسبة والمدفوعات")

    # Ensure accounting worksheets inside the SAME spreadsheet
    ws_master = ensure_worksheet(reg_sh, ACCOUNTING_MASTER_WS, ACC_MASTER_COLS)
    ws_ledger = ensure_worksheet(reg_sh, PAYMENTS_LEDGER_WS, ACC_LEDGER_COLS)

    # Reuse the chosen worksheet WITHOUT creating another selectbox:
    reg_ws = get_current_registration_worksheet(reg_sh)
    reg_df = pd.DataFrame(reg_ws.get_all_records())
    if reg_df.empty:
        st.warning("لا توجد تسجيلات حالياً.")
        st.stop()

    # If no Registration_ID, build a temporary one (timestamp + last4 of parent phone)
    if "Registration_ID" not in reg_df.columns:
        ts = reg_df.get("Timestamp", pd.Series(range(len(reg_df)))).astype(str).str.replace(r"\D", "", regex=True)
        phone_col = REG_COLUMN_MAP["Phone"]
        phone_series = reg_df.get(phone_col, pd.Series(range(len(reg_df)))).astype(str)
        reg_df["Registration_ID"] = ts + "-" + phone_series.str[-4:]

    # Build selector label
    name_col = REG_COLUMN_MAP["Student_Name"]
    course_col = REG_COLUMN_MAP["Course"]
    reg_df["_label"] = (
        reg_df["Registration_ID"].astype(str)
        + " — "
        + reg_df.get(name_col, "").astype(str)
        + " | "
        + reg_df.get(course_col, "").astype(str)
    )

    selected_label = st.selectbox("اختر تسجيلًا", sorted(reg_df["_label"].tolist()))
    sel_row = reg_df[reg_df["_label"] == selected_label].iloc[0].to_dict()

    # Safe getter from registration row
    def get_val(src: dict, logical_key: str, default: str = "") -> str:
        actual = REG_COLUMN_MAP.get(logical_key, logical_key)
        return str(src.get(actual, default)).strip()

    # Assemble working row
    reg_row = {
        "Registration_ID": sel_row["Registration_ID"],
        "Student_Name": get_val(sel_row, "Student_Name"),
        "Course": get_val(sel_row, "Course"),
        "Phone": get_val(sel_row, "Phone"),
        "PaymentPlan": get_val(sel_row, "PaymentPlan") or "أقساط",  # default "Installments"
        "InstallmentCount": None,
        "Total_Fee": 0.0,
    }

    # Parse installment count (fallback to 6)
    try:
        inst_raw = get_val(sel_row, "InstallmentCount") or str(MAX_INSTALLMENTS)
        reg_row["InstallmentCount"] = int(str(inst_raw).split()[0])
    except Exception:
        reg_row["InstallmentCount"] = MAX_INSTALLMENTS

    # Parse total fee from registration (may be empty)
    try:
        fee_raw = get_val(sel_row, "Total_Fee") or "0"
        reg_row["Total_Fee"] = float(str(fee_raw).replace(",", ""))
    except Exception:
        reg_row["Total_Fee"] = 0.0

    # Load existing master state
    master_df = ws_to_df(ws_master)
    existing = None
    if not master_df.empty and "Registration_ID" in master_df.columns:
        m = master_df[master_df["Registration_ID"] == reg_row["Registration_ID"]]
        if not m.empty:
            existing = m.iloc[0].to_dict()

    paid_to_date = _to_float(existing.get("Paid_To_Date")) if existing else 0.0

    # Determine effective total fee:
    # - If accounting master already has a fee, use it
    # - Else, if registration has a fee (>0), use it
    # - Else, default to FULL_PRICE so math works out-of-the-box
    existing_total = _to_float(existing.get("Total_Fee")) if existing else 0.0
    if existing_total > 0:
        effective_total_fee = existing_total
    elif reg_row["Total_Fee"] > 0:
        effective_total_fee = reg_row["Total_Fee"]
    else:
        effective_total_fee = FULL_PRICE

    remaining = max(effective_total_fee - paid_to_date, 0.0)
    status = (
        existing.get("Status")
        if existing and existing.get("Status")
        else ("Unpaid" if paid_to_date == 0 else "Installments")
    )

    # Load ledger for this registration & compute already paid installments
    ledger_df = ws_to_df(ws_ledger)
    already_paid = _parse_paid_installments_from_ledger(ledger_df, reg_row["Registration_ID"]) if not ledger_df.empty else set()

    # Metrics
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("إجمالي الرسوم (فعّال)", f"{effective_total_fee:,.2f}")
    with c2:
        st.metric("المدفوع حتى الآن", f"{paid_to_date:,.2f}")
    with c3:
        st.metric("المتبقي", f"{remaining:,.2f}")

    st.divider()

    # Payment form
    st.subheader("تسجيل عملية دفع")
    admin_name = st.text_input("مدخل البيانات (المشرف)", value="")

    pay_mode = st.radio("نوع الدفع", ["مكتمل (90)", "أقساط (15 لكل قسط)"], index=0 if math.isclose(remaining, effective_total_fee, abs_tol=1e-6) else 1)

    pay_amount = 0.0
    inst_selected = []

    if pay_mode.startswith("مكتمل"):
        # Full payment always equals FULL_PRICE by business rule
        pay_amount = FULL_PRICE

        # If some amount is already paid, make sure we don't exceed total
        if effective_total_fee > 0 and (paid_to_date + pay_amount) - effective_total_fee > 1e-6:
            st.error("دفعة مكتملة 90 ستتجاوز إجمالي الرسوم لهذا الطالب. استخدم الأقساط بدلاً من ذلك.")
            st.stop()

    else:
        st.caption("اختر الأقساط المدفوعة الآن (يمكن اختيار أكثر من قسط). الأقساط المدفوعة مسبقًا مقفلة.")
        cols = st.columns(MAX_INSTALLMENTS)
        for i in range(1, MAX_INSTALLMENTS + 1):
            paid_already = i in already_paid
            # checked=True if already paid; disabled to avoid duplicates
            with cols[i-1]:
                checked = st.checkbox(str(i), value=paid_already, disabled=paid_already, key=f"inst_{i}")
            if checked and not paid_already:
                inst_selected.append(i)

        count = len(inst_selected)
        pay_amount = INSTALLMENT_PRICE * count

        st.info(f"الأقساط المختارة: {', '.join(map(str, inst_selected)) if inst_selected else 'لا شيء'} — المبلغ = {pay_amount:.2f}")

        if count == 0:
            st.warning("اختر قسطًا واحدًا على الأقل لتسجيل الدفع.")

        # Overpay guard against remaining (if a total fee is defined)
        if effective_total_fee > 0 and (paid_to_date + pay_amount) - effective_total_fee > 1e-6:
            st.error("المبلغ الحالي سيتجاوز إجمالي الرسوم. قلّل عدد الأقساط المختارة.")
            st.stop()

    pay_method = st.selectbox("طريقة السداد", ["نقدًا", "تحويل بنكي", "نقاط بيع", "أخرى"])
    pay_note = st.text_input("ملاحظة (اختياري)")

    # Disable button if amount is zero or (installments chosen but none selected)
    btn_disabled = (pay_amount <= 0) or (pay_mode.startswith("أقساط") and len(inst_selected) == 0)

    if st.button("حفظ وإنشاء إيصال", type="primary", disabled=btn_disabled):
        new_paid_to_date = paid_to_date + pay_amount
        new_remaining = max(effective_total_fee - new_paid_to_date, 0.0)

        new_status = "Completed" if math.isclose(new_remaining, 0.0, abs_tol=1e-6) else "Installments"

        # Upsert master using the effective fee
        master_row = {
            "Registration_ID": reg_row["Registration_ID"],
            "Student_Name": reg_row["Student_Name"],
            "Course": reg_row["Course"],
            "Phone": reg_row["Phone"],
            "PaymentPlan": "Full" if pay_mode.startswith("مكتمل") else "Installments",
            "InstallmentCount": str(MAX_INSTALLMENTS),
            "Total_Fee": f"{effective_total_fee:.2f}",
            "Paid_To_Date": f"{new_paid_to_date:.2f}",
            "Remaining": f"{new_remaining:.2f}",
            "Status": new_status,
            "LastPaymentDate": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "LastReceiptID": ""  # fill after receipt creation
        }

        # Build ledger entry
        receipt_id = f"RCPT-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        inst_field = "" if pay_mode.startswith("مكتمل") else ",".join(map(str, inst_selected))

        ledger_row = {
            "Receipt_ID": receipt_id,
            "Registration_ID": reg_row["Registration_ID"],
            "Payment_Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Amount": f"{pay_amount:.2f}",
            "Method": pay_method,
            "Note": pay_note,
            "Entered_By": admin_name,
            "Installment_Number": inst_field
        }
