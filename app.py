
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, send_file, flash
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from apscheduler.schedulers.background import BackgroundScheduler
from config import Config
from database import get_db_connection
from datetime import date, datetime, time
from openpyxl import Workbook
from zoneinfo import ZoneInfo
from uuid import uuid4
import os
import zipfile
import hashlib
import shutil
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()


def send_whatsapp_notification(message):

    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    whatsapp_from = os.getenv("TWILIO_WHATSAPP_FROM")

    if not account_sid or not auth_token or not whatsapp_from:
        print("Twilio credentials missing!")
        return

    client = Client(account_sid, auth_token)

    recipients = [
        os.getenv("CR_WHATSAPP_1"),
        os.getenv("CR_WHATSAPP_2")
    ]

    for recipient in recipients:
        if not recipient:
            continue

        try:
            client.messages.create(
                body=message,
                from_=whatsapp_from,
                to=recipient
            )
            print("WhatsApp message sent to:", recipient)

        except Exception as e:
            print("WhatsApp sending failed:", e)

app = Flask(__name__)
app.config.from_object(Config)

os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


# Login required decorator
def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapper


# Home page
@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


# Login page

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")

        connection = get_db_connection()

        if not connection:
            return render_template(
                "login.html",
                error="Database connection failed!"
            )

        cursor = connection.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT
                    id,
                    name,
                    phone,
                    password_hash,
                    role,
                    is_active
                FROM users
                WHERE phone = %s
            """, (phone,))

            user = cursor.fetchone()

            if not user:
                return render_template(
                    "login.html",
                    error="Invalid phone number or password!"
                )

            if not user["is_active"]:
                return render_template(
                    "login.html",
                    error="Your account is inactive!"
                )

            if not check_password_hash(
                user["password_hash"], password
            ):
                return render_template(
                    "login.html",
                    error="Invalid phone number or password!"
                )

            # Clear old session
            session.clear()

            # Save user details
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["role"] = user["role"]

            # Role-based redirect
            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))

            elif user["role"] == "cr":
                return redirect(url_for("cr_dashboard"))

            elif user["role"] == "student":
                return redirect(url_for("student_upload"))

            else:
                session.clear()

                return render_template(
                    "login.html",
                    error="Invalid user role!"
                )

        except Exception as e:
            print("LOGIN ERROR:", e)

            return render_template(
                "login.html",
                error="Login failed. Please try again!"
            )

        finally:
            cursor.close()
            connection.close()

    return render_template("login.html")


# Student Registration

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "GET":
        return render_template("register.html")

    name = request.form.get("name", "").strip()
    usn = request.form.get("usn", "").strip().upper()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    print("REGISTER EMAIL RECEIVED:", repr(email))

    # Basic validation
    if not all([name, usn, email, phone, password, confirm_password]):
        return render_template("register.html",
                             error="Please fill all fields!")

    # Email validation
    if "@" not in email or "." not in email.split("@")[-1]:
        return render_template("register.html",
                             error="Enter a valid email address!")

    # Phone validation
    if not phone.isdigit() or not (10 <= len(phone) <= 15):
        return render_template("register.html",
                             error="Enter a valid phone number!")

    # Password validation
    if len(password) < 6:
        return render_template("register.html",
                             error="Password must contain at least 6 characters!")

    if password != confirm_password:
        return render_template("register.html",
                             error="Passwords do not match!")

    connection = None
    cursor = None

    try:
        connection = get_db_connection()

        if not connection:
            return render_template("register.html",
                                 error="Database connection failed!")

        cursor = connection.cursor(dictionary=True, buffered=True)

        # Check duplicate phone, USN or email
        cursor.execute("""
            SELECT id FROM users
            WHERE phone = %s OR usn = %s OR email = %s
            LIMIT 1
        """, (phone, usn, email))

        existing_user = cursor.fetchone()

        if existing_user:
            return render_template(
                "register.html",
                error="Phone number, USN or Email already registered!"
            )

        # Secure password hashing
        password_hash = generate_password_hash(password)

        # Save student
        cursor.execute("""
            INSERT INTO users
            (name, email, phone, password_hash, role,
             usn, semester, department, is_active)
            VALUES (%s, %s, %s, %s, 'student',
                    %s, 5, 'CSE', TRUE)
        """, (
            name,
            email,
            phone,
            password_hash,
            usn
        ))

        connection.commit()

        return render_template(
            "login.html",
            success="Registration successful! Please login."
        )

    except Exception as e:
        if connection:
            connection.rollback()

        print("Registration Error:", e)

        return render_template(
            "register.html",
            error="Registration failed. Please try again."
        )

    finally:
        if cursor:
            cursor.close()

        if connection:
            connection.close()
    return render_template("register.html")



# Common dashboard redirect
@app.route("/dashboard")
@login_required
def dashboard():

    # Student, CR and Admin all open upload page first
    return redirect(url_for("student_upload"))


@app.route("/student-dashboard")
@login_required
def student_dashboard():

    if session.get("role") != "student":
        return "Access Denied", 403

    return redirect(url_for("student_upload"))


@app.route("/cr-dashboard")
@login_required
def cr_dashboard():

    if session.get("role") != "cr":
        return "Access Denied", 403

    return render_template(
        "cr_dashboard.html",
        user_name=session["user_name"]
    )


@app.route("/admin-dashboard")
@login_required
def admin_dashboard():

    if session.get("role") != "admin":
        return "Access Denied", 403

    connection = get_db_connection()

    if not connection:
        return "Database connection failed!"

    cursor = connection.cursor(dictionary=True)

    try:
        # Total active CSE 5th semester members
        # Student + CR + Admin
        cursor.execute("""
            SELECT COUNT(*) AS total_students
            FROM users
            WHERE role IN ('student', 'cr', 'admin')
            AND department = 'CSE'
            AND semester = 5
            AND is_active = TRUE
        """)
        total_students = cursor.fetchone()["total_students"]

        # Total active CR accounts
        cursor.execute("""
            SELECT COUNT(*) AS total_crs
            FROM users
            WHERE role = 'cr'
            AND department = 'CSE'
            AND semester = 5
            AND is_active = TRUE
        """)
        total_crs = cursor.fetchone()["total_crs"]

        # Today's uploads from all active CSE 5th semester members
        cursor.execute("""
            SELECT COUNT(*) AS uploaded_today
            FROM submissions s
            JOIN users u ON s.user_id = u.id
            WHERE s.submission_date = CURDATE()
            AND u.role IN ('student', 'cr', 'admin')
            AND u.department = 'CSE'
            AND u.semester = 5
            AND u.is_active = TRUE
        """)
        uploaded_today = cursor.fetchone()["uploaded_today"]

        not_uploaded_today = max(
            0, total_students - uploaded_today
        )

        return render_template(
            "admin_dashboard.html",
            total_students=total_students,
            total_crs=total_crs,
            uploaded_today=uploaded_today,
            not_uploaded_today=not_uploaded_today
        )

    finally:
        cursor.close()

@app.route("/student-management")
@login_required
def student_management():

    if session.get("role") not in ["admin", "cr"]:
        return "Access Denied", 403

    connection = get_db_connection()

    if not connection:
        return "Database connection failed!"

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                u.id,
                u.name,
                u.usn,
                u.phone,
                u.email,
                u.role,
                u.department,
                u.semester,
                u.is_active,
                u.created_at,
                CASE
                    WHEN s.id IS NOT NULL THEN 'Uploaded'
                    ELSE 'Not Uploaded'
                END AS upload_status
            FROM users u
            LEFT JOIN submissions s
                ON u.id = s.user_id
                AND s.submission_date = CURDATE()
            WHERE u.role IN ('student', 'cr', 'admin')
            AND u.department = 'CSE'
            AND u.semester = 5
            AND u.is_active = TRUE
            ORDER BY
                CASE WHEN u.usn IS NULL OR u.usn = '' THEN 1 ELSE 0 END,
                u.usn ASC,
                u.name ASC
        """)

        students = cursor.fetchall()

        return render_template(
            "student_management.html",
            students=students
        )

    finally:
        cursor.close()
        connection.close()



