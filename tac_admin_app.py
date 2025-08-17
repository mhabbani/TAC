# ------------------------------------------------------------------------------
# author : Mohamed Habbani
# version : v1.3.5
# date : 2025-08-17 19:55 EDT
#
# File: tac_admin_app.py
# TAC Admin Panel — Accounting, Corrections, and Receipts Center
# ------------------------------------------------------------------------------
# CHANGES in v1.3.5
# - Robust method normalization: Arabic legacy values now display as English ("Cash",
#   "Bank transfer - Sudan", "Bank transfer - Saudi") everywhere (lists, PDFs, corrections).
# - Kept registration "طريقة السداد" reflection (if the column exists).
# - Defensive guards for empty/missing columns; safer label generation.
# - Quota-friendly caching remains; worksheet switching stays disabled by default.
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
import time
from typing import Optional, Tuple

# (PDF)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# =========================
# CONFIG / CONSTANTS
# =========================
st.set_page_config(page_title="TAC Admin Panel", layout="wide")

# Tuition rules
FULL_PRICE = 90.0
INSTALLMENT_PRICE = 15.0
MAX_INSTALLMENTS = 6

# Spreadsheet names (from secrets if available)
REG_SPREADSHEET_NAME = (
    st.secrets.get("tac", {}).get("registration_spreadsheet_name")
    or "TAC-Registeration"
)
REG_WORKSHEET_NAME = st.secrets.get("tac", {}).get("registration_worksheet_name")  # optional

# Reduce quota usage: avoid listing sheets each run
ALLOW_WS_SWITCH = False

ACCOUNTING_MASTER_WS = "Accounting"
PAYMENTS_LEDGER_WS   = "Payments_Ledger"

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
    "Installment_Number"
]

# Registration columns (Arabic headers in your form)
REG_COLUMN_MAP = {
    "Registration_ID": "Registration_ID",          # if missing, derived
    "Student_Name": "الاسم",
    "Course": "الكورس",
    "Phone": "رقم اتصال ولي الأمر",
    "PaymentPlan": "خطة الدفع",                    # optional
    "InstallmentCount": "عدد الأقساط",             # optional
    "Total_Fee": "الرسوم الكلية"                   # optional
}

# Payment methods (English only for saving / UI)
METHOD_CHOICES = ["Cash", "Bank transfer - Sudan", "Bank transfer - Saudi"]

# Display map (normalize legacy values into English)
METHOD_DISPLAY_MAP = {
    "Cash": "Cash",
    "cash": "Cash",
    "Bank transfer - Sudan": "Bank transfer - Sudan",
    "Bank transfer - Saudi": "Bank transfer - Saudi",
    # Legacy Arabic synonyms → English
    "نقدًا": "Cash",
    "نقدا": "Cash",
    "نقد": "Cash",
    "تحويل بنكي": "Bank transfer - Sudan",
    "تحويل بنكي - السودان": "Bank transfer - Sudan",
    "تحويل بنكي - السعودية": "Bank transfer - Saudi",
    "السعودية": "Bank transfer - Saudi",
    "السودان": "Bank transfer - Sudan",
    "نقاط بيع": "Cash",
    "POS":"Bank transfer - Saudi",
}

def normalize_method_value(val: str) -> str:
    if val is None:
        return "Cash"
    s = str(val).strip()
    return METHOD_DISPLAY_MAP.get(s, s if s in METHOD_CHOICES else "Cash")

# Accept both English and Arabic header names for ledger method (used by normalization)
LEDGER_METHOD_COLS = ["Method", "طريقة السداد"]

# =========================
# UI (RTL)
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
# GOOGLE AUTH
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
# CACHING HELPERS (reduce API reads)
# =========================
def _cache_get(key):
    return st.session_state.setdefault("_cache", {}).get(key)

def _cache_set(key, df):
    st.session_state.setdefault("_cache", {})[key] = {"df": df, "ts": time.time()}

