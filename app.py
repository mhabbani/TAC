import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- Fixed users and roles ---
USERS = {
    "admin": {"password": "Asnf_129", "role": "admin"},
    "power1": {"password": "pass123", "role": "power"},
    "power2": {"password": "pass456", "role": "power"},
    "power3": {"password": "pass789", "role": "power"},
}

# --- Connect to Google Sheets ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds_section = st.secrets.get("gcp_service_account") or st.secrets.get("google_service_account")
    if creds_section:
        creds_dict = dict(creds_section)
        if isinstance(creds_dict.get("private_key"), str) and "\\n" in creds_dict["private_key"]:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n").replace("\n", "\n")
        creds_dict["private_key"] = creds_dict["private_key"].replace("\n", "
")
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        raise KeyError("No Google service account credentials found in st.secrets")
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

# --- Auth ---
if "user" not in st.session_state:
    with st.form("login_form"):
        username = st.text_input("اسم المستخدم")
        password = st.text_input("كلمة المرور", type="password")
        submitted = st.form_submit_button("تسجيل الدخول")
        if submitted:
            user = USERS.get(username)
            if user and user["password"] == password:
                st.session_state["user"] = username
                st.session_state["role"] = user["role"]
                st.success("✅ تم تسجيل الدخول بنجاح")
                st.rerun()
            else:
                st.error("❌ اسم المستخدم أو كلمة المرور غير صحيحة")
    st.stop()

# --- Admin Panel ---
def show_admin_panel():
    st.header("لوحة تحكم المدير")
    st.markdown("✉️ مشاركة Google Sheet مع مستخدم جديد")
    new_email = st.text_input("إيميل المستخدم")
    if st.button("مشاركة"):
        try:
            sheet.share(new_email, perm_type='user', role='reader', notify=True)
            st.success("✅ تمت المشاركة بنجاح")
        except Exception as e:
            st.error(f"❌ خطأ: {e}")

# --- Power User Panel ---
def show_power_panel():
    st.header("لوحة المستخدم المفوض")
    try:
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        st.dataframe(df)
        st.download_button("📥 تحميل كملف CSV", df.to_csv(index=False), "tac_data.csv", "text/csv")
    except Exception as e:
        st.error(f"حدث خطأ أثناء جلب البيانات: {e}")

# --- Registration Panel ---
def show_registration_panel():
    st.image("logo_tac.png", width=250)
    st.title("📋 استمارة التسجيل")
    st.markdown("يرجى تعبئة البيانات التالية بدقة")

    courses = {
        "Jolly Phonics – Beginners": {
            "min_age": 7,
            "max_age": 12,
            "الفئة المستهدفة": "أطفال 7-12 سنة (غير ناطقين بالإنجليزية)",
            "الهدف": "تعليم أصوات الحروف وتكوين كلمات",
            "المدة": "8 أسابيع - 14 حصة (مرتان أسبوعيًا – 40 دقيقة)",
            "المحتوى": ["الأصوات الأساسية في اللغة الإنجليزية", "الأغاني وقراءة القصص التفاعلية", "تكوين وقراءة الكلمات", "تمارين صوتية وبصرية"],
            "السعر": "90 دولار",
            "خطة الدفع": "دفعتين × 45 دولار أو 15 دولار أسبوعيًا",
            "ضمان الاسترداد": "استرداد كامل بعد أول حصة إذا لم ينسجم الطفل، واسترداد 50% حتى الحصة الرابعة"
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

    with st.form("registration_form"):
        col1, col2 = st.columns(2)
        selected_course = col1.selectbox("اختر الكورس", list(courses.keys()), key="course_selector")
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
            if age < 15 and level == "جامعي":
                st.error("⚠️ لا يمكن اختيار 'جامعي' إذا كان العمر أقل من 15 سنة.")
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

# --- Display by role ---
role = st.session_state.get("role")
if role == "admin":
    show_admin_panel()
elif role == "power":
    show_power_panel()
else:
    show_registration_panel()