@app.route("/cr-management")
@login_required
def cr_management():

    if session.get("role") != "admin":
        return "Access Denied", 403

    connection = get_db_connection()

    if not connection:
        return "Database connection failed!"

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                id,
                name,
                phone,
                email,
                department,
                semester,
                is_active,
                created_at
            FROM users
            WHERE role = 'cr'
            ORDER BY name ASC
        """)

        crs = cursor.fetchall()

        return render_template(
            "cr_management.html",
            crs=crs
        )

    finally:
        cursor.close()
        connection.close()



@app.route("/uploaded-students")
@login_required
def uploaded_students():

    if session.get("role") not in ["admin", "cr"]:
        return "Access Denied", 403

    connection = get_db_connection()

    if not connection:
        return "Database connection failed!"

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                s.id,
                u.name,
                u.usn,
                u.phone,
                u.role,
                u.department,
                u.semester,
                s.submission_date,
                s.uploaded_at
            FROM submissions s
            JOIN users u ON s.user_id = u.id
            WHERE u.role IN ('student', 'cr', 'admin')
            AND u.department = 'CSE'
            AND u.semester = 5
            AND u.is_active = TRUE
            AND s.submission_date = CURDATE()
            ORDER BY
                CASE WHEN u.usn IS NULL OR u.usn = '' THEN 1 ELSE 0 END,
                u.usn ASC,
                u.name ASC
        """)

        records = cursor.fetchall()

        return render_template(
            "uploaded_students.html",
            records=records
        )

    finally:
        cursor.close()
        connection.close()