def read_ws_df_cached(ws, cache_key: str, ttl_sec: int = 180) -> pd.DataFrame:
    """Return ws as DataFrame with session-level TTL cache."""
    entry = _cache_get(cache_key)
    if entry and (time.time() - entry["ts"] < ttl_sec):
        return entry["df"].copy()
    rows = ws.get_all_values()
    if not rows:
        df = pd.DataFrame(columns=[])
    else:
        header, data = rows[0], rows[1:]
        df = pd.DataFrame(data, columns=header)
    _cache_set(cache_key, df)
    return df.copy()

def ensure_worksheet_once(sh, title: str, cols: list):
    """Create a sheet with header ONCE per session."""
    flag_key = f"_ws_ready_{title}"
    if st.session_state.get(flag_key):
        try:
            return sh.worksheet(title)
        except gspread.WorksheetNotFound:
            pass
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=max(len(cols), 10))
        ws.update([cols])
        st.session_state[flag_key] = True
        return ws
    if not st.session_state.get(flag_key):
        existing_header = ws.row_values(1)
        if existing_header != cols:
            ws.delete_rows(1)
            ws.insert_row(cols, 1)
        st.session_state[flag_key] = True
    return ws

def open_reg_spreadsheet_once():
    if "reg_spreadsheet_opened" in st.session_state:
        return st.session_state["reg_spreadsheet_opened"]
    sh = client.open(REG_SPREADSHEET_NAME)
    st.session_state["reg_spreadsheet_opened"] = sh
    return sh

def choose_registration_worksheet_fixed(sh):
    """Pick one worksheet with minimal API calls."""
    if "reg_ws_title" in st.session_state:
        return st.session_state["reg_ws_title"]

    title = None
    if REG_WORKSHEET_NAME:
        try:
            sh.worksheet(REG_WORKSHEET_NAME)
            title = REG_WORKSHEET_NAME
        except Exception:
            title = None
    if not title:
        for nm in ["Form Responses 1", "Sheet1", "الردود على النموذج 1"]:
            try:
                sh.worksheet(nm); title = nm; break
            except Exception:
                continue
    if not title:
        try:
            title = sh.sheet1.title
        except Exception as e:
            st.error(f"Unable to pick a worksheet: {e}")
            st.stop()

    st.session_state["reg_ws_title"] = title
    return title

def choose_registration_worksheet_with_switch(sh):
    """Optional worksheet switcher (disabled by default)."""
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
        chosen = titles[0] if titles else st.stop()
    if "reg_ws_title" not in st.session_state:
        st.session_state.reg_ws_title = chosen
    current_idx = titles.index(st.session_state.reg_ws_title) if st.session_state.reg_ws_title in titles else 0
    selected = st.sidebar.selectbox("اختر ورقة التسجيل", titles, index=current_idx, key="reg_ws_choice_unique")
    st.session_state.reg_ws_title = selected
    return selected

def get_current_registration_worksheet(sh):
    title = st.session_state.get("reg_ws_title")
    if not title:
        return sh.sheet1
    return sh.worksheet(title)

