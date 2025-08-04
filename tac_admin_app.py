
import streamlit as st
import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials

# إعداد الاتصال بـ Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds_section = st.secrets.get("gcp_service_account") or st.secrets.get("google_service_account")
    if creds_section:
        creds_dict = dict(creds_section)
        if isinstance(creds_dict.get("private_key"), str):
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n").replace("\n", "\n")
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        raise KeyError("No credentials found in secrets")
except Exception:
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)

client = gspread.authorize(creds)
sheet = client.open("TAC-Registeration").sheet1

# المستخدمون الثابتون
USERS = {
    "admin": {"role": "admin", "password": "adminpass"},
    "osama": {"role": "power", "password": "osama123"},
    "nour": {"role": "power", "password": "nour123"},
    "reem": {"role": "power", "password": "reem123"}
}

st.set_page_config(page_title="لوحة تحكم الإدارة - TAC Admin", layout="centered")

st.markdown("""
    <style>
    body, .css-18e3th9, .css-1d391kg {
        direction: rtl;
        text-align: right;
        font-family: 'Baloo Bhaijaan', sans-serif;
    }
    </style>
""", unsafe_allow_html=True)

st.title("🛡️ TAC Admin Panel")

username = st.text_input("اسم المستخدم")
password = st.text_input("كلمة المرور", type="password")

if st.button("تسجيل الدخول"):
    if username in USERS and USERS[username]["password"] == password:
        st.success(f"مرحبًا {username} 👋")

        role = USERS[username]["role"]

        if role == "admin":
            st.subheader("👤 لوحة المشرف")
            st.markdown("- ✅ إدارة المستخدمين (قريبًا)")
            st.markdown("- 🗂️ تعديل مشاركة Google Sheet")
            st.markdown("- ⚙️ إضافة/إزالة صلاحيات")
        elif role == "power":
            st.subheader("📊 لوحة المستخدم المتقدم")
            st.markdown("يمكنك عرض بيانات التسجيل أدناه 👇")

            try:
                data = sheet.get_all_records()
                df = pd.DataFrame(data)
                st.dataframe(df)
                st.download_button("📥 تحميل البيانات", data=df.to_csv(index=False), file_name="TAC_Registrations.csv")
            except Exception as e:
                st.error(f"حدث خطأ أثناء تحميل البيانات: {e}")
    else:
        st.error("❌ اسم المستخدم أو كلمة المرور غير صحيحة")
