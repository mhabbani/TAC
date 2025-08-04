
import streamlit as st
import gspread
import pandas as pd
import plotly.express as px
from oauth2client.service_account import ServiceAccountCredentials

# Google Sheets setup
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

# Static users
USERS = {
    "admin": {"role": "admin", "password": "adminpass"},
    "osama": {"role": "power", "password": "osama123"},
    "nour": {"role": "power", "password": "nour123"},
    "reem": {"role": "power", "password": "reem123"}
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

username = st.text_input("اسم المستخدم")
password = st.text_input("كلمة المرور", type="password")

if st.button("تسجيل الدخول"):
    if username in USERS and USERS[username]["password"] == password:
        st.success(f"مرحبًا {username} 👋")

        role = USERS[username]["role"]

        if role == "admin":
            st.subheader("👤 لوحة المشرف")

            # عرض المشاركة الحالية للشيت
            st.markdown("### 🔗 حالة المشاركة في Google Sheet")
            try:
                perms = sheet.spreadsheet.list_permissions()
                for p in perms:
                    email = p.get("emailAddress", "—")
                    role = p.get("role", "—")
                    st.write(f"📧 {email} — 🛡️ {role}")
            except Exception as e:
                st.error("لم يتم الحصول على بيانات المشاركة")

            # التحليلات والرسوم البيانية
            st.subheader("📊 تحليلات التسجيل")
            try:
                data = sheet.get_all_records()
                df = pd.DataFrame(data)

                chart_type = st.selectbox("اختر نوع المخطط", ["عدد المسجلين لكل كورس", "عدد حسب صلة القرابة", "تحليل الأعمار", "تكرار رقم ولي الأمر", "الطلاب المسجلون بأكثر من دورة"])

                if chart_type == "عدد المسجلين لكل كورس":
                    fig = px.bar(df["الكورس"].value_counts().reset_index(), x="index", y="الكورس", labels={"index": "اسم الكورس", "الكورس": "عدد المسجلين"})
                    st.plotly_chart(fig)

                elif chart_type == "عدد حسب صلة القرابة":
                    fig = px.pie(df, names="صلة القرابة", title="نسب الأقارب المسجلين")
                    st.plotly_chart(fig)

                elif chart_type == "تحليل الأعمار":
                    fig = px.histogram(df, x="العمر", nbins=10)
                    st.plotly_chart(fig)

                elif chart_type == "تكرار رقم ولي الأمر":
                    siblings = df.groupby("رقم اتصال ولي الأمر").filter(lambda x: len(x) > 1)
                    st.write("👨‍👩‍👧‍👦 المسجلين بنفس رقم ولي الأمر (إخوة):", siblings[["الاسم", "رقم اتصال ولي الأمر"]])
                    st.write(f"🔢 عدد الأسر التي سجلت أكثر من طفل: {siblings['رقم اتصال ولي الأمر'].nunique()}")

                elif chart_type == "الطلاب المسجلون بأكثر من دورة":
                    duplicates = df.groupby("الاسم").filter(lambda x: len(x) > 1)
                    st.write("👥 الطلاب المسجلون في أكثر من دورة:", duplicates[["الاسم", "الكورس"]])
                    st.write(f"🔁 عددهم: {duplicates['الاسم'].nunique()}")

                # تحليل ميول التسجيل العائلي
                st.markdown("### 💡 تحليل الشرائح المهتمة بتسجيل الأقارب")
                relation_counts = df["صلة القرابة"].value_counts()
                st.write("النسب الأكثر تسجيلًا لأقارب:", relation_counts)

            except Exception as e:
                st.error("حدث خطأ في التحليلات")

            # رابط صفحة جديدة (Placeholder)
            st.markdown("---")
            st.markdown("### 💰 الانتقال إلى صفحة مراقبة الحسابات والمدفوعات")
            if st.button("🔗 الانتقال إلى صفحة الدفع"):
                st.success("🔜 سيتم التوجيه إلى صفحة جديدة (سيتم تطويرها لاحقًا)")

        elif role == "power":
            st.subheader("📊 لوحة المستخدم المتقدم")
            try:
                data = sheet.get_all_records()
                df = pd.DataFrame(data)
                st.dataframe(df)
                st.download_button("📥 تحميل البيانات", data=df.to_csv(index=False), file_name="TAC_Registrations.csv")
            except Exception as e:
                st.error(f"حدث خطأ أثناء تحميل البيانات: {e}")
    else:
        st.error("❌ اسم المستخدم أو كلمة المرور غير صحيحة")
