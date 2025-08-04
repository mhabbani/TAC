
# Fixes:
# 1. Persist login using st.session_state
# 2. Display chart only after login
# 3. Avoid re-triggering login form on dropdown interaction

import streamlit as st
import gspread
import pandas as pd
import plotly.express as px
from oauth2client.service_account import ServiceAccountCredentials

# إعداد الاتصال بـ Google Sheets
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
sheet = client.open("TAC-Registeration").sheet1

USERS = {
    "admin": {"role": "admin", "password": "Asnf_129"},
    "Salma": {"role": "power", "password": "Salma1234"},
    "Sara": {"role": "power", "password": "Sara1234"},
    "Amal": {"role": "power", "password": "Amal1234"}
}

st.set_page_config(page_title="TAC Admin Panel", layout="wide")

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

# Initialize session
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""

# Login form
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

# After login
role = USERS[st.session_state.username]["role"]
st.success(f"مرحبًا {st.session_state.username} 👋 - الصلاحية: {role}")

# Fetch data
try:
    df = pd.DataFrame(sheet.get_all_records())
except Exception as e:
    st.error(f"❌ فشل في تحميل البيانات: {e}")
    st.stop()

if role == "admin":
    st.subheader("👤 لوحة المشرف")

    # Google Sheet sharing info
    with st.expander("🔗 مشاركة Google Sheet"):
        try:
            perms = sheet.spreadsheet.list_permissions()
            for p in perms:
                email = p.get("emailAddress", "—")
                role_perm = p.get("role", "—")
                st.write(f"📧 {email} — 🛡️ {role_perm}")
        except Exception as e:
            st.error("لم يتم الحصول على بيانات المشاركة")

    # Analytics section
    st.subheader("📊 تحليلات التسجيل")
    chart_type = st.selectbox("اختر نوع التحليل", [
        "عدد المسجلين لكل كورس",
        "نسبة صلة القرابة",
        "تحليل الأعمار",
        "الإخوة (نفس رقم ولي الأمر)",
        "المسجلين في أكثر من دورة"
    ])

    if chart_type == "عدد المسجلين لكل كورس":
        fig = px.bar(df["الكورس"].value_counts().reset_index(), x="index", y="الكورس", labels={"index": "اسم الكورس", "الكورس": "عدد"})
        st.plotly_chart(fig)

    elif chart_type == "نسبة صلة القرابة":
        fig = px.pie(df, names="صلة القرابة")
        st.plotly_chart(fig)

    elif chart_type == "تحليل الأعمار":
        fig = px.histogram(df, x="العمر", nbins=10)
        st.plotly_chart(fig)

    elif chart_type == "الإخوة (نفس رقم ولي الأمر)":
        siblings = df.groupby("رقم اتصال ولي الأمر").filter(lambda x: len(x) > 1)
        st.dataframe(siblings[["الاسم", "رقم اتصال ولي الأمر"]])
        st.info(f"👨‍👩‍👧‍👦 الأسر التي سجلت أكثر من طفل: {siblings['رقم اتصال ولي الأمر'].nunique()}")

    elif chart_type == "المسجلين في أكثر من دورة":
        multi = df.groupby("الاسم").filter(lambda x: len(x) > 1)
        st.dataframe(multi[["الاسم", "الكورس"]])
        st.info(f"🔁 عدد الطلاب المسجلين في أكثر من دورة: {multi['الاسم'].nunique()}")

    st.markdown("### 💰 الانتقال إلى صفحة مراقبة الحسابات والمدفوعات")
    if st.button("🔗 المتابعة إلى صفحة الدفع"):
        st.warning("🚧 هذه الصفحة تحت التطوير")

elif role == "power":
    st.subheader("📊 بيانات التسجيل")
    st.dataframe(df)
    st.download_button("📥 تحميل البيانات", data=df.to_csv(index=False), file_name="TAC_Registrations.csv")