@app.route("/view-submission/<int:submission_id>")
@login_required
def view_submission(submission_id):

    if session.get("role") not in ["admin", "cr"]:
        return "Access Denied", 403

    connection = get_db_connection()

    if not connection:
        return "Database connection failed!"

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT image_path
            FROM submissions
            WHERE id = %s
        """, (submission_id,))

        record = cursor.fetchone()

        if not record:
            return "Screenshot not found!", 404

        image_path = os.path.abspath(record["image_path"])
        upload_folder = os.path.abspath(Config.UPLOAD_FOLDER)

        if not image_path.startswith(upload_folder + os.sep):
            return "Invalid file path!", 403

        if not os.path.isfile(image_path):
            return "Screenshot file not found!", 404

        return send_file(image_path)

    finally:
        cursor.close()
        connection.close()



@app.route("/not-uploaded-students")
@login_required
def not_uploaded_students():

    if session.get("role") not in ["admin", "cr"]:
        return "Access Denied", 403

    connection = get_db_connection()

    if not connection:
        return "Database connection failed!"

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                u.id,
                u.name,
                u.usn,
                u.phone,
                u.email,
                u.role,
                u.department,
                u.semester
            FROM users u
            WHERE u.role IN ('student', 'cr', 'admin')
            AND u.department = 'CSE'
            AND u.semester = 5
            AND u.is_active = TRUE
            AND NOT EXISTS (
                SELECT 1
                FROM submissions s
                WHERE s.user_id = u.id
                AND s.submission_date = CURDATE()
            )
            ORDER BY
                CASE WHEN u.usn IS NULL OR u.usn = '' THEN 1 ELSE 0 END,
                u.usn ASC,
                u.name ASC
        """)

        students = cursor.fetchall()

        return render_template(
            "not_uploaded_students.html",
            students=students
        )

    finally:
        cursor.close()
        connection.close()


