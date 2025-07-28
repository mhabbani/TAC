
import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
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

st.set_page_config(page_title="استمارة التسجيل - Together Academic Center", layout="centered")

st.markdown("""
    <style>
    body, .css-18e3th9, .css-1d391kg {
        direction: rtl;
        text-align: right;
        font-family: 'Baloo Bhaijaan', sans-serif;
        background-color: #f0f4ff;
    }
    </style>
""", unsafe_allow_html=True)

st.image("logo_tac.png", width=250)
st.title("📋 استمارة التسجيل")
st.markdown("يرجى تعبئة البيانات التالية بدقة")

courses = {
    "Jolly Phonics – Beginners": {
        "min_age": 7,
        "max_age": 12,
        "الفئة المستهدفة": "أطفال 7-12 سنة (غير ناطقين بالإنجليزية)",
        "الهدف": "تعليم أصوات الحروف وتكوين كلمات (Reading Skills & Phonetics)",
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
    },
    "English Club": {
        "min_age": 12,
        "max_age": 16,
        "الفئة المستهدفة": "أطفال 12-16",
        "الهدف": "Improving Engish skills",
        "المدة": "8 أسابيع - 14 حصة (مرتان أسبوعيًا – 30 دقيقة)",
        "المحتوى": ["Listening", "Reading", "Speaking"],
        "السعر": "90 دولار للكورس الواحد",
        "خطة الدفع": "دفعتين × 45 دولار أو 15 دولار أسبوعيًا",
        "ضمان الاسترداد": "استرداد كامل بعد أول حصة إذا لم ينسجم الطفل، واسترداد 50% حتى الحصة الرابعة"
    }
}

col1, col2 = st.columns(2)
course_options = ["-- اختر الكورس --"] + list(courses.keys())
selected_course = col1.selectbox("اختر الكورس", course_options)

if selected_course != "-- اختر الكورس --":
    course_info = courses[selected_course]
    min_age = course_info["min_age"]
    max_age = course_info["max_age"]

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

    name = col1.text_input("الاسم الكامل")
    age = col2.number_input("العمر", min_value=min_age, max_value=max_age)
    school = col1.text_input("المدرسة")
    level = col2.selectbox("المستوى الدراسي", ["ابتدائي", "متوسط", "ثانوي", "جامعي"])
    address = col2.text_area("العنوان")

    phone = col1.text_input("رقم الاتصال", placeholder="مثال: 0501234567")
    whatsapp = col2.text_input("رقم الواتساب", placeholder="مثال: 0501234567")
    email = col1.text_input("البريد الإلكتروني", placeholder="example@email.com")

    guardian_name = col2.text_input("اسم ولي الأمر")
    relation = col1.selectbox("صلة القرابة", ["الأب", "الأم", "الأخ", "الأخت", "أخرى"])
    guardian_phone = col2.text_input("رقم اتصال ولي الأمر", placeholder="مثال: 0501234567")
    guardian_whatsapp = col1.text_input("رقم واتساب ولي الأمر", placeholder="مثال: 0501234567")
    guardian_email = col2.text_input("إيميل ولي الأمر", placeholder="example@email.com")

    payment_method = st.radio("طريقة الدفع", ["دفعة كاملة", "أقساط"])

    if st.button("إرسال التسجيل"):
        errors = []

        if "@" not in email or "." not in email:
            errors.append("❌ يرجى إدخال بريد إلكتروني صالح")
        if not phone.isdigit() or not (9 <= len(phone) <= 12):
            errors.append("❌ رقم الاتصال غير صحيح")
        if not whatsapp.isdigit() or not (9 <= len(whatsapp) <= 12):
            errors.append("❌ رقم الواتساب غير صحيح")
        if "@" not in guardian_email or "." not in guardian_email:
            errors.append("❌ بريد ولي الأمر غير صالح")
        if age < 15 and level == "جامعي":
            errors.append("⚠️ لا يمكن اختيار 'جامعي' إذا كان العمر أقل من 15 سنة.")

        if errors:
            for e in errors:
                st.markdown(f"<span style='color:red'>{e}</span>", unsafe_allow_html=True)
        else:
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
else:
    st.info("يرجى اختيار الكورس لعرض التفاصيل واستكمال التسجيل.")
