# ------------------------------------------------------------------------------
# author : Mohamed Habbani
# version : v1.2.0
# date : 2025-08-17 16:30 EDT
#
# File: tac_admin_app.py
# TAC Admin Panel (Arabic RTL) + Accounting, Corrections, and Receipts Center
# ------------------------------------------------------------------------------
# - Pages:
#     * لوحة المشرف (Admin dashboard & analytics)
#     * المحاسبة والمدفوعات (Payments entry; Full=90, Installment=15; multi-installments)
#     * التصحيحات والتعديلات (Edit/Delete receipts; auto-recalc)
#     * الإيصالات / Receipts (history & re-download receipts by student)
# - Receipts:
#     * Include a centered small TAC logo (upload once per session on Receipts page)
#     * Arabic labels/names supported (optional Arabic font + shaping if available)
#     * Red triangular stamp at lower middle (TAC abbrev, full name, date, signature)
# ------------------------------------------------------------------------------

import streamlit as st
import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import uuid
import io
import math
import os
from typing import Optional, Tuple

# (PDF)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# =========================
# GOOGLE SHEETS AUTH (as old)
# =========================
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds_section = st.secrets.get("gcp_service_account") or st.secrets.get("google_service_account")
    if creds_section:
        creds_dict = dict(creds_section)
        if isinstance(creds_dict.get("private_key"), str):
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        raise KeyError("No credentials found in secrets")
except Exception:
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)

client = gspread.authorize(creds)

# =========================
# CONFIG / CONSTANTS
# =========================
st.set_page_config(page_title="TAC Admin Panel", layout="wide")

REG_SPREADSHEET_NAME = (
    st.secrets.get("tac", {}).get("registration_spreadsheet_name")
    or "TAC-Registeration"
)
REG_WORKSHEET_NAME = st.secrets.get("tac", {}).get("registration_worksheet_name")  # optional

ACCOUNTING_MASTER_WS = "Accounting"
PAYMENTS_LEDGER_WS   = "Payments_Ledger"

# Business rules
FULL_PRICE = 90.0
INSTALLMENT_PRICE = 15.0
MAX_INSTALLMENTS = 6

# Master & ledger schemas
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

# Registration columns (Arabic)
REG_COLUMN_MAP = {
    "Registration_ID": "Registration_ID",          # if not present, we derive one
    "Student_Name": "الاسم",
    "Course": "الكورس",
    "Phone": "رقم اتصال ولي الأمر",
    "PaymentPlan": "خطة الدفع",                    # optional
    "InstallmentCount": "عدد الأقساط",             # optional
    "Total_Fee": "الرسوم الكلية"                   # optional
}

# =========================
# RTL UI
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
# LOGIN
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
# HELPERS (sheets)
# =========================
def open_reg_spreadsheet():
    return client.open(REG_SPREADSHEET_NAME)

def choose_registration_worksheet_once(sh):
    """ONE sidebar selectbox; store chosen title in session_state."""
    try:
        titles = [ws.title for ws in sh.worksheets()]
    except Exception as e:
        st.error(f"تعذر قراءة أوراق العمل: {e}")
        st.stop()

    desired = REG_WORKSHEET_NAME
    candidates = [desired, "Form Responses 1", "Sheet1", "الردود على النموذج 1"]
    chosen = None
    for name in candidates:
        if name and name in titles:
            chosen = name; break
    if not chosen:
        if not titles:
            st.error("لا توجد أي أوراق عمل داخل الملف."); st.stop()
        chosen = titles[0]

    if "reg_ws_title" not in st.session_state:
        st.session_state.reg_ws_title = chosen

    current_idx = titles.index(st.session_state.reg_ws_title) if st.session_state.reg_ws_title in titles else 0
    selected = st.sidebar.selectbox("اختر ورقة التسجيل", titles, index=current_idx, key="reg_ws_choice_unique")
    st.session_state.reg_ws_title = selected

    st.caption(f"📄 Using worksheet: **{st.session_state.reg_ws_title}**")
    return st.session_state.reg_ws_title

def get_current_registration_worksheet(sh):
    title = st.session_state.get("reg_ws_title")
    if not title:
        return sh.sheet1
    return sh.worksheet(title)

def ensure_worksheet(sh, title: str, cols: list):
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=max(len(cols), 10))
        ws.update([cols])
        return ws
    # Ensure header matches exactly
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

def _to_float(x, default=0.0):
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return default

def upsert_master_row(ws_master, row_dict: dict):
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
        rownum = matches[0] + 2
        ws_master.update(f"A{rownum}:{last_col_letter}{rownum}", [values])
    else:
        ws_master.append_row(values)