@app.route("/daily-reports")
@login_required
def daily_reports():

    if session.get("role") not in ["admin", "cr"]:
        return "Access Denied", 403

    connection = get_db_connection()

    if not connection:
        return "Database connection failed!"

    cursor = connection.cursor(dictionary=True)

    try:
        # Total active CSE 5th semester members
        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM users
            WHERE role IN ('student', 'cr', 'admin')
            AND department = 'CSE'
            AND semester = 5
            AND is_active = TRUE
        """)
        total_students = cursor.fetchone()["total"]

        # Today's uploads from all members
        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM submissions s
            JOIN users u ON s.user_id = u.id
            WHERE s.submission_date = CURDATE()
            AND u.role IN ('student', 'cr', 'admin')
            AND u.department = 'CSE'
            AND u.semester = 5
            AND u.is_active = TRUE
        """)
        uploaded_count = cursor.fetchone()["total"]

        not_uploaded_count = max(
            0, total_students - uploaded_count
        )

        upload_percentage = (
            round((uploaded_count / total_students) * 100, 2)
            if total_students > 0 else 0
        )

        # Previous daily reports
        cursor.execute("""
            SELECT
                report_date,
                uploaded_count,
                not_uploaded_count,
                uploaded_count + not_uploaded_count AS total_students,
                generated_at,
                excel_path,
                zip_path
            FROM daily_reports
            ORDER BY report_date DESC
        """)

        reports = cursor.fetchall()

        # Today's generated files
        cursor.execute("""
            SELECT excel_path, zip_path
            FROM daily_reports
            WHERE report_date = CURDATE()
        """)

        today_report = cursor.fetchone()

        excel_path = (
            today_report["excel_path"] if today_report else None
        )
        zip_path = (
            today_report["zip_path"] if today_report else None
        )

        return render_template(
            "daily_reports.html",
            total_students=total_students,
            uploaded_count=uploaded_count,
            not_uploaded_count=not_uploaded_count,
            upload_percentage=upload_percentage,
            reports=reports,
            excel_path=excel_path,
            zip_path=zip_path
        )

    finally:
        cursor.close()
        connection.close()


