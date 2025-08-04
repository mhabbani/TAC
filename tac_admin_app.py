
import streamlit as st
import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials

# Google Sheets setup
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
    "admin": {"role": "admin", "password": "adminpass"},
    "salma": {"role": "power", "password": "salma123"},
    "sara": {"role": "power", "password": "sara123"},
    "amal": {"role": "power", "password": "amal123"}
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

# Session state for login
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

try:
    df = pd.DataFrame(sheet.get_all_records())
except Exception as e:
    st.error(f"❌ فشل في تحميل البيانات: {e}")
    st.stop()

if role == "admin":
    st.subheader("👤 لوحة المشرف")

    with st.expander("🔗 مشاركة Google Sheet"):
        try:
            perms = sheet.spreadsheet.list_permissions()
            for p in perms:
                email = p.get("emailAddress", "—")
                role_perm = p.get("role", "—")
                st.write(f"📧 {email} — 🛡️ {role_perm}")
        except Exception as e:
            st.error("لم يتم الحصول على بيانات المشاركة")

    st.subheader("📋 معاينة نموذج التسجيل")
    col1, col2, col3 = st.columns(3)
    row_limit = col1.selectbox("عدد السجلات المعروضة", ["الكل", 5, 10, 20, 50])
    search_name = col2.text_input("🔍 البحث بالاسم أو الكورس")
    age_filter = col3.selectbox("📊 تصفية حسب العمر", ["الكل"] + sorted(df["العمر"].dropna().unique().astype(str).tolist()))
    country_filter = st.selectbox("🌍 تصفية حسب الدولة", ["الكل"] + sorted(df["العنوان"].dropna().apply(lambda x: x.split("-")[0].strip()).unique().tolist()))

    filtered_df = df.copy()
    if search_name:
        filtered_df = filtered_df[
            filtered_df["الاسم"].str.contains(search_name, case=False, na=False) |
            filtered_df["الكورس"].str.contains(search_name, case=False, na=False)
        ]
    if age_filter != "الكل":
        filtered_df = filtered_df[filtered_df["العمر"] == int(age_filter)]
    if country_filter != "الكل":
        filtered_df = filtered_df[filtered_df["العنوان"].str.startswith(country_filter)]

    if row_limit == "الكل":
        st.dataframe(filtered_df)
    else:
        st.dataframe(filtered_df.tail(int(row_limit)))

    # TEXTUAL ANALYTICS
    st.subheader("📊 تحليلات نصية للتسجيل")
    chart_type = st.selectbox("اختر نوع التحليل", [
        "عدد المسجلين لكل كورس",
        "نسبة صلة القرابة",
        "تحليل الأعمار",
        "الإخوة (نفس رقم ولي الأمر)",
        "المسجلين في أكثر من دورة"
    ])

    total = len(df)

    if chart_type == "عدد المسجلين لكل كورس":
        st.markdown("### 📘 توزيع الكورسات:")
        for course, count in df["الكورس"].value_counts().items():
            percent = round((count / total) * 100, 2)
            st.markdown(f"- **{course}**: {count} طالب ({percent}%)")

    elif chart_type == "نسبة صلة القرابة":
        st.markdown("### 🧑‍🤝‍🧑 صلات القرابة:")
        for rel, count in df["صلة القرابة"].value_counts().items():
            percent = round((count / total) * 100, 2)
            st.markdown(f"- **{rel}**: {count} ({percent}%)")

    elif chart_type == "تحليل الأعمار":
        st.markdown("### 🎂 توزيع الأعمار:")
        for age, count in df["العمر"].value_counts().sort_index().items():
            percent = round((count / total) * 100, 2)
            st.markdown(f"- **{age} سنة**: {count} ({percent}%)")

    elif chart_type == "الإخوة (نفس رقم ولي الأمر)":
        siblings = df.groupby("رقم اتصال ولي الأمر").filter(lambda x: len(x) > 1)
        st.dataframe(siblings[["الاسم", "رقم اتصال ولي الأمر"]])
        st.info(f"👨‍👩‍👧‍👦 عدد الأسر التي سجلت أكثر من طفل: {siblings['رقم اتصال ولي الأمر'].nunique()}")

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
