import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# إعداد الاتصال بـ Google Sheets
#
# In Streamlit Cloud, the Google service account credentials are provided via
# `st.secrets`. To avoid committing the private key to the repository, the
# JSON contents of the service account key should be stored under
# `gcp_service_account` in your app's Secrets (Streamlit → Manage App → Secrets).
# We then construct the credentials using `from_json_keyfile_dict`.
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    # Load the service account credentials from Streamlit secrets.
    # The credentials should be stored under the key "gcp_service_account" in
    # your Streamlit secrets. However, if the user mistakenly named the key
    # "google_service_account", we try that as a fallback. When neither key
    # is present (such as during local development), we'll fall back to reading
    # from a local `credentials.json` file below.
    creds_section = st.secrets.get("gcp_service_account") or st.secrets.get("google_service_account")
    if creds_section:
        # Convert to a mutable dictionary from the secrets section
        creds_dict = dict(creds_section)
        # Some users may store the private key using literal "\n" sequences to denote
        # newlines in their Streamlit secrets file. If so, replace the escaped
        # newline characters with actual newline characters. This helps avoid
        # `ValueError: No key could be detected` when oauth2client tries to
        # parse the private key.
        if isinstance(creds_dict.get("private_key"), str) and "\n" in creds_dict["private_key"]:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        raise KeyError("No Google service account credentials found in st.secrets")
except Exception:
    # Fallback for local development: load credentials from a local JSON file.
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)

client = gspread.authorize(creds)
sheet = client.open("TAC-Registeration").sheet1

st.set_page_config(page_title="استمارة التسجيل - Together Academic Center", layout="centered")

# تنسيق RTL
st.markdown("""
    <style>
    body, .css-18e3th9, .css-1d391kg {
        direction: rtl;
        text-align: right;
        font-family: 'Baloo Bhaijaan', sans-serif;
        /* Apply a light complementary background color that pairs with the TAC logo */
        background-color: #f0f4ff;
    }
    </style>
""", unsafe_allow_html=True)

st.image("logo_tac.png", width=250)
st.title("📋 استمارة التسجيل")
st.markdown("يرجى تعبئة البيانات التالية بدقة")

# تعريف الكورسات
courses = {
    "Jolly Phonics – Beginners": {
        "min_age": 7,
        "max_age": 12,
        "الفئة المستهدفة": "أطفال 7-12 سنة (غير ناطقين بالإنجليزية)",
        "الهدف": "تعليم أصوات الحروف وتكوين كلمات (Reading Skills)",
        "المدة": "8 أسابيع - 14 حصة (مرتان أسبوعيًا – 40 دقيقة)",
        "المحتوى": [
            "الأصوات الأساسية في اللغة الإنجليزية",
            "الأغاني وقراءة القصص التفاعلية",
            "تكوين وقراءة الكلمات",
            "تمارين صوتية وبصرية"
        ],
        "السعر": "90 دولار",
        "خطة الدفع": "دفعتين × 45 دولار أو 15 دولار أسبوعيًا",
        "ضمان الاسترداد": "كامل بعد أول حصة أو 50% حتى الحصة الرابعة"
    }
}

with st.form("registration_form"):
    col1, col2 = st.columns(2)

    selected_course = col1.selectbox("اختر الكورس", list(courses.keys()))
    course_info = courses[selected_course]
    min_age = course_info["min_age"]
    max_age = course_info["max_age"]

    name = col1.text_input("الاسم الكامل")
    age = col2.number_input("العمر", min_value=min_age, max_value=max_age)
    school = col1.text_input("المدرسة")
    level = col2.selectbox("المستوى الدراسي", ["ابتدائي", "متوسط", "ثانوي", "جامعي"])
    address = col2.text_area("العنوان")

    st.markdown("### تفاصيل الكورس المختار")
    st.markdown(f"**الفئة المستهدفة:** {course_info['الفئة المستهدفة']}")
    st.markdown(f"**الهدف:** {course_info['الهدف']}")
    st.markdown(f"**المدة:** {course_info['المدة']}")
    st.markdown("**المحتوى:**")
    for item in course_info["المحتوى"]:
        st.markdown(f"- {item}")
    st.markdown(f"**السعر:** {course_info['السعر']}")
    st.markdown(f"**خطة الدفع:** {course_info['خطة الدفع']}")
    st.markdown(f"**ضمان الاسترداد:** {course_info['ضمان الاسترداد']}")

    phone = col1.text_input("رقم الاتصال")
    whatsapp = col2.text_input("رقم الواتساب")
    email = col1.text_input("البريد الإلكتروني")

    guardian_name = col2.text_input("اسم ولي الأمر")
    relation = col1.selectbox("صلة القرابة", ["الأب", "الأم", "الأخ", "الأخت", "أخرى"])
    guardian_phone = col2.text_input("رقم اتصال ولي الأمر")
    guardian_whatsapp = col1.text_input("رقم واتساب ولي الأمر")
    guardian_email = col2.text_input("إيميل ولي الأمر")

    payment_method = st.radio("طريقة الدفع", ["دفعة كاملة", "أقساط"])

    submitted = st.form_submit_button("إرسال التسجيل")

    if submitted:
        row = [
            name, age, school, level, selected_course, payment_method,
            address, phone, whatsapp, email,
            guardian_name, relation, guardian_phone, guardian_whatsapp,
            guardian_email, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]

        headers = [
            "الاسم", "العمر", "المدرسة", "المستوى", "الكورس", "طريقة الدفع",
            "العنوان", "رقم الاتصال", "الواتساب", "الإيميل",
            "اسم ولي الأمر", "صلة القرابة", "رقم اتصال ولي الأمر",
            "واتساب ولي الأمر", "إيميل ولي الأمر", "تاريخ التسجيل"
        ]

        try:
            if len(sheet.get_all_values()) == 0:
                sheet.append_row(headers)
            sheet.append_row(row)
            st.success("✅ تم إرسال التسجيل بنجاح! تم حفظه في Google Sheets.")
        except Exception as e:
            st.error(f"❌ حدث خطأ أثناء الحفظ: {e}")