def generate_daily_excel_report(report_date=None):

    if report_date is None:
        report_date = date.today()

    connection = get_db_connection()

    if not connection:
        print("Database connection failed!")
        return None

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                u.id,
                u.name,
                u.usn,
                u.phone,
                u.role,
                u.department,
                u.semester,
                CASE
                    WHEN s.id IS NOT NULL THEN 'Uploaded'
                    ELSE 'Not Uploaded'
                END AS upload_status,
                s.uploaded_at
            FROM users u
            LEFT JOIN submissions s
                ON u.id = s.user_id
                AND s.submission_date = %s
            WHERE u.role IN ('student', 'cr', 'admin')
            AND u.department = 'CSE'
            AND u.semester = 5
            AND u.is_active = TRUE
            ORDER BY u.name
        """, (report_date,))

        students = cursor.fetchall()

        # Create date-wise folder
        date_folder = os.path.join(
            Config.UPLOAD_FOLDER,
            report_date.strftime("%Y-%m-%d")
        )

        os.makedirs(date_folder, exist_ok=True)

        excel_path = os.path.join(
            date_folder,
            "daily_report.xlsx"
        )

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Daily Report"

        sheet.append([
            "ID",
            "Name",
            "USN",
            "Phone",
            "Role",
            "Department",
            "Semester",
            "Upload Status",
            "Uploaded At"
        ])

        uploaded_count = 0
        not_uploaded_count = 0

        for student in students:

            if student["upload_status"] == "Uploaded":
                uploaded_count += 1
            else:
                not_uploaded_count += 1

            sheet.append([
                student["id"],
                student["name"],
                student["usn"],
                student["phone"],
                student["role"],
                student["department"],
                student["semester"],
                student["upload_status"],
                str(student["uploaded_at"] or "")
            ])

        workbook.save(excel_path)

        # Save report details in database
        cursor.execute("""
            INSERT INTO daily_reports
                (report_date, uploaded_count,
                 not_uploaded_count, excel_path)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                uploaded_count = VALUES(uploaded_count),
                not_uploaded_count = VALUES(not_uploaded_count),
                excel_path = VALUES(excel_path)
        """, (
            report_date,
            uploaded_count,
            not_uploaded_count,
            excel_path
        ))

        connection.commit()

        print("Daily Excel Report Generated:", excel_path)

        return excel_path

    except Exception as e:
        connection.rollback()
        print("Report Generation Error:", e)
        return None

    finally:
        cursor.close()
        connection.close()


def generate_daily_zip_report(report_date=None):

    if report_date is None:
        report_date = date.today()

    connection = get_db_connection()

    if not connection:
        print("Database connection failed!")
        return None

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                u.id,
                u.name,
                u.usn,
                u.role,
                s.image_path
            FROM submissions s
            JOIN users u ON s.user_id = u.id
            WHERE s.submission_date = %s
            AND u.role IN ('student', 'cr', 'admin')
            AND u.department = 'CSE'
            AND u.semester = 5
            AND u.is_active = TRUE
            ORDER BY u.name
        """, (report_date,))

        records = cursor.fetchall()

        date_folder = os.path.join(
            Config.UPLOAD_FOLDER,
            report_date.strftime("%Y-%m-%d")
        )

        uploaded_folder = os.path.join(date_folder, "uploaded")
        os.makedirs(uploaded_folder, exist_ok=True)

        zip_path = os.path.join(
            date_folder,
            "daily_screenshots.zip"
        )

        with zipfile.ZipFile(
            zip_path, "w", zipfile.ZIP_DEFLATED
        ) as zip_file:

            for record in records:

                image_path = os.path.abspath(record["image_path"])

                # Security: only include files inside upload folder
                upload_root = os.path.abspath(Config.UPLOAD_FOLDER)

                if not image_path.startswith(upload_root + os.sep):
                    continue

                if not os.path.isfile(image_path):
                    continue

                identity = record["usn"] or str(record["id"])

                safe_identity = secure_filename(str(identity))
                safe_name = secure_filename(str(record["name"]))

                filename = (
                    f"{record['role']}_{safe_identity}_{safe_name}"
                    + os.path.splitext(image_path)[1].lower()
                )

                zip_file.write(
                    image_path,
                    arcname=os.path.join("uploaded", filename)
                )

        cursor.execute("""
            UPDATE daily_reports
            SET zip_path = %s
            WHERE report_date = %s
        """, (zip_path, report_date))

        connection.commit()

        print("Daily ZIP Generated:", zip_path)

        return zip_path

    except Exception as e:
        connection.rollback()
        print("ZIP Generation Error:", e)
        return None

    finally:
        cursor.close()
        connection.close()


@app.route("/download-excel")
@login_required
def download_excel():

    if session.get("role") not in ["admin", "cr"]:
        return "Access Denied", 403

    connection = get_db_connection()

    if not connection:
        return "Database connection failed!", 500

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT excel_path
            FROM daily_reports
            WHERE report_date = CURDATE()
        """)

        report = cursor.fetchone()

        if not report or not report["excel_path"]:
            return "Today's Excel report is not available yet.", 404

        file_path = os.path.abspath(report["excel_path"])
        upload_folder = os.path.abspath(Config.UPLOAD_FOLDER)

        if not file_path.startswith(upload_folder + os.sep):
            return "Invalid file path!", 403

        if not os.path.isfile(file_path):
            return "Excel file not found!", 404

        return send_file(
            file_path,
            as_attachment=True,
            download_name="daily_report.xlsx"
        )

    finally:
        cursor.close()
        connection.close()


@app.route("/download-zip")
@login_required
def download_zip():

    if session.get("role") not in ["admin", "cr"]:
        return "Access Denied", 403

    connection = get_db_connection()

    if not connection:
        return "Database connection failed!", 500

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT zip_path
            FROM daily_reports
            WHERE report_date = CURDATE()
        """)

        report = cursor.fetchone()

        if not report or not report["zip_path"]:
            return "Today's ZIP file is not available yet.", 404

        file_path = os.path.abspath(report["zip_path"])
        upload_folder = os.path.abspath(Config.UPLOAD_FOLDER)

        if not file_path.startswith(upload_folder + os.sep):
            return "Invalid file path!", 403

        if not os.path.isfile(file_path):
            return "ZIP file not found!", 404

        return send_file(
            file_path,
            as_attachment=True,
            download_name="daily_screenshots.zip"
        )

    finally:
        cursor.close()
        connection.close()