def append_ledger_row(ws_ledger, row_dict: dict):
    first_row = ws_ledger.row_values(1)
    if first_row != ACC_LEDGER_COLS:
        ws_ledger.clear()
        ws_ledger.update([ACC_LEDGER_COLS])
    ws_ledger.append_row([row_dict.get(c, "") for c in ACC_LEDGER_COLS])

def find_ledger_rownum_by_receipt(ws_ledger, df_ledger: pd.DataFrame, receipt_id: str) -> Optional[int]:
    """Return 1-based sheet row number for given Receipt_ID (including header)."""
    if df_ledger.empty or "Receipt_ID" not in df_ledger.columns:
        return None
    idx = df_ledger.index[df_ledger["Receipt_ID"] == receipt_id].tolist()
    if not idx:
        return None
    return idx[0] + 2  # +1 header, +1 to convert 0-based to 1-based

# =========================
# Arabic font + shaping helpers (best effort)
# =========================
def try_register_arabic_font() -> str:
    """
    Try to register an Arabic-capable TTF. Place a font file at:
    - ./assets/Amiri-Regular.ttf  (recommended)
    - or set secrets: st.secrets['tac']['arabic_font_path']
    Returns the font name to use.
    """
    # secrets override
    font_path = (st.secrets.get("tac", {}) or {}).get("arabic_font_path")
    if not font_path:
        # local defaults
        candidates = [
            "assets/Amiri-Regular.ttf",
            "assets/NotoNaskhArabic-Regular.ttf",
            "assets/Scheherazade-Regular.ttf",
        ]
        for p in candidates:
            if os.path.exists(p):
                font_path = p; break
    if font_path and os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont("ARMain", font_path))
            return "ARMain"
        except Exception:
            pass
    return "Helvetica"  # fallback (Arabic may not render correctly)

def ar_shape(txt: str) -> str:
    """
    Shape Arabic text for ReportLab if possible (arabic_reshaper + bidi).
    If not available, return the original string.
    """
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(str(txt)))
    except Exception:
        return str(txt)

# =========================
# RECEIPT (PDF) with logo + red triangle stamp
# =========================
def draw_red_triangle_stamp(c: canvas.Canvas, center: Tuple[float, float], side_mm: float, date_str: str, ar_font: str):
    side = side_mm * mm
    cx, cy = center
    h = (math.sqrt(3)/2.0) * side
    x1, y1 = cx,         cy + h/2
    x2, y2 = cx - side/2, cy - h/2
    x3, y3 = cx + side/2, cy - h/2

    # Triangle outline
    c.setStrokeColorRGB(1, 0, 0)
    c.setLineWidth(2)
    p = c.beginPath()
    p.moveTo(x1, y1); p.lineTo(x2, y2); p.lineTo(x3, y3); p.close()
    c.drawPath(p, stroke=1, fill=0)

    # Text inside (red)
    c.setFillColorRGB(1, 0, 0)
    # Big "TAC"
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(cx, cy + 6*mm, "TAC")
    # Full name
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(cx, cy, "Together Academic Center")
    # Date + "signature"
    c.setFont("Helvetica-Oblique", 9)
    c.drawCentredString(cx, cy - 5*mm, f"Date: {date_str}")
    c.setFont("Times-Italic", 11)
    c.drawCentredString(cx, cy - 11*mm, "Registrar Office ✍︎")

