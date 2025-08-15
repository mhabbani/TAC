# tac_admin.py
# TAC Admin Panel (Arabic RTL) + Accounting & Receipts inside the same spreadsheet
# ------------------------------------------------------------------------------
# - Preserves old admin features (login, RTL UI, filters, analytics, sharing view)
# - Uses your original Google auth pattern (secrets -> fallback to credentials.json)
# - Reads registrations from existing spreadsheet WITHOUT modifying it
# - Adds two worksheets INSIDE the same spreadsheet (created if missing):
#     * "Accounting"       -> one row per Registration_ID (master)
#     * "Payments_Ledger"  -> one row per payment/receipt
# - Supports Completed or Installments (1–6), prevents overpayment
# - Generates PDF receipts for each payment (download)
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
    "Installment_Number"  # 1..6 or blank for full
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

def get_registration_worksheet(sh):
    """Return the registration worksheet: by name if provided, else the first sheet (.sheet1)."""
    if REG_WORKSHEET_NAME:
        return sh.worksheet(REG_WORKSHEET_NAME)
    return sh.sheet1

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
        line(f"Installment Number: {installment_no}")
    line(f"Remaining Balance: {remaining}")
    line(f"Entered By: {entered_by or 'Admin'}")
    line("")
    line("Thank you for your payment.", italic=True, size=10, dy=12*mm)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf

# =========================
# LOAD REGISTRATIONS (as old)
# =========================
try:
    reg_sh = open_reg_spreadsheet()
    reg_ws = get_registration_worksheet(reg_sh)
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

    # Reload registrations fresh for accounting page
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

    # Parse installment count
    try:
        inst_raw = get_val(sel_row, "InstallmentCount") or "6"
        reg_row["InstallmentCount"] = int(str(inst_raw).split()[0])
    except Exception:
        reg_row["InstallmentCount"] = 6

    # Parse total fee
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

    # Metrics
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("إجمالي الرسوم", f"{reg_row['Total_Fee']:,.2f}")
    with c2:
        st.metric("المدفوع حتى الآن", f"{paid_to_date:,.2f}")
    with c3:
        st.metric("المتبقي", f"{remaining:,.2f}")

    st.divider()

    # Payment form
    st.subheader("تسجيل عملية دفع")
    admin_name = st.text_input("مدخل البيانات (المشرف)", value="")
    payment_type = st.radio("نوع الدفع", ["مكتمل", "أقساط"], index=(0 if remaining == 0 else 1))

    installment_no = None
    if payment_type == "أقساط":
        st.caption("اختر القسط الحالي المدفوع (١–٦). قسط واحد فقط في كل عملية.")
        i1, i2, i3, i4, i5, i6 = st.columns(6)
        checks = []
        with i1: checks.append(st.checkbox("1"))
        with i2: checks.append(st.checkbox("2"))
        with i3: checks.append(st.checkbox("3"))
        with i4: checks.append(st.checkbox("4"))
        with i5: checks.append(st.checkbox("5"))
        with i6: checks.append(st.checkbox("6"))
        if sum(checks) > 1:
            st.error("الرجاء اختيار قسط واحد فقط.")
            st.stop()
        if sum(checks) == 1:
            installment_no = checks.index(True) + 1

    # Suggested installment amount
    suggested_inst_amount = 0.0
    if reg_row["InstallmentCount"] and reg_row["InstallmentCount"] > 0:
        suggested_inst_amount = reg_row["Total_Fee"] / reg_row["InstallmentCount"]

    pay_amount = st.number_input(
        "المبلغ المدفوع",
        min_value=0.0,
        max_value=max(remaining, 0.0),
        step=10.0,
        format="%.2f",
        value=float(min(remaining, suggested_inst_amount)) if payment_type == "أقساط" and remaining > 0 else 0.0,
        help="يتم منع الدفع الزائد تلقائيًا."
    )

    pay_method = st.selectbox("طريقة السداد", ["نقدًا", "تحويل بنكي", "نقاط بيع", "أخرى"])
    pay_note = st.text_input("ملاحظة (اختياري)")

    btn_disabled = (pay_amount <= 0) or (payment_type == "أقساط" and not installment_no)
    if st.button("حفظ وإنشاء إيصال", type="primary", disabled=btn_disabled):
        if pay_amount > remaining + 1e-6:
            st.error("المبلغ المدفوع يتجاوز الرصيد المتبقي.")
            st.stop()

        new_paid_to_date = paid_to_date + pay_amount
        new_remaining = max(reg_row["Total_Fee"] - new_paid_to_date, 0.0)
        new_status = "Completed" if math.isclose(new_remaining, 0.0, abs_tol=1e-6) else (
            "Installments" if payment_type == "أقساط" else "Completed"
        )

        # Upsert master
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
            "LastReceiptID": ""  # fill after receipt creation
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

        # Generate PDF receipt
        pdf_buf = generate_receipt_pdf(
            receipt_id=receipt_id,
            reg_row=reg_row,
            pay_amount=f"{pay_amount:.2f}",
            pay_method=pay_method,
            remaining=f"{new_remaining:.2f}",
            installment_no=installment_no,
            entered_by=admin_name or "Admin"
        )

        st.success(f"تم تسجيل الدفع. تم إنشاء الإيصال {receipt_id}.")
        st.download_button(
            label="📄 تنزيل الإيصال (PDF)",
            data=pdf_buf,
            file_name=f"{receipt_id}.pdf",
            mime="application/pdf"
        )

        st.rerun()

    st.caption("📌 ملاحظة: لا يتم تعديل ورقة التسجيل الأصلية. يتم حفظ بيانات الحسابات في تبويبي 'Accounting' و 'Payments_Ledger' داخل نفس الملف.")

# =========================
# POWER USERS (as old)
# =========================
if role == "power" and page == "بيانات التسجيل":
    st.subheader("📊 بيانات التسجيل")
    st.dataframe(df)
    st.download_button("📥 تحميل البيانات", data=df.to_csv(index=False), file_name="TAC_Registrations.csv")