@app.route("/student-upload", methods=["GET", "POST"])
@login_required
def student_upload():

    # Student, CR and Admin can upload screenshots
    if session.get("role") not in ["student", "cr", "admin"]:
        return "Access Denied", 403

    if request.method == "GET":
        return render_template("student_upload.html")

    current_datetime = datetime.now(ZoneInfo("Asia/Kolkata"))
    current_time = current_datetime.time()

    # Upload timing: 8:30 AM to 4:00 PM
    if not (time(8, 30) <= current_time <= time(16, 0)):
        flash("Uploads are allowed only between 8:30 AM and 4:00 PM.")
        return redirect(url_for("student_upload"))

    if "screenshot" not in request.files:
        flash("Please select a screenshot.")
        return redirect(url_for("student_upload"))

    file = request.files["screenshot"]

    if file.filename == "":
        flash("Please select a file.")
        return redirect(url_for("student_upload"))

    if not allowed_file(file.filename):
        flash("Only PNG, JPG, JPEG and WEBP files are allowed.")
        return redirect(url_for("student_upload"))

    file_bytes = file.read()

    if not file_bytes:
        flash("Selected file is empty.")
        return redirect(url_for("student_upload"))

    if len(file_bytes) > 5 * 1024 * 1024:
        flash("File size must be below 5 MB.")
        return redirect(url_for("student_upload"))

    image_hash = hashlib.sha256(file_bytes).hexdigest()

    connection = get_db_connection()

    if not connection:
        return "Database connection failed!", 500

    cursor = connection.cursor(dictionary=True)

    saved_path = None

    try:
        # Check whether this screenshot was uploaded before
        cursor.execute("""
            SELECT id
            FROM submissions
            WHERE image_hash = %s
        """, (image_hash,))

        if cursor.fetchone():
            flash("This screenshot has already been uploaded.")
            return redirect(url_for("student_upload"))

        user_id = session.get("user_id")
        submission_date = current_datetime.date()

        # Check today's submission
        cursor.execute("""
            SELECT id
            FROM submissions
            WHERE user_id = %s
            AND submission_date = %s
        """, (user_id, submission_date))

        if cursor.fetchone():
            flash("You have already uploaded today's screenshot.")
            return redirect(url_for("student_upload"))

        # Create date-wise upload folder
        date_folder = os.path.join(
            Config.UPLOAD_FOLDER,
            submission_date.strftime("%Y-%m-%d"),
            "uploaded"
        )

        os.makedirs(date_folder, exist_ok=True)

        original_name = secure_filename(file.filename)
        extension = os.path.splitext(original_name)[1].lower()

        filename = f"{user_id}_{uuid4().hex}{extension}"

        saved_path = os.path.abspath(
            os.path.join(date_folder, filename)
        )

        with open(saved_path, "wb") as output_file:
            output_file.write(file_bytes)

        # Save submission in MySQL
        cursor.execute("""
            INSERT INTO submissions (
                user_id,
                submission_date,
                image_filename,
                image_path,
                image_hash
            )
            VALUES (%s, %s, %s, %s, %s)
        """, (
            user_id,
            submission_date,
            filename,
            saved_path,
            image_hash
        ))

        connection.commit()

        flash("Screenshot uploaded successfully!")
        return redirect(url_for("student_upload"))

    except Exception as e:
        connection.rollback()

        if saved_path and os.path.isfile(saved_path):
            os.remove(saved_path)

        print("Screenshot Upload Error:", e)
        flash("Upload failed. Please try again.")
        return redirect(url_for("student_upload"))

    finally:
        cursor.close()
        connection.close()