# =========================
# Ledger column normalization + helpers
# =========================
def normalize_ledger_columns(df: pd.DataFrame) -> pd.DataFrame:
    """If ledger uses Arabic 'طريقة السداد', rename it to 'Method' for consistency."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df
    if "طريقة السداد" in df.columns and "Method" not in df.columns:
        df = df.rename(columns={"طريقة السداد": "Method"})
    return df

def get_rownum_for_selected_reg(reg_df: pd.DataFrame, selected_label: str) -> int:
    """Return 1-based sheet row number for the selected registration (including header)."""
    try:
        idxs = reg_df.index[reg_df["_label"] == selected_label].tolist()
        if not idxs:
            return -1
        return idxs[0] + 2  # +1 header +1 convert to 1-based
    except Exception:
        return -1

# =========================
# Arabic / Transliteration helpers
# =========================
AR_NUM = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
AR_DIAC = dict.fromkeys([ord(c) for c in "ًٌٍَُِّْـ"], None)

AR_MAP = {
    "أ":"a","ا":"a","إ":"i","آ":"aa","ب":"b","ت":"t","ث":"th","ج":"j","ح":"h","خ":"kh","د":"d","ذ":"dh","ر":"r","ز":"z",
    "س":"s","ش":"sh","ص":"s","ض":"d","ط":"t","ظ":"z","ع":"a","غ":"gh","ف":"f","ق":"q","ك":"k","ل":"l","م":"m","ن":"n",
    "ه":"h","و":"w","ؤ":"u","ي":"y","ئ":"i","ى":"a","ة":"h","ء":"'", "لا":"la","ﻻ":"la"
}
def to_latin_if_arabic(text: str) -> str:
    if not isinstance(text, str):
        return str(text)
    s = text.translate(AR_NUM)
    s = s.translate(AR_DIAC) if hasattr(s, "translate") else s
    out = []
    i = 0
    while i < len(s):
        if i+1 < len(s) and s[i:i+2] in AR_MAP:
            out.append(AR_MAP[s[i:i+2]]); i += 2; continue
        ch = s[i]; out.append(AR_MAP.get(ch, ch)); i += 1
    return "".join(out)

def try_register_arabic_font() -> str:
    font_path = (st.secrets.get("tac", {}) or {}).get("arabic_font_path")
    if not font_path:
        for p in ["assets/Amiri-Regular.ttf", "assets/NotoNaskhArabic-Regular.ttf", "assets/Scheherazade-Regular.ttf"]:
            if os.path.exists(p): font_path = p; break
    if font_path and os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont("ARMain", font_path))
            return "ARMain"
        except Exception:
            pass
    return "Helvetica"

def ar_shape(txt: str) -> str:
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(str(txt)))
    except Exception:
        return str(txt)

def has_arabic(txt: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" for ch in str(txt))

# =========================
# PDF: maroon circular stamp
# =========================
def draw_maroon_circle_stamp(c: canvas.Canvas, center: Tuple[float, float], radius_mm: float, date_str: str):
    r = radius_mm * mm
    cx, cy = center
    # maroon + dashed
    c.setStrokeColorRGB(0.5, 0, 0)  # maroon
    c.setFillColorRGB(0.5, 0, 0)
    c.setDash(3, 2)
    c.setLineWidth(2)
    c.circle(cx, cy, r, stroke=1, fill=0)
    c.setDash()

    c.setFillColorRGB(0.5, 0, 0)
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(cx, cy + 6*mm, "TAC")
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(cx, cy, "Together Academic Center")
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
    logo_bytes: Optional[bytes],
    text_mode: str = "latin"  # "latin" or "auto_arabic"
) -> io.BytesIO:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 20*mm

    ar_font = try_register_arabic_font()

    def prepare(txt: str) -> Tuple[str, str]:
        s = str(txt)
        if text_mode == "latin":
            return ("Helvetica", to_latin_if_arabic(s))
        if has_arabic(s):
            return (ar_font, ar_shape(s))
        return ("Helvetica", s)

    def line(txt, dy=8*mm, size=11, bold=False, italic=False, centered=False):
        nonlocal y
        f_name, use_txt = prepare(txt)
        if bold and italic: f_name = "Helvetica-BoldOblique" if f_name == "Helvetica" else f_name
        elif bold:          f_name = "Helvetica-Bold" if f_name == "Helvetica" else f_name
        elif italic:        f_name = "Helvetica-Oblique" if f_name == "Helvetica" else f_name
        c.setFont(f_name, size)
        if centered: c.drawCentredString(w/2, y, use_txt)
        else:        c.drawString(20*mm, y, use_txt)
        y -= dy

    # Header
    line("Together Academic Center (TAC) – Payment Receipt", dy=12*mm, size=16, bold=True, centered=True)
    line(f"Receipt ID: {receipt_id}", centered=True)
    line(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}", centered=True)

    # Centered logo (optional)
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
            c.setFont("Helvetica-Bold", 20); c.drawCentredString(w/2, y-10*mm, "TAC"); y -= 18*mm
    else:
        c.setFont("Helvetica-Bold", 20); c.drawCentredString(w/2, y-10*mm, "TAC"); y -= 18*mm

    # Body
    line(f"Student: {reg_row.get('Student_Name','')}")
    line(f"Course: {reg_row.get('Course','')}")
    line(f"Phone: {reg_row.get('Phone','')}")
    line(f"Registration ID: {reg_row.get('Registration_ID','')}")
    line("")
    line("Payment Details", dy=10*mm, size=12, bold=True)
    line(f"Amount Paid: {pay_amount}")       # pass with 'USD' already added by caller
    line(f"Method: {pay_method}")
    if installment_no:
        line(f"Installment Number(s): {installment_no}")
    if remaining:
        line(f"Remaining Balance: {remaining}")  # pass with 'USD' if provided
    line(f"Entered By: {entered_by or 'Admin'}")
    line("")

    # Maroon circular stamp near bottom center
    draw_maroon_circle_stamp(
        c,
        center=(w/2, 35*mm),
        radius_mm=35,
        date_str=datetime.now().strftime("%Y-%m-%d")
    )

    c.showPage(); c.save(); buf.seek(0)
    return buf

# =========================
# LOAD SPREADSHEET + WORKSHEETS
# =========================
try:
    reg_sh = open_reg_spreadsheet_once()
    if ALLOW_WS_SWITCH:
        choose_registration_worksheet_with_switch(reg_sh)
    else:
        choose_registration_worksheet_fixed(reg_sh)
    reg_ws = get_current_registration_worksheet(reg_sh)
except Exception as e:
    st.error(f"❌ Failed to open spreadsheet: {e}")
    st.stop()

def ensure_accounting_tabs_once():
    try:
        ws_master = ensure_worksheet_once(reg_sh, ACCOUNTING_MASTER_WS, ACC_MASTER_COLS)
        ws_ledger = ensure_worksheet_once(reg_sh, PAYMENTS_LEDGER_WS, ACC_LEDGER_COLS)
        return ws_master, ws_ledger
    except Exception as e:
        st.error(f"❌ Cannot ensure accounting worksheets: {e}")
        st.stop()

# =========================
# SIDEBAR NAV + receipt settings
# =========================
if role == "admin":
    page = st.sidebar.radio("القائمة", ["لوحة المشرف", "المحاسبة والمدفوعات", "التصحيحات والتعديلات", "الإيصالات / Receipts"])
else:
    page = st.sidebar.radio("القائمة", ["بيانات التسجيل"])

st.sidebar.markdown("### 🧾 Receipt Settings")
text_mode = st.sidebar.selectbox("Receipt text mode", ["English (transliteration)", "Auto-Arabic"], index=0)
TEXT_MODE_KEY = "latin" if text_mode.startswith("English") else "auto_arabic"
logo_file = st.sidebar.file_uploader("TAC logo (PNG/JPG) – optional", type=["png","jpg","jpeg"], key="logo_upl_sidebar")
if logo_file is not None:
    st.session_state["tac_logo_bytes"] = logo_file.read()

# =========================
# COMMON HELPERS
# =========================
def _to_float(x, default=0.0):
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return default

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

def derive_registration_id(df: pd.DataFrame):
    if "Registration_ID" not in df.columns:
        ts = df.get("Timestamp", pd.Series(range(len(df)))).astype(str).str.replace(r"\D", "", regex=True)
        phone_col = REG_COLUMN_MAP["Phone"]
        phone_series = df.get(phone_col, pd.Series(range(len(df)))).astype(str)
        df["Registration_ID"] = ts + "-" + phone_series.str[-4:]
    return df

def usd(x) -> str:
    return f"{_to_float(x):.2f} USD"

# =========================
# ADMIN PAGE
# =========================
if role == "admin" and page == "لوحة المشرف":
    st.subheader("👤 لوحة المشرف")

    if st.button("Load sharing info"):
        try:
            perms = reg_ws.spreadsheet.list_permissions()
            for p in perms:
                email = p.get("emailAddress", "—")
                role_perm = p.get("role", "—")
                st.write(f"📧 {email} — 🛡️ {role_perm}")
        except Exception as e:
            st.error(f"Failed to get sharing data: {e}")

    try:
        df = read_ws_df_cached(reg_ws, "reg_df", ttl_sec=180)
    except Exception as e:
        st.error(f"❌ Failed to load data: {e}")
        st.stop()

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

    st.dataframe(filtered_df if row_limit == "الكل" else filtered_df.tail(int(row_limit)))

    st.info("Use the sidebar to navigate: Payments / Corrections / Receipts.")

# =========================
# ACCOUNTING PAGE (payments)
# =========================
if role == "admin" and page == "المحاسبة والمدفوعات":
    st.subheader("💳 المحاسبة والمدفوعات")

    ws_master, ws_ledger = ensure_accounting_tabs_once()

    reg_df = derive_registration_id(read_ws_df_cached(reg_ws, "reg_df", ttl_sec=180))
    master_df = read_ws_df_cached(ws_master, "master_df", ttl_sec=60)
    ledger_df = read_ws_df_cached(ws_ledger, "ledger_df", ttl_sec=60)
    ledger_df = normalize_ledger_columns(ledger_df)

    if reg_df.empty:
        st.warning("لا توجد تسجيلات حالياً."); st.stop()

    name_col = REG_COLUMN_MAP["Student_Name"]; course_col = REG_COLUMN_MAP["Course"]
    safe_name = reg_df.get(name_col, pd.Series([""]*len(reg_df))).astype(str)
    safe_course = reg_df.get(course_col, pd.Series([""]*len(reg_df))).astype(str)

    reg_df["_label"] = (reg_df.get("Registration_ID", pd.Series([""]*len(reg_df))).astype(str) + " — " +
                        safe_name + " | " + safe_course)
    selected_label = st.selectbox("اختر تسجيلًا", sorted(reg_df["_label"].tolist()))
    sel_row = reg_df[reg_df["_label"] == selected_label].iloc[0].to_dict()

    def get_val(src: dict, logical_key: str, default: str = "") -> str:
        actual = REG_COLUMN_MAP.get(logical_key, logical_key)
        return str(src.get(actual, default)).strip()

    reg_row = {
        "Registration_ID": sel_row.get("Registration_ID", ""),
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

    existing = None
    if not master_df.empty and "Registration_ID" in master_df.columns:
        m = master_df[master_df["Registration_ID"] == reg_row["Registration_ID"]]
        if not m.empty: existing = m.iloc[0].to_dict()

    paid_to_date = _to_float(existing.get("Paid_To_Date")) if existing else 0.0
    existing_total = _to_float(existing.get("Total_Fee")) if existing else 0.0
    effective_total_fee = existing_total if existing_total > 0 else (reg_row["Total_Fee"] if reg_row["Total_Fee"] > 0 else FULL_PRICE)
    remaining = max(effective_total_fee - paid_to_date, 0.0)

    already_paid = _parse_paid_installments_from_ledger(ledger_df, reg_row["Registration_ID"]) if not ledger_df.empty else set()

    c1, c2, c3 = st.columns(3)
    with c1: st.metric("إجمالي الرسوم (فعّال)", usd(effective_total_fee))
    with c2: st.metric("المدفوع حتى الآن", usd(paid_to_date))
    with c3: st.metric("المتبقي", usd(remaining))

    st.divider()
    st.subheader("تسجيل عملية دفع")
    admin_name = st.text_input("مدخل البيانات (المشرف)", value="")

    # Payment mode & amount
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
        st.info(f"الأقساط المختارة: {', '.join(map(str, inst_selected)) if inst_selected else 'لا شيء'} — المبلغ = {usd(pay_amount)}")
        if effective_total_fee > 0 and (paid_to_date + pay_amount) - effective_total_fee > 1e-6:
            st.error("المبلغ الحالي سيتجاوز إجمالي الرسوم. قلّل عدد الأقساط المختارة.")
            inst_selected = []; pay_amount = 0.0

    # Payment method (English only for saving)
    pay_method = st.selectbox("Payment method", METHOD_CHOICES, index=0)
    pay_note = st.text_input("Note (optional)")

    btn_disabled = (pay_amount <= 0) or (pay_mode.startswith("أقساط") and len(inst_selected) == 0)

    if st.button("Save & generate receipt", type="primary", disabled=btn_disabled):
        try:
            receipt_id = f"RCPT-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
            inst_field = "" if pay_mode.startswith("مكتمل") else ",".join(map(str, inst_selected))

            # Append ledger row (numeric amount in sheet)
            ws_ledger.append_row([
                receipt_id,
                reg_row["Registration_ID"],
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                f"{pay_amount:.2f}",
                pay_method,      # English
                pay_note,
                admin_name,
                inst_field
            ])

            # Update master (upsert)
            new_paid = paid_to_date + pay_amount
            new_remaining = max(effective_total_fee - new_paid, 0.0)
            new_status = "Completed" if math.isclose(new_remaining, 0.0, abs_tol=1e-6) else "Installments"

            md = read_ws_df_cached(ws_master, "master_df", ttl_sec=0)  # fresh
            idx = md.index[md["Registration_ID"] == reg_row["Registration_ID"]].tolist() if not md.empty else []
            row_vals = [
                reg_row["Registration_ID"], reg_row["Student_Name"], reg_row["Course"], reg_row["Phone"],
                ("Full" if pay_mode.startswith("مكتمل") else "Installments"),
                str(MAX_INSTALLMENTS), f"{effective_total_fee:.2f}",
                f"{new_paid:.2f}", f"{new_remaining:.2f}", new_status,
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                receipt_id
            ]
            last_col = chr(64 + len(ACC_MASTER_COLS))
            if idx:
                rownum = idx[0] + 2
                ws_master.update(f"A{rownum}:{last_col}{rownum}", [row_vals])
            else:
                ws_master.append_row(row_vals)

            # Reflect payment method into Registration sheet ('طريقة السداد') if column exists
            try:
                if "طريقة السداد" in reg_df.columns:
                    reg_rownum = get_rownum_for_selected_reg(reg_df, selected_label)
                    if reg_rownum > 1:
                        reg_col_idx = reg_df.columns.get_loc("طريقة السداد") + 1  # 1-based
                        reg_ws.update_cell(reg_rownum, reg_col_idx, pay_method)
                        reg_df.loc[reg_rownum - 2, "طريقة السداد"] = pay_method
                        _cache_set("reg_df", reg_df)
            except Exception:
                pass  # non-fatal

            # refresh caches quickly
            _cache_set("master_df", read_ws_df_cached(ws_master, "master_df", ttl_sec=0))
            _cache_set("ledger_df", read_ws_df_cached(ws_ledger, "ledger_df", ttl_sec=0))

            # Prepare printable row (per receipt text mode)
            logo_bytes = st.session_state.get("tac_logo_bytes")
            printable = {
                "Registration_ID": reg_row["Registration_ID"],
                "Student_Name": to_latin_if_arabic(reg_row["Student_Name"]) if TEXT_MODE_KEY=="latin" else reg_row["Student_Name"],
                "Course": to_latin_if_arabic(reg_row["Course"]) if TEXT_MODE_KEY=="latin" else reg_row["Course"],
                "Phone": to_latin_if_arabic(reg_row["Phone"]) if TEXT_MODE_KEY=="latin" else reg_row["Phone"],
            }

            pdf_buf = generate_receipt_pdf(
                receipt_id=receipt_id,
                reg_row=printable,
                pay_amount=f"{pay_amount:.2f} USD",
                pay_method=pay_method,
                remaining=f"{new_remaining:.2f} USD",
                installment_no=(inst_field if inst_field else None),
                entered_by=to_latin_if_arabic(admin_name) if TEXT_MODE_KEY=="latin" else admin_name,
                logo_bytes=logo_bytes,
                text_mode=TEXT_MODE_KEY
            )

            st.success(f"تم تسجيل الدفع. تم إنشاء الإيصال {receipt_id}.")
            st.download_button("📄 Download receipt (PDF)", data=pdf_buf, file_name=f"{receipt_id}.pdf", mime="application/pdf")
            st.rerun()
        except Exception as e:
            st.error("Saving failed.")
            st.exception(e)

    # Past receipts for this student (re-download)
    st.divider()
    st.markdown("### 🧾 إيصالات سابقة لهذا الطالب")
    sub = ledger_df.copy()
    if not sub.empty:
        sub = sub[sub.get("Registration_ID","") == reg_row["Registration_ID"]]
        if not sub.empty:
            try:
                sub["_dt"] = pd.to_datetime(sub["Payment_Date"], errors="coerce")
                sub = sub.sort_values("_dt", ascending=False, na_position="last")
            except Exception:
                pass
            for _, r in sub.iterrows():
                rid = r.get("Receipt_ID","")
                amt = _to_float(r.get("Amount","0"))
                dt  = r.get("Payment_Date","")
                meth= normalize_method_value(r.get("Method",""))
                inst= r.get("Installment_Number","")

                colA, colB = st.columns([3,1])
                with colA:
                    st.write(f"**{rid}** — {dt} — {usd(amt)} — {meth} — أقساط: {inst or '—'}")
                with colB:
                    logo_bytes = st.session_state.get("tac_logo_bytes")
                    printable = {
                        "Registration_ID": reg_row["Registration_ID"],
                        "Student_Name": to_latin_if_arabic(reg_row["Student_Name"]) if TEXT_MODE_KEY=="latin" else reg_row["Student_Name"],
                        "Course": to_latin_if_arabic(reg_row["Course"]) if TEXT_MODE_KEY=="latin" else reg_row["Course"],
                        "Phone": to_latin_if_arabic(reg_row["Phone"]) if TEXT_MODE_KEY=="latin" else reg_row["Phone"],
                    }
                    pdf_buf = generate_receipt_pdf(
                        receipt_id=rid,
                        reg_row=printable,
                        pay_amount=f"{amt:.2f} USD",
                        pay_method=meth,
                        remaining="",  # historical single receipt; omit remaining here
                        installment_no=(inst if inst else None),
                        entered_by="Admin",
                        logo_bytes=logo_bytes,
                        text_mode=TEXT_MODE_KEY
                    )
                    st.download_button("Download", data=pdf_buf, file_name=f"{rid}.pdf", mime="application/pdf", key=f"dl_{rid}")

# =========================
# CORRECTIONS PAGE (edit/delete receipts)
# =========================
def find_ledger_rownum_by_receipt(ws_ledger, df_ledger: pd.DataFrame, receipt_id: str) -> Optional[int]:
    if df_ledger.empty or "Receipt_ID" not in df_ledger.columns:
        return None
    idx = df_ledger.index[df_ledger["Receipt_ID"] == receipt_id].tolist()
    if not idx:
        return None
    return idx[0] + 2  # +1 header +1 to convert

def recalc_master_for_registration(ws_master, ws_ledger, reg_id: str, reg_df: pd.DataFrame):
    ledger_df = read_ws_df_cached(ws_ledger, "ledger_df", ttl_sec=0)
    ledger_df = normalize_ledger_columns(ledger_df)
    if ledger_df.empty:
        total_paid = 0.0
    else:
        sub = ledger_df[ledger_df.get("Registration_ID", "") == reg_id]
        total_paid = sum(_to_float(a) for a in sub.get("Amount", []))

    master_df = read_ws_df_cached(ws_master, "master_df", ttl_sec=0)
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
            name = course = phone = ""; existing_total = 0.0

    effective_total_fee = _to_float(existing.get("Total_Fee")) if existing else 0.0
    if effective_total_fee <= 0:
        effective_total_fee = existing_total if existing_total > 0 else FULL_PRICE

    remaining = max(effective_total_fee - total_paid, 0.0)
    status = "Completed" if math.isclose(remaining, 0.0, abs_tol=1e-6) else ("Unpaid" if total_paid == 0 else "Installments")

    row_vals = [
        reg_id, name, course, phone, existing.get("PaymentPlan","Installments") if existing else "Installments",
        str(MAX_INSTALLMENTS), f"{effective_total_fee:.2f}",
        f"{total_paid:.2f}", f"{remaining:.2f}", status,
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        existing.get("LastReceiptID","") if existing else ""
    ]

    if not master_df.empty and "Registration_ID" in master_df.columns:
        idx = master_df.index[master_df["Registration_ID"] == reg_id].tolist()
    else:
        idx = []
    last_col = chr(64 + len(ACC_MASTER_COLS))
    if idx:
        rownum = idx[0] + 2
        ws_master.update(f"A{rownum}:{last_col}{rownum}", [row_vals])
    else:
        ws_master.append_row(row_vals)

    _cache_set("master_df", read_ws_df_cached(ws_master, "master_df", ttl_sec=0))_