def generate_receipt_pdf(
    receipt_id: str,
    reg_row: dict,
    pay_amount: str,
    pay_method: str,
    remaining: str,
    installment_no: Optional[str],
    entered_by: str,
    logo_bytes: Optional[bytes] = None,
) -> io.BytesIO:
    """Generate PDF receipt with centered small TAC logo and red triangle stamp."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 20*mm

    # Arabic support (best effort)
    ar_font = try_register_arabic_font()

    def line(txt, dy=8*mm, font="Helvetica", size=11, bold=False, italic=False, centered=False):
        nonlocal y
        # Choose font
        f = font
        if bold and italic:
            f = "Helvetica-BoldOblique"
        elif bold:
            f = "Helvetica-Bold"
        elif italic:
            f = "Helvetica-Oblique"

        # If Arabic likely present, swap to Arabic-capable font and shape
        use_txt = txt
        if any(u"\u0600" <= ch <= u"\u06FF" for ch in txt):
            f = ar_font
            use_txt = ar_shape(txt)

        c.setFont(f, size)
        if centered:
            c.drawCentredString(w/2, y, use_txt)
        else:
            c.drawString(20*mm, y, use_txt)
        y -= dy

    # Header
    line("Together Academic Center (TAC) – Payment Receipt", dy=12*mm, bold=True, size=16, centered=True)
    line(f"Receipt ID: {receipt_id}", centered=True)
    line(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}", centered=True)

    # Small centered TAC logo (optional image)
    if logo_bytes:
        try:
            img = ImageReader(io.BytesIO(logo_bytes))
            logo_w = 35*mm
            iw, ih = img.getSize()
            aspect = ih/iw if iw else 1.0
            logo_h = logo_w * aspect
            c.drawImage(img, (w - logo_w)/2, y - logo_h - 3*mm, width=logo_w, height=logo_h, preserveAspectRatio=True, mask='auto')
            y -= (logo_h + 8*mm)
        except Exception:
            # Fallback: text "TAC"
            c.setFont("Helvetica-Bold", 20)
            c.drawCentredString(w/2, y-10*mm, "TAC")
            y -= 18*mm
    else:
        # Fallback: text "TAC"
        c.setFont("Helvetica-Bold", 20)
        c.drawCentredString(w/2, y-10*mm, "TAC")
        y -= 18*mm

    # Body
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

    # Red triangular stamp at lower middle
    stamp_center = (w/2, 35*mm)
    draw_red_triangle_stamp(c, stamp_center, side_mm=70, date_str=datetime.now().strftime("%Y-%m-%d"), ar_font=ar_font)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf

# =========================
# LOAD REGISTRATIONS
# =========================
try:
    reg_sh = open_reg_spreadsheet()
    choose_registration_worksheet_once(reg_sh)
    reg_ws = get_current_registration_worksheet(reg_sh)
    df = pd.DataFrame(reg_ws.get_all_records())
except Exception as e:
    st.error(f"❌ Failed to load data: {e}")
    st.stop()

# =========================
# NAV
# =========================
if role == "admin":
    page = st.sidebar.radio(
        "القائمة",
        ["لوحة المشرف", "المحاسبة والمدفوعات", "التصحيحات والتعديلات", "الإيصالات / Receipts"]
    )
else:
    page = st.sidebar.radio("القائمة", ["بيانات التسجيل"])

# =========================
# ADMIN PAGE (unchanged basics)
# =========================
if role == "admin" and page == "لوحة المشرف":
    st.subheader("👤 لوحة المشرف")
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
    age_col = "العمر" if "العمر" in df.columns else None
    addr_col = "العنوان" if "العنوان" in df.columns else None

    if age_col:
        age_filter = col3.selectbox("📊 تصفية حسب العمر", ["الكل"] + sorted(df[age_col].dropna().astype(str).unique().tolist()))
    else:
        age_filter = "الكل"; col3.info("لا يوجد عمود 'العمر'")

    if addr_col:
        country_filter = st.selectbox("🌍 تصفية حسب الدولة",
            ["الكل"] + sorted(df[addr_col].dropna().apply(lambda x: str(x).split("-")[0].strip()).unique().tolist()))
    else:
        country_filter = "الكل"; st.info("لا يوجد عمود 'العنوان'")

    filtered_df = df.copy()
    if search_name:
        name_col = "الاسم" if "الاسم" in df.columns else None
        course_col = "الكورس" if "الكورس" in df.columns else None
        m1 = filtered_df[name_col].astype(str).str.contains(search_name, case=False, na=False) if name_col else False
        m2 = filtered_df[course_col].astype(str).str.contains(search_name, case=False, na=False) if course_col else False
        filtered_df = filtered_df[m1 | m2] if (name_col or course_col) else filtered_df
    if age_filter != "الكل" and age_col:
        filtered_df = filtered_df[filtered_df[age_col].astype(str) == str(age_filter)]
    if country_filter != "الكل" and addr_col:
        filtered_df = filtered_df[filtered_df[addr_col].astype(str).str.startswith(country_filter)]

    if row_limit == "الكل": st.dataframe(filtered_df)
    else:                   st.dataframe(filtered_df.tail(int(row_limit)))

    st.info("استخدم القائمة الجانبية → المحاسبة والمدفوعات / التصحيحات والتعديلات / الإيصالات.")

# =========================
# ACCOUNTING PAGE (payments)
# =========================
def _parse_paid_installments_from_ledger(ledger_df: pd.DataFrame, reg_id: str) -> set:
    if ledger_df.empty: return set()
    sub = ledger_df[ledger_df.get("Registration_ID", "") == reg_id]
    paid = set()
    for val in sub.get("Installment_Number", []):
        s = str(val).strip()
        if not s: continue
        for p in [t.strip() for t in s.split(",")]:
            if p.isdigit(): paid.add(int(p))
    return paid

if role == "admin" and page == "المحاسبة والمدفوعات":
    st.subheader("💳 المحاسبة والمدفوعات")

    try:
        ws_master = ensure_worksheet(reg_sh, ACCOUNTING_MASTER_WS, ACC_MASTER_COLS)
        ws_ledger = ensure_worksheet(reg_sh, PAYMENTS_LEDGER_WS, ACC_LEDGER_COLS)
    except Exception as e:
        st.error(f"❌ Cannot ensure accounting worksheets: {e}")
        st.stop()

    reg_ws = get_current_registration_worksheet(reg_sh)
    reg_df = pd.DataFrame(reg_ws.get_all_records())
    if reg_df.empty:
        st.warning("لا توجد تسجيلات حالياً."); st.stop()

    # derive Registration_ID if missing
    if "Registration_ID" not in reg_df.columns:
        ts = reg_df.get("Timestamp", pd.Series(range(len(reg_df)))).astype(str).str.replace(r"\D", "", regex=True)
        phone_col = REG_COLUMN_MAP["Phone"]
        phone_series = reg_df.get(phone_col, pd.Series(range(len(reg_df)))).astype(str)
        reg_df["Registration_ID"] = ts + "-" + phone_series.str[-4:]

    name_col = REG_COLUMN_MAP["Student_Name"]; course_col = REG_COLUMN_MAP["Course"]
    reg_df["_label"] = (reg_df["Registration_ID"].astype(str) + " — " +
                        reg_df.get(name_col, "").astype(str) + " | " +
                        reg_df.get(course_col, "").astype(str))

    selected_label = st.selectbox("اختر تسجيلًا", sorted(reg_df["_label"].tolist()))
    sel_row = reg_df[reg_df["_label"] == selected_label].iloc[0].to_dict()

    def get_val(src: dict, logical_key: str, default: str = "") -> str:
        actual = REG_COLUMN_MAP.get(logical_key, logical_key)
        return str(src.get(actual, default)).strip()

    reg_row = {
        "Registration_ID": sel_row["Registration_ID"],
        "Student_Name": get_val(sel_row, "Student_Name"),
        "Course": get_val(sel_row, "Course"),
        "Phone": get_val(sel_row, "Phone"),
        "PaymentPlan": get_val(sel_row, "PaymentPlan") or "أقساط",
        "InstallmentCount": None,
        "Total_Fee": 0.0,
    }
    try:
        inst_raw = get_val(sel_row, "InstallmentCount") or str(MAX_INSTALLMENTS)
        reg_row["InstallmentCount"] = int(str(inst_raw).split()[0])
    except Exception:
        reg_row["InstallmentCount"] = MAX_INSTALLMENTS
    try:
        fee_raw = get_val(sel_row, "Total_Fee") or "0"
        reg_row["Total_Fee"] = float(str(fee_raw).replace(",", ""))
    except Exception:
        reg_row["Total_Fee"] = 0.0

    ws_master_df = ws_to_df(ws_master)
    existing = None
    if not ws_master_df.empty and "Registration_ID" in ws_master_df.columns:
        m = ws_master_df[ws_master_df["Registration_ID"] == reg_row["Registration_ID"]]
        if not m.empty: existing = m.iloc[0].to_dict()

    paid_to_date = _to_float(existing.get("Paid_To_Date")) if existing else 0.0
    existing_total = _to_float(existing.get("Total_Fee")) if existing else 0.0
    effective_total_fee = existing_total if existing_total > 0 else (reg_row["Total_Fee"] if reg_row["Total_Fee"] > 0 else FULL_PRICE)
    remaining = max(effective_total_fee - paid_to_date, 0.0)

    ledger_df = ws_to_df(ws_ledger)
    already_paid = _parse_paid_installments_from_ledger(ledger_df, reg_row["Registration_ID"]) if not ledger_df.empty else set()

    c1, c2, c3 = st.columns(3)
    with c1: st.metric("إجمالي الرسوم (فعّال)", f"{effective_total_fee:,.2f}")
    with c2: st.metric("المدفوع حتى الآن", f"{paid_to_date:,.2f}")
    with c3: st.metric("المتبقي", f"{remaining:,.2f}")

    st.divider()
    st.subheader("تسجيل عملية دفع")
    admin_name = st.text_input("مدخل البيانات (المشرف)", value="")
    pay_mode = st.radio("نوع الدفع", ["مكتمل (90)", "أقساط (15 لكل قسط)"],
                        index=0 if math.isclose(remaining, effective_total_fee, abs_tol=1e-6) else 1)

    pay_amount = 0.0
    inst_selected = []

    if pay_mode.startswith("مكتمل"):
        pay_amount = min(FULL_PRICE, remaining if effective_total_fee > 0 else FULL_PRICE)
        if pay_amount <= 0:
            st.warning("لا يوجد رصيد متبقٍ لدفع كامل.")
    else:
        st.caption("اختر الأقساط المدفوعة الآن (يمكن اختيار أكثر من قسط). الأقساط المدفوعة مسبقًا مقفلة.")
        cols = st.columns(MAX_INSTALLMENTS)
        for i in range(1, MAX_INSTALLMENTS + 1):
            paid_already = i in already_paid
            with cols[i-1]:
                checked = st.checkbox(str(i), value=paid_already, disabled=paid_already, key=f"inst_{i}")
            if checked and not paid_already:
                inst_selected.append(i)
        count = len(inst_selected)
        pay_amount = INSTALLMENT_PRICE * count
        st.info(f"الأقساط المختارة: {', '.join(map(str, inst_selected)) if inst_selected else 'لا شيء'} — المبلغ = {pay_amount:.2f}")
        if effective_total_fee > 0 and (paid_to_date + pay_amount) - effective_total_fee > 1e-6:
            st.error("المبلغ الحالي سيتجاوز إجمالي الرسوم. قلّل عدد الأقساط المختارة.")
            inst_selected = []; pay_amount = 0.0

    pay_method = st.selectbox("طريقة السداد", ["نقدًا", "تحويل بنكي", "نقاط بيع", "أخرى"])
    pay_note = st.text_input("ملاحظة (اختياري)")

    btn_disabled = (pay_amount <= 0) or (pay_mode.startswith("أقساط") and len(inst_selected) == 0)

    if st.button("حفظ وإنشاء إيصال", type="primary", disabled=btn_disabled):
        try:
            new_paid_to_date = paid_to_date + pay_amount
            new_remaining = max(effective_total_fee - new_paid_to_date, 0.0)
            new_status = "Completed" if math.isclose(new_remaining, 0.0, abs_tol=1e-6) else "Installments"

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
            append_ledger_row(ws_ledger, ledger_row)

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
                "LastReceiptID": receipt_id
            }
            upsert_master_row(ws_master, master_row)

            # Receipt download immediately (uses any uploaded logo this session)
            logo_bytes = st.session_state.get("tac_logo_bytes")
            pdf_buf = generate_receipt_pdf(
                receipt_id=receipt_id,
                reg_row=reg_row,
                pay_amount=f"{pay_amount:.2f}",
                pay_method=pay_method,
                remaining=f"{new_remaining:.2f}",
                installment_no=(inst_field if inst_field else None),
                entered_by=admin_name or "Admin",
                logo_bytes=logo_bytes
            )

            st.success(f"تم تسجيل الدفع. تم إنشاء الإيصال {receipt_id}.")
            st.download_button("📄 تنزيل الإيصال (PDF)", data=pdf_buf, file_name=f"{receipt_id}.pdf", mime="application/pdf")
            st.rerun()
        except Exception as e:
            st.error("Saving failed.")
            st.exception(e)

    # Past receipts for this student (always active if any payments exist)
    st.divider()
    st.markdown("### 🧾 إيصالات سابقة لهذا الطالب (إعادة تنزيل)")
    sub = ws_to_df(ws_ledger)
    if not sub.empty:
        sub = sub[sub.get("Registration_ID","") == reg_row["Registration_ID"]].copy()
        if not sub.empty:
            try:
                sub["_dt"] = pd.to_datetime(sub["Payment_Date"], errors="coerce")
                sub = sub.sort_values("_dt", ascending=False, na_position="last")
            except Exception:
                pass
            for _, r in sub.iterrows():
                rid = r.get("Receipt_ID","")
                amt = r.get("Amount","")
                dt  = r.get("Payment_Date","")
                meth= r.get("Method","")
                inst= r.get("Installment_Number","")
                # Remaining (current snapshot)
                new_paid_to_date = _to_float(ws_master_df[ws_master_df["Registration_ID"] == reg_row["Registration_ID"]].iloc[0]["Paid_To_Date"]) if not ws_master_df.empty else 0.0
                curr_remaining = max(effective_total_fee - new_paid_to_date, 0.0)
                colA, colB = st.columns([3,1])
                with colA:
                    st.write(f"**{rid}** — {dt} — {amt} — {meth} — أقساط: {inst or '—'}")
                with colB:
                    logo_bytes = st.session_state.get("tac_logo_bytes")
                    pdf_buf = generate_receipt_pdf(
                        receipt_id=rid,
                        reg_row=reg_row,
                        pay_amount=str(amt),
                        pay_method=str(meth),
                        remaining=f"{curr_remaining:.2f}",
                        installment_no=(inst if inst else None),
                        entered_by="Admin",
                        logo_bytes=logo_bytes
                    )
                    st.download_button("تنزيل", data=pdf_buf, file_name=f"{rid}.pdf", mime="application/pdf", key=f"dl_{rid}")

# =========================
# CORRECTIONS PAGE (edit/delete receipts)
# =========================
def recalc_master_for_registration(ws_master, ws_ledger, reg_id: str, reg_df: pd.DataFrame):
    ledger_df = ws_to_df(ws_ledger)
    if ledger_df.empty:
        total_paid = 0.0
    else:
        sub = ledger_df[ledger_df.get("Registration_ID", "") == reg_id]
        total_paid = sum(_to_float(a) for a in sub.get("Amount", []))

    master_df = ws_to_df(ws_master)
    existing = None
    if not master_df.empty and "Registration_ID" in master_df.columns:
        m = master_df[master_df["Registration_ID"] == reg_id]
        if not m.empty:
            existing = m.iloc[0].to_dict()

    if existing:
        name = existing.get("Student_Name", "")
        course = existing.get("Course", "")
        phone = existing.get("Phone", "")
        existing_total = _to_float(existing.get("Total_Fee"))
    else:
        match = reg_df[reg_df.get("Registration_ID","") == reg_id]
        if not match.empty:
            row = match.iloc[0].to_dict()
            name = str(row.get(REG_COLUMN_MAP["Student_Name"], ""))
            course = str(row.get(REG_COLUMN_MAP["Course"], ""))
            phone = str(row.get(REG_COLUMN_MAP["Phone"], ""))
            existing_total = _to_float(row.get(REG_COLUMN_MAP["Total_Fee"]))
        else:
            name = course = phone = ""
            existing_total = 0.0

    effective_total_fee = _to_float(existing.get("Total_Fee")) if existing else 0.0
    if effective_total_fee <= 0:
        effective_total_fee = existing_total if existing_total > 0 else FULL_PRICE

    remaining = max(effective_total_fee - total_paid, 0.0)
    status = "Completed" if math.isclose(remaining, 0.0, abs_tol=1e-6) else ("Unpaid" if total_paid == 0 else "Installments")

    master_row = {
        "Registration_ID": reg_id,
        "Student_Name": name,
        "Course": course,
        "Phone": phone,
        "PaymentPlan": existing.get("PaymentPlan", "Installments") if existing else "Installments",
        "InstallmentCount": str(MAX_INSTALLMENTS),
        "Total_Fee": f"{effective_total_fee:.2f}",
        "Paid_To_Date": f"{total_paid:.2f}",
        "Remaining": f"{remaining:.2f}",
        "Status": status,
        "LastPaymentDate": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "LastReceiptID": existing.get("LastReceiptID","") if existing else ""
    }
    upsert_master_row(ws_master, master_row)

if role == "admin" and page == "التصحيحات والتعديلات":
    st.subheader("🛠️ التصحيحات والتعديلات")
    try:
        ws_master = ensure_worksheet(reg_sh, ACCOUNTING_MASTER_WS, ACC_MASTER_COLS)
        ws_ledger = ensure_worksheet(reg_sh, PAYMENTS_LEDGER_WS, ACC_LEDGER_COLS)
    except Exception as e:
        st.error(f"❌ Cannot ensure accounting worksheets: {e}")
        st.stop()

    reg_ws = get_current_registration_worksheet(reg_sh)
    reg_df = pd.DataFrame(reg_ws.get_all_records())
    ledger_df = ws_to_df(ws_ledger)

    if ledger_df.empty:
        st.info("لا توجد إيصالات في الدفتر بعد."); st.stop()

    # Student map for labeling
    reg_map = {}
    if not reg_df.empty:
        if "Registration_ID" not in reg_df.columns:
            ts = reg_df.get("Timestamp", pd.Series(range(len(reg_df)))).astype(str).str.replace(r"\D", "", regex=True)
            phone_col = REG_COLUMN_MAP["Phone"]
            phone_series = reg_df.get(phone_col, pd.Series(range(len(reg_df)))).astype(str)
            reg_df["Registration_ID"] = ts + "-" + phone_series.str[-4:]
        name_col = REG_COLUMN_MAP["Student_Name"]
        for _, r in reg_df.iterrows():
            reg_map[str(r.get("Registration_ID",""))] = str(r.get(name_col, ""))

    # Sort
    if "Payment_Date" in ledger_df.columns:
        try:
            ledger_df["_dt"] = pd.to_datetime(ledger_df["Payment_Date"], errors="coerce")
            ledger_df = ledger_df.sort_values("_dt", ascending=False, na_position="last")
        except Exception:
            pass

    def _label_row(row):
        rid = str(row.get("Receipt_ID",""))
        reg_id = str(row.get("Registration_ID",""))
        nm = reg_map.get(reg_id, "")
        amt = str(row.get("Amount",""))
        dt  = str(row.get("Payment_Date",""))
        return f"{rid} — {reg_id}{(' / ' + nm) if nm else ''} — {amt} — {dt}"

    options = [ _label_row(r) for _, r in ledger_df.iterrows() ]
    selected_receipt_label = st.selectbox("اختر إيصالًا لتعديله/حذفه", options)
    sel_idx = options.index(selected_receipt_label)
    sel_row = ledger_df.iloc[sel_idx].to_dict()

    # Editable fields
    st.markdown("### ✏️ تعديل بيانات الإيصال")
    st.text_input("Receipt ID (غير قابل للتعديل)", value=sel_row.get("Receipt_ID",""), disabled=True, key="edit_receipt_id")
    reg_id_val = st.text_input("Registration ID", value=sel_row.get("Registration_ID",""))
    pay_date_val = st.text_input("Payment Date (YYYY-MM-DD HH:MM)", value=sel_row.get("Payment_Date",""))
    amount_val = st.number_input("Amount", min_value=0.0, step=5.0, format="%.2f", value=_to_float(sel_row.get("Amount",0)))
    method_val = st.selectbox("Method", ["نقدًا", "تحويل بنكي", "نقاط بيع", "أخرى"],
                              index=["نقدًا","تحويل بنكي","نقاط بيع","أخرى"].index(sel_row.get("Method","نقدًا")) if sel_row.get("Method","نقدًا") in ["نقدًا","تحويل بنكي","نقاط بيع","أخرى"] else 0)
    note_val = st.text_input("Note", value=sel_row.get("Note",""))
    entered_by_val = st.text_input("Entered By", value=sel_row.get("Entered_By",""))
    inst_val = st.text_input("Installment Number(s) e.g. 1,2,3", value=sel_row.get("Installment_Number",""))

    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 Save changes"):
            try:
                inst_clean = ",".join([p.strip() for p in inst_val.split(",") if p.strip()])
                if inst_clean and not all(p.isdigit() and 1 <= int(p) <= MAX_INSTALLMENTS for p in inst_clean.split(",")):
                    st.error(f"Installment numbers must be between 1 and {MAX_INSTALLMENTS}."); st.stop()

                rownum = find_ledger_rownum_by_receipt(ws_ledger, ledger_df, sel_row.get("Receipt_ID",""))
                if not rownum:
                    st.error("لم يتم العثور على صف هذا الإيصال في الورقة."); st.stop()

                updated_vals = [
                    sel_row.get("Receipt_ID",""),
                    reg_id_val,
                    pay_date_val,
                    f"{amount_val:.2f}",
                    method_val,
                    note_val,
                    entered_by_val,
                    inst_clean
                ]
                last_col_letter = chr(64 + len(ACC_LEDGER_COLS))
                ws_ledger.update(f"A{rownum}:{last_col_letter}{rownum}", [updated_vals])

                recalc_master_for_registration(ws_master, ws_ledger, reg_id_val, reg_df)

                st.success("تم حفظ التعديلات وإعادة احتساب الرصيد.")
                st.rerun()
            except Exception as e:
                st.error("فشل حفظ التعديلات.")
                st.exception(e)

    with c2:
        st.markdown("### 🗑️ حذف الإيصال")
        confirm = st.checkbox("أؤكد حذف هذا الإيصال نهائيًا")
        if st.button("Delete receipt", type="secondary", disabled=not confirm):
            try:
                rownum = find_ledger_rownum_by_receipt(ws_ledger, ledger_df, sel_row.get("Receipt_ID",""))
                if not rownum:
                    st.error("لم يتم العثور على صف هذا الإيصال في الورقة."); st.stop()
                ws_ledger.delete_rows(rownum)
                recalc_master_for_registration(ws_master, ws_ledger, sel_row.get("Registration_ID",""), reg_df)
                st.success("تم حذف الإيصال وإعادة احتساب الرصيد.")
                st.rerun()
            except Exception as e:
                st.error("فشل حذف الإيصال.")
                st.exception(e)

# =========================
# RECEIPTS PAGE (history & re-download)
# =========================
if role == "admin" and page == "الإيصالات / Receipts":
    st.subheader("🧾 الإيصالات / Receipts")

    # Logo uploader (kept for this session)
    col_logo_u, col_logo_prev = st.columns([2,1])
    with col_logo_u:
        logo_file = st.file_uploader("Upload TAC logo (PNG/JPG) – optional", type=["png","jpg","jpeg"])
        if logo_file is not None:
            st.session_state["tac_logo_bytes"] = logo_file.read()
            st.success("Logo loaded for this session.")
    with col_logo_prev:
        if st.session_state.get("tac_logo_bytes"):
            st.image(st.session_state["tac_logo_bytes"], caption="Current TAC Logo", use_container_width=True)

    # Ensure tabs
    try:
        ws_master = ensure_worksheet(reg_sh, ACCOUNTING_MASTER_WS, ACC_MASTER_COLS)
        ws_ledger = ensure_worksheet(reg_sh, PAYMENTS_LEDGER_WS, ACC_LEDGER_COLS)
    except Exception as e:
        st.error(f"❌ Cannot ensure accounting worksheets: {e}")
        st.stop()

    reg_ws = get_current_registration_worksheet(reg_sh)
    reg_df = pd.DataFrame(reg_ws.get_all_records())
    master_df = ws_to_df(ws_master)
    ledger_df = ws_to_df(ws_ledger)

    if ledger_df.empty:
        st.info("لا توجد إيصالات مسجلة بعد."); st.stop()

    # Build student list from ledger
    reg_ids = sorted(set(ledger_df.get("Registration_ID", [])))
    # Map to names
    reg_name_map = {}
    if not reg_df.empty:
        if "Registration_ID" not in reg_df.columns:
            ts = reg_df.get("Timestamp", pd.Series(range(len(reg_df)))).astype(str).str.replace(r"\D", "", regex=True)
            phone_col = REG_COLUMN_MAP["Phone"]
            phone_series = reg_df.get(phone_col, pd.Series(range(len(reg_df)))).astype(str)
            reg_df["Registration_ID"] = ts + "-" + phone_series.str[-4:]
        name_col = REG_COLUMN_MAP["Student_Name"]
        for _, r in reg_df.iterrows():
            reg_name_map[str(r.get("Registration_ID",""))] = str(r.get(name_col,""))

    labels = [f"{rid} — {reg_name_map.get(str(rid),'')}" for rid in reg_ids]
    chosen = st.selectbox("اختر الطالب / Registration", labels)
    chosen_reg_id = reg_ids[labels.index(chosen)]

    # Effective total fee for this registration
    if not master_df.empty and "Registration_ID" in master_df.columns:
        ex = master_df[master_df["Registration_ID"] == chosen_reg_id]
    else:
        ex = pd.DataFrame()
    if not ex.empty:
        eff_total = _to_float(ex.iloc[0].get("Total_Fee", FULL_PRICE)) or FULL_PRICE
        student_name = ex.iloc[0].get("Student_Name","")
        course = ex.iloc[0].get("Course","")
        phone  = ex.iloc[0].get("Phone","")
    else:
        eff_total = FULL_PRICE
        row = reg_df[reg_df["Registration_ID"] == chosen_reg_id].iloc[0] if (not reg_df.empty and (reg_df["Registration_ID"] == chosen_reg_id).any()) else {}
        student_name = str(row.get(REG_COLUMN_MAP["Student_Name"], "")) if isinstance(row, dict) else ""
        course = str(row.get(REG_COLUMN_MAP["Course"], "")) if isinstance(row, dict) else ""
        phone  = str(row.get(REG_COLUMN_MAP["Phone"], "")) if isinstance(row, dict) else ""

    # Ledger subset & chronological paid-to-date to compute remaining at each receipt time
    sub = ledger_df[ledger_df["Registration_ID"] == chosen_reg_id].copy()
    try:
        sub["_dt"] = pd.to_datetime(sub["Payment_Date"], errors="coerce")
        sub = sub.sort_values("_dt", ascending=True, na_position="last")
    except Exception:
        sub["_dt"] = None

    sub["PaidCum"] = sub["Amount"].apply(_to_float).cumsum()
    sub["RemainingAtThis"] = (eff_total - sub["PaidCum"]).clip(lower=0)

    # Show list newest first
    try:
        sub = sub.sort_values("_dt", ascending=False, na_position="last")
    except Exception:
        pass

    st.markdown(f"**Student:** {student_name} — **Course:** {course} — **Phone:** {phone} — **Total:** {eff_total:.2f}")

    if sub.empty:
        st.info("لا توجد إيصالات لهذا الطالب بعد.")
    else:
        for _, r in sub.iterrows():
            rid = r.get("Receipt_ID","")
            dt  = r.get("Payment_Date","")
            amt = f"{_to_float(r.get('Amount',0)):.2f}"
            inst= r.get("Installment_Number","")
            meth= r.get("Method","")
            remaining_at = f"{_to_float(r.get('RemainingAtThis', eff_total)):.2f}"

            cA, cB = st.columns([3,1])
            with cA:
                st.write(f"**{rid}** — {dt} — {amt} — {meth} — أقساط: {inst or '—'} — المتبقي بعد هذه الدفعة: {remaining_at}")
            with cB:
                reg_row = {
                    "Registration_ID": chosen_reg_id,
                    "Student_Name": student_name,
                    "Course": course,
                    "Phone": phone,
                }
                logo_bytes = st.session_state.get("tac_logo_bytes")
                pdf_buf = generate_receipt_pdf(
                    receipt_id=rid,
                    reg_row=reg_row,
                    pay_amount=str(amt),
                    pay_method=str(meth),
                    remaining=str(remaining_at),
                    installment_no=(inst if inst else None),
                    entered_by=r.get("Entered_By","Admin"),
                    logo_bytes=logo_bytes
                )
                st.download_button("تنزيل", data=pdf_buf, file_name=f"{rid}.pdf", mime="application/pdf", key=f"dl_hist_{rid}")

# =========================
# POWER USERS
# =========================
if role == "power" and page == "بيانات التسجيل":
    st.subheader("📊 بيانات التسجيل")
    st.dataframe(df)
    st.download_button("📥 تحميل البيانات", data=df.to_csv(index=False), file_name="TAC_Registrations.csv")