@app.route("/role-access-login", methods=["POST"])
@login_required
def role_access_login():

    # Any logged-in user can request Admin or CR access
    if session.get("role") not in ["student", "cr", "admin"]:
        return "Access Denied", 403

    phone = request.form.get("phone", "").strip()
    password = request.form.get("password", "")
    requested_role = request.form.get("role", "").strip()

    if requested_role not in ["admin", "cr"]:
        flash("Invalid access type.")
        return redirect(url_for("student_upload"))

    connection = get_db_connection()

    if not connection:
        flash("Database connection failed!")
        return redirect(url_for("student_upload"))

    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id, name, phone, password_hash, role, is_active
            FROM users
            WHERE phone = %s AND role = %s
        """, (phone, requested_role))

        user = cursor.fetchone()

        if (
            user
            and user["is_active"]
            and check_password_hash(user["password_hash"], password)
        ):

            session.clear()

            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["role"] = user["role"]

            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))

            if user["role"] == "cr":
                return redirect(url_for("cr_dashboard"))

        flash("Invalid phone number or password.")
        return redirect(url_for("student_upload"))

    finally:
        cursor.close()
        connection.close()


# Logout
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# Application start and automatic daily report scheduler

if __name__ == "__main__":

    scheduler = BackgroundScheduler(
        timezone="Asia/Kolkata"
    )

    def daily_report_job():

        print("Starting 4 PM Daily Report Generation...")

        report_date = date.today()

        # Generate Excel report
        excel_file = generate_daily_excel_report(report_date)

        if not excel_file:
            print("Excel report generation failed!")
            return

        # Generate ZIP report
        zip_file = generate_daily_zip_report(report_date)

        if not zip_file:
            print("ZIP report generation failed!")
            return

        # Get daily report counts
        connection = get_db_connection()

        if not connection:
            print("Database connection failed!")
            return

        cursor = connection.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT uploaded_count, not_uploaded_count
                FROM daily_reports
                WHERE report_date = %s
            """, (report_date,))

            report = cursor.fetchone()

            if not report:
                print("Daily report counts not found!")
                return

            uploaded = report["uploaded_count"]
            not_uploaded = report["not_uploaded_count"]
            total = uploaded + not_uploaded

            message = (
                "STUDENT DAILY LOGIN REPORT\n\n"
                f"Date: {report_date}\n"
                f"Total Members: {total}\n"
                f"Uploaded: {uploaded}\n"
                f"Not Uploaded: {not_uploaded}\n\n"
                "Daily Excel Report: Ready\n"
                "Daily Screenshots ZIP: Ready\n\n"
                "Student Daily Login Portal"
            )

            # Send WhatsApp notification to both CRs
            send_whatsapp_notification(message)

            print("Daily WhatsApp Notifications Completed!")

        except Exception as e:
            print("WhatsApp Report Error:", e)

        finally:
            cursor.close()
            connection.close()

        print("Daily Report Job Completed!")

    scheduler.add_job(
        daily_report_job,
        trigger="cron",
        hour=16,
        minute=0,
        id="daily_report_job",
        replace_existing=True
    )

    scheduler.start()

    print("Daily Scheduler Started!")
    print("Automatic report time: 4:00 PM IST")

    app.run(
        debug=True,
        use_reloader=False
    )

