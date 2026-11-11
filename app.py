from flask import (
    Flask,
    render_template,
    request,
    redirect,
    session,
    flash,
    jsonify,
    make_response
)

from werkzeug.security import check_password_hash, generate_password_hash

from datetime import date, datetime, timedelta
import os
import uuid
import csv
from io import StringIO

from database import (
    create_tables,
    get_connection
)

from config import SECRET_KEY
from modules.results import results_bp
from modules.student_attendance import student_attendance_bp
from modules.expenditures import expenditures_bp
from modules.document_sharing import document_sharing_bp
from modules.students_by_class import students_by_class_bp
from modules.yearly_history import yearly_history_bp
from modules.student_promotion import student_promotion_bp
from modules.all_reports import all_reports_bp


# ==================================================
# FLASK APPLICATION
# ==================================================

app = Flask(__name__)

app.secret_key = SECRET_KEY

app.register_blueprint(results_bp)
app.register_blueprint(student_attendance_bp)
app.register_blueprint(expenditures_bp)
app.register_blueprint(document_sharing_bp)
app.register_blueprint(students_by_class_bp)
app.register_blueprint(yearly_history_bp)
app.register_blueprint(student_promotion_bp)
app.register_blueprint(all_reports_bp)


# ==================================================
# UPLOAD SETTINGS
# ==================================================

UPLOAD_FOLDER = os.path.join(
    "static",
    "uploads"
)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)

ALLOWED_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "gif"
}


def normalize_academic_year_state():
    """
    Keep academic-year state consistent across the whole application.

    When years exist, exactly one is active. If multiple years are marked
    active, the newest active year wins. If none is active, the newest year
    becomes active. Historical years are never removed by this helper.
    """
    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS academic_years (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year_name TEXT UNIQUE NOT NULL,
            is_active INTEGER DEFAULT 0
        )
    """)

    columns = conn.execute("PRAGMA table_info(academic_years)").fetchall()
    names = [row["name"] for row in columns]
    if "is_active" not in names:
        conn.execute(
            "ALTER TABLE academic_years ADD COLUMN is_active INTEGER DEFAULT 0"
        )

    years = conn.execute("""
        SELECT id, year_name, is_active
        FROM academic_years
        ORDER BY id DESC
    """).fetchall()

    if not years:
        conn.commit()
        conn.close()
        return None

    active_years = [
        row for row in years
        if int(row["is_active"] or 0) == 1
    ]

    winner = active_years[0] if active_years else years[0]

    conn.execute("UPDATE academic_years SET is_active = 0")
    conn.execute(
        "UPDATE academic_years SET is_active = 1 WHERE id = ?",
        (winner["id"],)
    )
    conn.commit()

    year = conn.execute("""
        SELECT *
        FROM academic_years
        WHERE id = ?
    """, (winner["id"],)).fetchone()

    conn.close()
    return year



def get_active_academic_year():
    """Return the single normalized active academic year."""
    return normalize_academic_year_state()



def ensure_column(table, column, definition):
    """
    Safely add a column only when the table exists
    and the column does not already exist.
    """
    conn = get_connection()

    columns = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    # If the table does not exist, do nothing.
    if not columns:
        conn.close()
        return

    existing = [row["name"] for row in columns]

    if column not in existing:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )
        conn.commit()

    conn.close()


# ============================================================
# SCHOOL SETTINGS TABLE
# ============================================================


def ensure_school_settings_table():

    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS school_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_name TEXT,
            school_address TEXT,
            phone_number TEXT,
            email TEXT,
            principal_name TEXT,
            school_motto TEXT,
            school_logo TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    conn.commit()
    conn.close()

    ensure_column(
        "school_settings",
        "school_phone",
        "TEXT"
    )

    ensure_column(
        "school_settings",
        "school_email",
        "TEXT"
    )


# ============================================================
# SALARY TABLES
# ============================================================




def ensure_salary_tracking_schema(connection):
    """Prepare the selected school's salary-attendance tracking schema."""
    columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(teacher_salaries)"
        ).fetchall()
    }
    if "attendance_tracking_enabled" not in columns:
        connection.execute(
            "ALTER TABLE teacher_salaries "
            "ADD COLUMN attendance_tracking_enabled INTEGER DEFAULT 0"
        )
    connection.execute("""
        CREATE TABLE IF NOT EXISTS teacher_salary_attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            salary_id INTEGER NOT NULL,
            attendance_id INTEGER NOT NULL UNIQUE,
            teacher_id INTEGER NOT NULL,
            salary_month TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.commit()

def ensure_salary_tables():

    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS teacher_salaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER,
            salary_month TEXT,
            salary_rate REAL DEFAULT 0,
            days_present INTEGER DEFAULT 0,
            days_absent INTEGER DEFAULT 0,
            leave_days INTEGER DEFAULT 0,
            amount_paid REAL DEFAULT 0,
            amount_received REAL DEFAULT 0,
            payment_status TEXT DEFAULT 'Unpaid',
            payment_date TEXT,
            remarks TEXT,
            created_at TEXT,
            time_received TEXT
        )
    """)

    # Track attendance records covered by confirmed salary payments.
    # Existing historical payments are deliberately not assigned dates.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS teacher_salary_attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            salary_id INTEGER NOT NULL,
            attendance_id INTEGER NOT NULL UNIQUE,
            teacher_id INTEGER NOT NULL,
            salary_month TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

    ensure_column(
        "teacher_salaries",
        "amount_received",
        "REAL DEFAULT 0"
    )

    ensure_column(
        "teacher_salaries",
        "time_received",
        "TEXT"
    )

    ensure_column(
        "teacher_salaries",
        "academic_year_id",
        "INTEGER"
    )

    ensure_column(
        "teacher_salaries",
        "attendance_tracking_enabled",
        "INTEGER DEFAULT 0"
    )


# ============================================================
# STUDENT FEE TABLES
# ============================================================


def ensure_fee_tables():

    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_fee_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER,
            academic_year_id INTEGER,
            total_fee REAL DEFAULT 0,
            amount REAL DEFAULT 0,
            fee_amount REAL DEFAULT 0,
            description TEXT,
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_fee_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER,
            academic_year_id INTEGER,
            description TEXT,
            amount REAL DEFAULT 0,
            created_at TEXT
        )
    """)

    # Student-specific charges such as Graduation, Clearance,
    # Certificate, etc. These belong to one student only and do
    # not automatically appear on every student in the class.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_specific_fees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            class_id INTEGER,
            academic_year_id INTEGER,
            description TEXT,
            amount REAL DEFAULT 0,
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_fee_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            class_id INTEGER,
            academic_year_id INTEGER,
            fee_item_id INTEGER,
            student_specific_fee_id INTEGER,
            amount REAL DEFAULT 0,
            payment_date TEXT,
            description TEXT,
            payment_method TEXT,
            reference TEXT,
            created_at TEXT
        )
    """)

    # Student-level scholarship settings. These do NOT change the master
    # class bill. They only change what one student is required to pay.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_fee_scholarships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            class_id INTEGER,
            academic_year_id INTEGER,
            scholarship_name TEXT,
            scholarship_type TEXT DEFAULT 'Selected Bills',
            notes TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(student_id, academic_year_id)
        )
    """)

    # Percentage discounts per class fee for a scholarship student.
    # 100 = fully exempt, 50 = student pays half, 0 = no discount.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_fee_scholarship_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            class_id INTEGER,
            academic_year_id INTEGER,
            fee_item_id INTEGER,
            discount_percent REAL DEFAULT 0,
            created_at TEXT,
            UNIQUE(student_id, academic_year_id, fee_item_id)
        )
    """)

    # Add the scholarship reason to THIS selected school's database.
    scholarship_columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(student_fee_scholarships)"
        ).fetchall()
    }

    if "scholarship_reason" not in scholarship_columns:
        conn.execute(
            "ALTER TABLE student_fee_scholarships "
            "ADD COLUMN scholarship_reason TEXT DEFAULT ''"
        )

    conn.commit()
    conn.close()

    ensure_column(
        "student_fee_scholarships",
        "scholarship_reason",
        "TEXT DEFAULT ''"
    )

    ensure_column(
        "student_fee_settings",
        "fee_amount",
        "REAL DEFAULT 0"
    )

    ensure_column(
        "student_payments",
        "fee_item_id",
        "INTEGER"
    )

    ensure_column(
        "student_payments",
        "payment_description",
        "TEXT"
    )

    ensure_column(
        "student_payments",
        "description",
        "TEXT"
    )

    ensure_column(
        "student_fee_payments",
        "fee_item_id",
        "INTEGER"
    )

    ensure_column(
        "student_fee_payments",
        "student_specific_fee_id",
        "INTEGER"
    )


# ============================================================
# CREATE / UPDATE DATABASE STRUCTURE
# ============================================================

create_tables()
ensure_school_settings_table()
ensure_salary_tables()
ensure_fee_tables()

# Normalize the academic-year table whenever the application starts.
normalize_academic_year_state()


# ============================================================
# GLOBAL ACADEMIC-YEAR CONTEXT
# ============================================================

@app.context_processor

def inject_academic_year_context():
    """Make the current academic year available to every template."""
    active_year = get_active_academic_year()
    conn = get_connection()
    academic_year_list = conn.execute("""
        SELECT id, year_name, is_active
        FROM academic_years
        ORDER BY id DESC
    """).fetchall()
    conn.close()

    return {
        "active_year": active_year,
        "active_academic_year": active_year,
        "current_academic_year": active_year,
        "academic_year": active_year,
        "academic_years_list": academic_year_list,
    }


@app.after_request

def add_global_academic_year_banner(response):
    """Show the current academic year on pages that do not already show it."""
    try:
        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            return response

        path = request.path or ""
        if (
            path == "/"
            or path.startswith("/student-fees/student-receipt/")
            or path.startswith("/student-fees/receipt/")
        ):
            return response

        body = response.get_data(as_text=True)
        lower_body = body.lower()

        # Student Fees / Class Bill already have their own year banner.
        if "active academic year:" in lower_body:
            return response

        active_year = get_active_academic_year()
        year_name = active_year["year_name"] if active_year else "Not Set"

        banner = (
            '<div data-global-academic-year-banner="1" '
            'style="background:#17365d;color:#fff;padding:8px 14px;'
            'border-bottom:3px solid #f0ad4e;font-family:Arial,sans-serif;'
            'font-size:13px;font-weight:700;text-align:center;">'
            'CURRENT ACADEMIC YEAR: <span style="font-size:15px;">'
            + str(year_name)
            + '</span><a href="/academic-years" style="color:#fff;'
            'text-decoration:underline;margin-left:12px;font-weight:700;">'
            'Manage Academic Years</a></div>'
        )

        match = re.search(r"<body[^>]*>", body, flags=re.IGNORECASE)
        if match:
            insert_at = match.end()
            body = body[:insert_at] + banner + body[insert_at:]
        else:
            body = banner + body

        response.set_data(body)
        return response
    except Exception:
        # A display helper must never break an otherwise working page.
        return response


# ============================================================
# SCHOLARSHIP HELPERS
# ============================================================


def get_student_scholarship(conn, student_id, academic_year_id):
    """Return the student's scholarship record for the academic year."""
    return conn.execute("""
        SELECT *
        FROM student_fee_scholarships
        WHERE student_id = ?
          AND academic_year_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (student_id, academic_year_id)).fetchone()



def get_student_scholarship_discounts(conn, student_id, academic_year_id):
    """
    Return (scholarship, discount_map).

    For a Full Scholarship every current class bill is discounted 100%,
    including class bills added later. For Selected Bills, only the saved
    fee-item percentages are applied.
    """
    scholarship = get_student_scholarship(
        conn,
        student_id,
        academic_year_id
    )

    discounts = {}

    if not scholarship:
        return scholarship, discounts

    if scholarship["scholarship_type"] == "Full Scholarship":
        discounts["__FULL__"] = 100.0
        return scholarship, discounts

    rows = conn.execute("""
        SELECT fee_item_id, discount_percent
        FROM student_fee_scholarship_items
        WHERE student_id = ?
          AND academic_year_id = ?
    """, (student_id, academic_year_id)).fetchall()

    for row in rows:
        discounts[row["fee_item_id"]] = max(
            0.0,
            min(float(row["discount_percent"] or 0), 100.0)
        )

    return scholarship, discounts



def get_class_fee_discount_percent(scholarship, discount_map, fee_item_id):
    """Return the effective scholarship discount for one class fee."""
    if not scholarship:
        return 0.0

    if scholarship["scholarship_type"] == "Full Scholarship":
        return 100.0

    return float(discount_map.get(fee_item_id, 0.0) or 0.0)



def login_required():
    return "user_id" in session



def allowed_file(filename):

    return (
        "." in filename
        and
        filename.rsplit(
            ".",
            1
        )[1].lower()
        in ALLOWED_EXTENSIONS
    )


# ==================================================
# DATABASE SETUP
# ==================================================

create_tables()


# ==================================================
# ENSURE SALARY TIME COLUMN EXISTS
# ==================================================

def ensure_salary_columns():

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        "PRAGMA table_info(teacher_salaries)"
    )

    columns = cursor.fetchall()

    column_names = [
        column["name"]
        for column in columns
    ]

    if "time_received" not in column_names:

        cursor.execute(
            """
            ALTER TABLE teacher_salaries
            ADD COLUMN time_received TEXT
            """
        )

    connection.commit()

    connection.close()


ensure_salary_columns()


# ==================================================
# LOGIN
# ==================================================

@app.route(
    "/",
    methods=["GET", "POST"]
)
def login():

    from modules.multischool import get_schools
    from modules.multischool import authenticate_school_user

    schools = get_schools()

    if request.method == "POST":

        username = request.form.get("username", "").strip()

        password = request.form.get("password", "")

        school_id = request.form.get("school_id")

        role = request.form.get("role")

        if not school_id:

            return render_template(
                "login.html",
                schools=schools
            )

        try:
            school_id = int(school_id)
        except (TypeError, ValueError):

            return render_template(
                "login.html",
                schools=schools
            )

        user, selected_school = authenticate_school_user(
            school_id,
            username,
            password,
            role
        )

        if user and selected_school:

            session["school_db"] = selected_school["database_path"]

            session["school_id"] = selected_school["id"]

            session["user_id"] = user["id"]

            session["username"] = user["username"]

            session["role"] = user["role"]

            return redirect("/dashboard")

        return render_template(
            "login.html",
            schools=schools
        )

    return render_template(
        "login.html",
        schools=schools
    )


# ==================================================
# DASHBOARD
# ==================================================

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect("/")

    connection = get_connection()
    dashboard_stats = {}
    school_settings = None
    recent_notifications = []
    dashboard_academic_year = '2026/2027'
    dashboard_colors = {
        "page": "#FFFFFF",
        "sidebar": "#000080",
        "cards": "#FFFFFF",
        "buttons": "#000080",
        "links": "#FF8C00",
    }

    try:
        table_names = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        stat_tables = {
            "students": "students",
            "teachers": "teachers",
            "classes": "classes",
            "subjects": "subjects",
            "payments": "student_fee_payments",
            "results": "student_results",
            "attendance": "student_attendance",
            "expenditures": "expenditures",
            "notifications": "notifications",
            "documents": "student_documents",
            "shares": "document_shares",
        }

        for key, table in stat_tables.items():
            if table in table_names:
                dashboard_stats[key] = connection.execute(
                    'SELECT COUNT(*) FROM "' + table + '"'
                ).fetchone()[0]
            else:
                dashboard_stats[key] = 0

        if "academic_years" in table_names:
            year_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(academic_years)"
                ).fetchall()
            }
            if "year_name" in year_columns:
                if "is_active" in year_columns:
                    year_row = connection.execute(
                        "SELECT year_name FROM academic_years "
                        "WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                else:
                    year_row = connection.execute(
                        "SELECT year_name FROM academic_years "
                        "ORDER BY id DESC LIMIT 1"
                    ).fetchone()

                if year_row and year_row["year_name"]:
                    dashboard_academic_year = year_row["year_name"]

        if "school_settings" in table_names:
            school_settings = connection.execute(
                "SELECT * FROM school_settings ORDER BY id DESC LIMIT 1"
            ).fetchone()

        if "dashboard_color_settings" in table_names:
            color_row = connection.execute(
                "SELECT * FROM dashboard_color_settings ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if color_row:
                dashboard_colors = {
                    "page": color_row["page_color"],
                    "sidebar": color_row["sidebar_color"],
                    "cards": color_row["cards_color"],
                    "buttons": color_row["buttons_color"],
                    "links": color_row["links_color"],
                }

        if "notifications" in table_names:
            notification_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(notifications)"
                ).fetchall()
            }
            if {"school_id", "recipient_user_id", "created_at",
                "title", "message", "is_read"}.issubset(notification_columns):
                school_id = session.get("school_id")
                user_id = session.get("user_id")
                if school_id is not None and user_id is not None:
                    selected_columns = [
                        "id", "title", "message", "is_read", "created_at"
                    ]
                    for optional_column in (
                        "notification_type", "related_name", "related_id"
                    ):
                        if optional_column in notification_columns:
                            selected_columns.append(optional_column)

                    if {"id", "title", "message", "is_read",
                        "created_at"}.issubset(notification_columns):
                        column_sql = ", ".join(selected_columns)
                        recent_notifications = connection.execute(
                            f"""
                            SELECT {column_sql}
                            FROM notifications
                            WHERE school_id = ? AND recipient_user_id = ?
                              AND (
                                  COALESCE(is_read, 0) = 0
                                  OR id IN (
                                      SELECT id
                                      FROM notifications
                                      WHERE school_id = ? AND recipient_user_id = ?
                                        AND datetime(created_at) >= datetime('now', '-24 hours')
                                      ORDER BY datetime(created_at) DESC, id DESC
                                      LIMIT 2
                                  )
                              )
                            ORDER BY datetime(created_at) DESC, id DESC
                            """,
                            (school_id, user_id, school_id, user_id)
                        ).fetchall()

    finally:
        connection.close()

    return render_template(
        "dashboard.html",
        username=session.get("username", "User"),
        role=session.get("role", "User"),
        school_settings=school_settings,
        dashboard_stats=dashboard_stats,
        dashboard_academic_year=dashboard_academic_year,
        recent_notifications=recent_notifications,
        dashboard_colors=dashboard_colors
    )




@app.route("/notifications/all")
def all_notifications():
    if "user_id" not in session:
        return redirect("/")

    school_id = session.get("school_id")
    user_id = session.get("user_id")

    if school_id is None or user_id is None:
        flash("Your school session is missing. Please sign in again.")
        return redirect("/")

    connection = get_connection()
    try:
        exists = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='notifications'"
        ).fetchone()

        if not exists:
            flash("The notifications table is not available.")
            return redirect("/dashboard")

        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(notifications)"
            ).fetchall()
        }

        required = {
            "id", "school_id", "recipient_user_id",
            "title", "message", "is_read", "created_at"
        }
        if not required.issubset(columns):
            flash("The notification table is missing required fields.")
            return redirect("/dashboard")

        notifications = connection.execute(
            """
            SELECT *
            FROM notifications
            WHERE school_id = ? AND recipient_user_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (school_id, user_id)
        ).fetchall()

        return render_template(
            "notifications_all.html",
            notifications=notifications,
            username=session.get("username", "User")
        )
    finally:
        connection.close()


@app.route("/notifications/<int:notification_id>/delete", methods=["POST"])
def delete_notification(notification_id):
    if "user_id" not in session:
        return redirect("/")

    school_id = session.get("school_id")
    user_id = session.get("user_id")

    if school_id is None or user_id is None:
        flash("Your school session is missing. Please sign in again.")
        return redirect("/")

    connection = get_connection()
    try:
        result = connection.execute(
            """
            DELETE FROM notifications
            WHERE id = ? AND school_id = ? AND recipient_user_id = ?
            """,
            (notification_id, school_id, user_id)
        )
        connection.commit()

        if result.rowcount:
            flash("Notification deleted successfully.")
        else:
            flash("Notification not found in your account.")
    except Exception as error:
        connection.rollback()
        print("Notification deletion failed:", error)
        flash("Could not delete the notification.")
    finally:
        connection.close()

    return redirect("/notifications/all")


@app.route("/notifications/<int:notification_id>")
def notification_detail(notification_id):
    if "user_id" not in session:
        return redirect("/")

    school_id = session.get("school_id")
    user_id = session.get("user_id")

    if school_id is None or user_id is None:
        flash("Your school session is missing. Please sign in again.")
        return redirect("/")

    connection = get_connection()
    try:
        table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='notifications'"
        ).fetchone()

        if not table:
            flash("The notifications table is not available.")
            return redirect("/dashboard")

        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(notifications)"
            ).fetchall()
        }

        required = {
            "id", "school_id", "recipient_user_id",
            "title", "message", "is_read", "created_at"
        }
        if not required.issubset(columns):
            flash("The notification table is missing required fields.")
            return redirect("/dashboard")

        notification = connection.execute(
            """
            SELECT *
            FROM notifications
            WHERE id = ? AND school_id = ? AND recipient_user_id = ?
            """,
            (notification_id, school_id, user_id)
        ).fetchone()

        if notification is None:
            flash("That notification was not found in your account.")
            return redirect("/dashboard")

        if not notification["is_read"]:
            connection.execute(
                """
                UPDATE notifications
                SET is_read = 1
                WHERE id = ? AND school_id = ? AND recipient_user_id = ?
                """,
                (notification_id, school_id, user_id)
            )
            connection.commit()

        return render_template(
            "notification_detail.html",
            notification=notification,
            username=session.get("username", "User")
        )
    finally:
        connection.close()


@app.route(
    "/academic-years",
    methods=["GET", "POST"]
)
def academic_years():

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    if request.method == "POST":

        year_name = request.form.get(
            "year_name",
            ""
        ).strip()

        if year_name:

            cursor.execute(
                """
                SELECT id
                FROM academic_years
                WHERE year_name = ?
                """,
                (year_name,)
            )

            existing_year = cursor.fetchone()

            if not existing_year:

                cursor.execute(
                    """
                    INSERT INTO academic_years (
                        year_name,
                        is_active
                    )
                    VALUES (?, 0)
                    """,
                    (year_name,)
                )

                connection.commit()

        connection.close()

        return redirect(
            "/academic-years"
        )

    cursor.execute(
        """
        SELECT *
        FROM academic_years
        ORDER BY id DESC
        """
    )

    years = cursor.fetchall()

    connection.close()

    return render_template(
        "academic_years.html",
        years=years,
        academic_years=years
    )


# ==================================================
# ACTIVATE ACADEMIC YEAR
# ==================================================

@app.route(
    "/academic-years/activate/<int:year_id>"
)
def activate_academic_year(year_id):

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE academic_years
        SET is_active = 0
        """
    )

    cursor.execute(
        """
        UPDATE academic_years
        SET is_active = 1
        WHERE id = ?
        """,
        (year_id,)
    )

    connection.commit()

    connection.close()

    return redirect(
        "/academic-years"
    )


# ==================================================
# DEACTIVATE ACADEMIC YEAR
# ==================================================

@app.route(
    "/academic-years/deactivate/<int:year_id>"
)
def deactivate_academic_year(year_id):

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE academic_years
        SET is_active = 0
        WHERE id = ?
        """,
        (year_id,)
    )

    connection.commit()

    connection.close()

    return redirect(
        "/academic-years"
    )


# ==================================================
# DELETE ACADEMIC YEAR
# ONLY INACTIVE YEARS CAN BE DELETED
# ==================================================

@app.route(
    "/academic-years/delete/<int:year_id>"
)
def delete_academic_year(year_id):

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT is_active
        FROM academic_years
        WHERE id = ?
        """,
        (year_id,)
    )

    year = cursor.fetchone()

    if year:

        if year["is_active"] == 0:

            cursor.execute(
                """
                DELETE FROM academic_years
                WHERE id = ?
                """,
                (year_id,)
            )

            connection.commit()

    connection.close()

    return redirect(
        "/academic-years"
    )


# ==================================================
# CLASSES
# ==================================================

@app.route(
    "/classes",
    methods=["GET", "POST"]
)
def classes():

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    if request.method == "POST":

        class_name = request.form.get(
            "class_name",
            ""
        ).strip()

        description = request.form.get(
            "description",
            ""
        ).strip()

        if class_name:

            table_columns = {
                row[1]
                for row in cursor.execute(
                    "PRAGMA table_info(classes)"
                ).fetchall()
            }

            insert_columns = []
            insert_values = []

            if "name" in table_columns:
                insert_columns.append("name")
                insert_values.append(class_name)

            if "class_name" in table_columns:
                insert_columns.append("class_name")
                insert_values.append(class_name)

            if "description" in table_columns:
                insert_columns.append("description")
                insert_values.append(description)

            columns_sql = ", ".join(
                '"' + column + '"' for column in insert_columns
            )
            values_sql = ", ".join("?" for _ in insert_columns)

            cursor.execute(
                "INSERT INTO classes (" + columns_sql +
                ") VALUES (" + values_sql + ")",
                tuple(insert_values)
            )
            connection.commit()

            connection.close()

            return redirect(
                "/classes"
            )

    cursor.execute(
        """
        SELECT *
        FROM classes
        ORDER BY id DESC
        """
    )

    classes_list = cursor.fetchall()

    connection.close()

    return render_template(
        "classes.html",
        classes=classes_list
    )


@app.route("/classes/edit/<int:class_id>", methods=["GET", "POST"])
def edit_class(class_id):

    if not login_required():
        return redirect("/")

    conn = get_connection()

    if request.method == "POST":

        class_name = request.form.get(
            "class_name",
            ""
        ).strip()

        description = request.form.get(
            "description",
            ""
        ).strip()

        amount = request.form.get(
            "amount",
            "0"
        )

        try:
            amount = float(amount or 0)
        except Exception:
            amount = 0

        conn.execute("""
            UPDATE classes
            SET class_name = ?,
                description = ?,
                amount = ?
            WHERE id = ?
        """, (
            class_name,
            description,
            amount,
            class_id
        ))

        conn.commit()
        conn.close()

        flash("Class updated successfully.")

        return redirect("/classes")

    class_data = conn.execute("""
        SELECT *
        FROM classes
        WHERE id = ?
    """, (class_id,)).fetchone()

    conn.close()

    return render_template(
        "edit_class.html",
        class_data=class_data
    )



# ==================================================
@app.route("/classes/delete/<int:class_id>")
def delete_class(class_id):

    if not login_required():
        return redirect("/")

    conn = get_connection()

    conn.execute("""
        DELETE FROM classes
        WHERE id = ?
    """, (class_id,))

    conn.commit()
    conn.close()

    flash("Class deleted.")

    return redirect("/classes")



# STUDENTS
# ==================================================

@app.route(
    "/students",
    methods=["GET", "POST"]
)
def students():

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    # ==============================================
    # GENERATE NEXT STUDENT ID
    # ==============================================

    cursor.execute(
        """
        SELECT student_id
        FROM students
        WHERE student_id LIKE 'STU%'
        ORDER BY id DESC
        LIMIT 1
        """
    )

    last_student = cursor.fetchone()

    if last_student:

        try:

            last_number = int(
                last_student["student_id"][3:]
            )

            next_number = last_number + 1

        except ValueError:

            next_number = 1

    else:

        next_number = 1

    next_student_id = (
        f"STU{next_number:03d}"
    )

    # ==============================================
    # REGISTER STUDENT
    # ==============================================

    if request.method == "POST":

        first_name = request.form.get(
            "first_name",
            ""
        )

        last_name = request.form.get(
            "last_name",
            ""
        )

        date_of_birth = request.form.get(
            "date_of_birth",
            ""
        )

        gender = request.form.get(
            "gender",
            ""
        )

        class_id = request.form.get(
            "class_id",
            ""
        )

        parent_name = request.form.get(
            "parent_name",
            ""
        )

        parent_phone = request.form.get(
            "parent_phone",
            ""
        )
        student_address = request.form.get("student_address", "").strip()
        parent_address = request.form.get("parent_address", "").strip()
        parent_relationship = request.form.get("parent_relationship", "").strip()
        last_school_attended = request.form.get("last_school_attended", "").strip()
        previous_school_location = request.form.get("previous_school_location", "").strip()
        previous_principal_phone = request.form.get("previous_principal_phone", "").strip()

        admission_date = request.form.get(
            "admission_date",
            ""
        )

        academic_year_id = request.form.get(
            "academic_year_id",
            ""
        )

        status = request.form.get(
            "status",
            ""
        )

        # ==========================================
        # STUDENT PHOTO
        # ==========================================

        photo_filename = None

        photo = request.files.get(
            "photo"
        )

        if photo and photo.filename:

            if allowed_file(
                photo.filename
            ):

                extension = (
                    photo.filename
                    .rsplit(".", 1)[1]
                    .lower()
                )

                photo_filename = (
                    str(uuid.uuid4())
                    + "."
                    + extension
                )

                photo.save(
                    os.path.join(
                        app.config[
                            "UPLOAD_FOLDER"
                        ],
                        photo_filename
                    )
                )

        # ==========================================
        # SAVE STUDENT
        # ==========================================

        cursor.execute(
            """
            INSERT INTO students (

                student_id,
                first_name,
                last_name,
                date_of_birth,
                gender,
                class_id,
                parent_name,
                parent_phone,
                student_address,
                parent_address,
                parent_relationship,
                last_school_attended,
                previous_school_location,
                previous_principal_phone,
                admission_date,
                academic_year_id,
                status,
                photo

            )

            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                next_student_id,
                first_name,
                last_name,
                date_of_birth,
                gender,
                class_id,
                parent_name,
                parent_phone,
                student_address,
                parent_address,
                parent_relationship,
                last_school_attended,
                previous_school_location,
                previous_principal_phone,
                admission_date,
                academic_year_id,
                status,
                photo_filename
            )
        )

        connection.commit()

        try:
            from modules.notifications import create_notification
            sid = session.get("school_id")
            if sid is not None:
                create_notification(
                    connection, sid, "new_student",
                    "New Student Admitted",
                    f"New student registered: {first_name} {last_name}.",
                    related_id=None,
                    related_name=f"{first_name} {last_name}"
                )
        except Exception as notification_error:
            print("Student notification could not be created:", notification_error)

        connection.close()

        return redirect(
            "/students"
        )

    # ==============================================
    # SEARCH STUDENTS
    # ==============================================

    search = request.args.get(
        "search",
        ""
    ).strip()

    if search:

        cursor.execute(
            """
            SELECT
                students.*,
                classes.class_name,
                academic_years.year_name

            FROM students

            LEFT JOIN student_class_placements p
                ON p.student_db_id = students.id
                AND p.academic_year_id = students.academic_year_id

            LEFT JOIN classes
                ON COALESCE(p.class_id, students.class_id) = classes.id

            LEFT JOIN academic_years
                ON students.academic_year_id =
                   academic_years.id

            WHERE
                students.student_id LIKE ?
                OR students.first_name LIKE ?
                OR students.last_name LIKE ?

            ORDER BY students.id DESC
            """,
            (
                "%" + search + "%",
                "%" + search + "%",
                "%" + search + "%"
            )
        )

    else:

        cursor.execute(
            """
            SELECT
                students.*,
                classes.class_name,
                academic_years.year_name

            FROM students

            LEFT JOIN student_class_placements p
                ON p.student_db_id = students.id
                AND p.academic_year_id = students.academic_year_id

            LEFT JOIN classes
                ON COALESCE(p.class_id, students.class_id) = classes.id

            LEFT JOIN academic_years
                ON students.academic_year_id =
                   academic_years.id

            ORDER BY students.id DESC
            """
        )

    students_list = cursor.fetchall()

    # ==============================================
    # GET CLASSES
    # ==============================================

    cursor.execute(
        """
        SELECT *
        FROM classes
        ORDER BY class_name
        """
    )

    classes_list = cursor.fetchall()

    # ==============================================
    # GET ACADEMIC YEARS
    # ==============================================

    cursor.execute(
        """
        SELECT *
        FROM academic_years
        ORDER BY id DESC
        """
    )

    academic_years_list = cursor.fetchall()

    connection.close()

    return render_template(
        "students.html",
        students=students_list,
        classes=classes_list,
        academic_years=academic_years_list,
        next_student_id=next_student_id,
        search=search
    )


# ==================================================


# ==================================================

# ==================================================
# STUDENTS BY PHOTO
# ==================================================

@app.route("/students/activate-deactivate", methods=["GET", "POST"])
def manage_student_status():
    if "user_id" not in session:
        return redirect("/")

    connection = get_connection()
    message = ""

    try:
        if request.method == "POST":
            selected_ids = request.form.getlist("student_ids")
            action = request.form.get("action", "")

            if action not in ("activate", "deactivate"):
                message = "Please choose Activate Selected or Deactivate Selected."
            elif not selected_ids:
                message = "Please select at least one student."
            else:
                status_value = "Active" if action == "activate" else "Inactive"
                valid_ids = []

                for value in selected_ids:
                    try:
                        valid_ids.append(int(value))
                    except (TypeError, ValueError):
                        pass

                if valid_ids:
                    connection.executemany(
                        "UPDATE students SET status = ? WHERE id = ?",
                        [(status_value, student_id) for student_id in valid_ids]
                    )
                    connection.commit()
                    message = f"{len(valid_ids)} student(s) set to {status_value}."
                else:
                    message = "No valid students were selected."

        search = request.args.get("search", "").strip()
        selected_class = request.args.get("class_id", "").strip()

        classes_list = connection.execute(
            "SELECT id, class_name FROM classes ORDER BY class_name"
        ).fetchall()

        query = """
            SELECT s.id, s.student_id, s.first_name, s.last_name,
                   s.status, c.class_name
            FROM students s
            LEFT JOIN classes c ON c.id = s.class_id
        """
        conditions = []
        params = []

        if selected_class:
            try:
                conditions.append("s.class_id = ?")
                params.append(int(selected_class))
            except ValueError:
                selected_class = ""

        if search:
            conditions.append("""
                (s.student_id LIKE ?
                 OR s.first_name LIKE ?
                 OR s.last_name LIKE ?
                 OR (COALESCE(s.first_name, '') || ' ' ||
                     COALESCE(s.last_name, '')) LIKE ?)
            """)
            term = f"%{search}%"
            params.extend([term, term, term, term])

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY c.class_name, s.first_name, s.last_name"
        students_list = connection.execute(query, params).fetchall()

        return render_template(
            "manage_student_status.html",
            students=students_list,
            classes=classes_list,
            search=search,
            selected_class=selected_class,
            message=message
        )
    finally:
        connection.close()



@app.route("/students-age")
def students_age():
    if "user_id" not in session:
        return redirect("/")

    connection = get_connection()
    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT id, year_name, is_active
            FROM academic_years
            ORDER BY id DESC
        """)
        academic_years = cursor.fetchall()

        selected_year = request.args.get("academic_year_id", "").strip()
        if not selected_year:
            active_year = next(
                (y for y in academic_years
                 if "is_active" in y.keys() and y["is_active"]),
                None
            )
            if active_year:
                selected_year = str(active_year["id"])
            elif academic_years:
                selected_year = str(academic_years[0]["id"])

        cursor.execute("""
            SELECT *
            FROM school_settings
            ORDER BY id DESC
            LIMIT 1
        """)
        school_settings = cursor.fetchone()

        students = []
        today = date.today()

        if selected_year.isdigit():
            cursor.execute("""
                SELECT
                    s.id,
                    s.student_id,
                    s.first_name,
                    s.last_name,
                    s.photo,
                    s.date_of_birth,
                    c.class_name,
                    ay.year_name
                FROM students s
                LEFT JOIN student_class_placements p
                    ON p.student_db_id = s.id
                    AND p.academic_year_id = ?
                LEFT JOIN classes c
                    ON c.id = COALESCE(p.class_id, s.class_id)
                LEFT JOIN academic_years ay
                    ON ay.id = s.academic_year_id
                WHERE s.academic_year_id = ?
                ORDER BY COALESCE(c.class_name, 'Unassigned'),
                         s.first_name, s.last_name
            """, (int(selected_year), int(selected_year)))

            for row in cursor.fetchall():
                student = dict(row)
                dob_value = student.get("date_of_birth")
                dob = None

                if dob_value:
                    dob_text = str(dob_value).strip()
                    for fmt in ("%Y-%m-%d", "%d-%m-%Y",
                                "%d/%m/%Y", "%Y/%m/%d"):
                        try:
                            dob = datetime.strptime(
                                dob_text[:10], fmt
                            ).date()
                            break
                        except ValueError:
                            continue

                if dob and dob <= today:
                    age_years = today.year - dob.year
                    if (today.month, today.day) < (dob.month, dob.day):
                        age_years -= 1
                    last_birthday = dob.replace(year=today.year)
                    if last_birthday > today:
                        last_birthday = dob.replace(year=today.year - 1)

                    extra_days = (today - last_birthday).days
                    student["age"] = f"{age_years} years"
                    student["age_sort_key"] = age_years
                    student["days"] = f"{extra_days} days"
                    student["dob_display"] = dob.strftime("%d %B %Y")
                elif dob and dob > today:
                    student["age"] = "Future date"
                    student["age_sort_key"] = 999999
                    student["days"] = "?"
                    student["dob_display"] = dob.strftime("%d %B %Y")
                else:
                    student["age"] = "Not Provided"
                    student["age_sort_key"] = 999999
                    student["days"] = "?"
                    student["dob_display"] = "Not Provided"

                student["full_name"] = (
                    f"{student.get('first_name') or ''} "
                    f"{student.get('last_name') or ''}"
                ).strip() or "Name not provided"

                students.append(student)

        return render_template(
            "students_age.html",
            students=students,
            academic_years=academic_years,
            selected_year=selected_year,
            school_settings=school_settings,
            today=today.strftime("%d %B %Y")
        )
    finally:
        connection.close()

@app.route("/students-by-photo")
def students_by_photo():
    if "user_id" not in session:
        return redirect("/")

    connection = get_connection()
    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT id, year_name, is_active
            FROM academic_years
            ORDER BY id DESC
        """)
        academic_years = cursor.fetchall()

        cursor.execute("""
            SELECT id, class_name
            FROM classes
            ORDER BY class_name
        """)
        classes = cursor.fetchall()

        cursor.execute("""
            SELECT *
            FROM school_settings
            ORDER BY id DESC
            LIMIT 1
        """)
        school_settings = cursor.fetchone()

        selected_year = request.args.get("academic_year_id", "").strip()
        selected_class = request.args.get("class_id", "").strip()
        selected_status = request.args.get("status", "active").strip().lower()
        search = request.args.get("search", "").strip()

        if not selected_year:
            active_year = next(
                (y for y in academic_years
                 if "is_active" in y.keys() and y["is_active"]),
                None
            )
            if active_year:
                selected_year = str(active_year["id"])
            elif academic_years:
                selected_year = str(academic_years[0]["id"])

        if selected_status not in ("active", "inactive", "all"):
            selected_status = "active"

        students = []
        if selected_year.isdigit():
            cursor.execute("""
                SELECT
                    s.id,
                    s.student_id,
                    s.first_name,
                    s.last_name,
                    s.gender,
                    s.photo,
                    s.status,
                    c.class_name,
                    ay.year_name
                FROM students s
                LEFT JOIN student_class_placements p
                    ON p.student_db_id = s.id
                    AND p.academic_year_id = ?
                LEFT JOIN classes c
                    ON c.id = COALESCE(p.class_id, s.class_id)
                LEFT JOIN academic_years ay
                    ON ay.id = s.academic_year_id
                WHERE s.academic_year_id = ?
                  AND (
                      ? = ''
                      OR CAST(COALESCE(p.class_id, s.class_id) AS TEXT) = ?
                  )
                  AND (
                      ? = 'all'
                      OR LOWER(COALESCE(s.status, 'active')) = ?
                  )
                  AND (
                      ? = ''
                      OR COALESCE(s.first_name, '') LIKE ?
                      OR COALESCE(s.last_name, '') LIKE ?
                      OR COALESCE(s.student_id, '') LIKE ?
                  )
                ORDER BY COALESCE(c.class_name, 'Unassigned'),
                         s.first_name, s.last_name
            """, (
                int(selected_year),
                int(selected_year),
                selected_class, selected_class,
                selected_status, selected_status,
                search,
                "%" + search + "%",
                "%" + search + "%",
                "%" + search + "%"
            ))
            students = cursor.fetchall()

        return render_template(
            "students_by_photo.html",
            students=students,
            classes=classes,
            academic_years=academic_years,
            school_settings=school_settings,
            selected_year=selected_year,
            selected_class=selected_class,
            selected_status=selected_status,
            search=search
        )
    finally:
        connection.close()



# PARENTS REGISTER
# ==================================================

@app.route("/parents-register")
def parents_register():
    if "user_id" not in session:
        return redirect("/")

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            SELECT id, year_name
            FROM academic_years
            ORDER BY id DESC
        """)
        academic_years = cursor.fetchall()

        selected_year = request.args.get("academic_year_id", "").strip()
        search = request.args.get("search", "").strip()

        if not selected_year:
            active_year = next(
                (year for year in academic_years
                 if "is_active" in year.keys() and year["is_active"]),
                None
            )
            if active_year:
                selected_year = str(active_year["id"])

        selected_year_name = ""
        for year in academic_years:
            if str(year["id"]) == selected_year:
                selected_year_name = year["year_name"]
                break

        cursor.execute("""
            SELECT *
            FROM school_settings
            ORDER BY id DESC
            LIMIT 1
        """)
        school_settings = cursor.fetchone()

        parents = []

        if selected_year.isdigit():
            term = "%" + search + "%"
            cursor.execute("""
                SELECT
                    s.parent_name,
                    s.parent_phone,
                    s.first_name,
                    s.last_name,
                    s.student_id,
                    c.class_name
                FROM students s
                LEFT JOIN student_class_placements p
                    ON p.student_db_id = s.id
                    AND p.academic_year_id = s.academic_year_id
                LEFT JOIN classes c
                    ON c.id = COALESCE(p.class_id, s.class_id)
                WHERE s.academic_year_id = ?
                  AND (
                      COALESCE(s.parent_name, '') <> ''
                      OR COALESCE(s.parent_phone, '') <> ''
                  )
                  AND (
                      ? = ''
                      OR COALESCE(s.parent_name, '') LIKE ?
                      OR COALESCE(s.parent_phone, '') LIKE ?
                      OR COALESCE(s.first_name, '') LIKE ?
                      OR COALESCE(s.last_name, '') LIKE ?
                      OR COALESCE(s.student_id, '') LIKE ?
                  )
                ORDER BY c.class_name, s.first_name, s.last_name
            """, (
                int(selected_year),
                search,
                term, term, term, term, term
            ))
            parents = cursor.fetchall()

    finally:
        connection.close()

    return render_template(
        "parents_register.html",
        parents=parents,
        academic_years=academic_years,
        selected_year=selected_year,
        selected_year_name=selected_year_name,
        school_settings=school_settings,
        search=search
    )


# STUDENT PROFILE
# ==================================================

@app.route(
    "/students/profile/<int:student_id>"
)
def student_profile(student_id):

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            students.*,
            classes.class_name,
            academic_years.year_name

        FROM students

        LEFT JOIN classes
            ON students.class_id = classes.id

        LEFT JOIN academic_years
            ON students.academic_year_id =
               academic_years.id

        WHERE students.id = ?
        """,
        (student_id,)
    )

    student = cursor.fetchone()

    connection.close()

    if not student:

        return "Student not found"

    return render_template(
        "student_profile.html",
        student=student
    )


# ==================================================
# EDIT STUDENT
# ==================================================

@app.route(
    "/students/edit/<int:student_id>",
    methods=["GET", "POST"]
)
def edit_student(student_id):

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    if request.method == "POST":

        first_name = request.form.get(
            "first_name",
            ""
        )

        last_name = request.form.get(
            "last_name",
            ""
        )

        date_of_birth = request.form.get(
            "date_of_birth",
            ""
        )

        gender = request.form.get(
            "gender",
            ""
        )

        class_id = request.form.get(
            "class_id",
            ""
        )

        parent_name = request.form.get(
            "parent_name",
            ""
        )

        parent_phone = request.form.get(
            "parent_phone",
            ""
        )
        student_address = request.form.get("student_address", "").strip()
        parent_address = request.form.get("parent_address", "").strip()
        parent_relationship = request.form.get("parent_relationship", "").strip()
        last_school_attended = request.form.get("last_school_attended", "").strip()
        previous_school_location = request.form.get("previous_school_location", "").strip()
        previous_principal_phone = request.form.get("previous_principal_phone", "").strip()

        admission_date = request.form.get(
            "admission_date",
            ""
        )

        academic_year_id = request.form.get(
            "academic_year_id",
            ""
        )

        status = request.form.get(
            "status",
            ""
        )

        cursor.execute(
            """
            SELECT photo
            FROM students
            WHERE id = ?
            """,
            (student_id,)
        )

        current_student = cursor.fetchone()

        photo_filename = (
            current_student["photo"]
            if current_student
            else None
        )

        photo = request.files.get(
            "photo"
        )

        if photo and photo.filename:

            if allowed_file(
                photo.filename
            ):

                extension = (
                    photo.filename
                    .rsplit(".", 1)[1]
                    .lower()
                )

                new_photo_filename = (
                    str(uuid.uuid4())
                    + "."
                    + extension
                )

                photo.save(
                    os.path.join(
                        app.config[
                            "UPLOAD_FOLDER"
                        ],
                        new_photo_filename
                    )
                )

                if photo_filename:

                    old_photo_path = os.path.join(
                        app.config[
                            "UPLOAD_FOLDER"
                        ],
                        photo_filename
                    )

                    if os.path.exists(
                        old_photo_path
                    ):

                        os.remove(
                            old_photo_path
                        )

                photo_filename = (
                    new_photo_filename
                )

        cursor.execute(
            """
            UPDATE students

            SET
                first_name = ?,
                last_name = ?,
                date_of_birth = ?,
                gender = ?,
                class_id = ?,
                parent_name = ?,
                parent_phone = ?,
                student_address = ?,
                parent_address = ?,
                parent_relationship = ?,
                last_school_attended = ?,
                previous_school_location = ?,
                previous_principal_phone = ?,
                admission_date = ?,
                academic_year_id = ?,
                status = ?,
                photo = ?

            WHERE id = ?
            """,
            (
                first_name,
                last_name,
                date_of_birth,
                gender,
                class_id,
                parent_name,
                parent_phone,
                student_address,
                parent_address,
                parent_relationship,
                last_school_attended,
                previous_school_location,
                previous_principal_phone,
                admission_date,
                academic_year_id,
                status,
                photo_filename,
                student_id
            )
        )

        connection.commit()

        connection.close()

        return redirect(
            "/students/profile/"
            + str(student_id)
        )

    cursor.execute(
        """
        SELECT *
        FROM students
        WHERE id = ?
        """,
        (student_id,)
    )

    student = cursor.fetchone()

    cursor.execute(
        """
        SELECT *
        FROM classes
        ORDER BY class_name
        """
    )

    classes_list = cursor.fetchall()

    cursor.execute(
        """
        SELECT *
        FROM academic_years
        ORDER BY id DESC
        """
    )

    academic_years_list = cursor.fetchall()

    connection.close()

    if not student:

        return "Student not found"

    return render_template(
        "edit_student.html",
        student=student,
        classes=classes_list,
        academic_years=academic_years_list
    )


# ==================================================
# DELETE STUDENT
# ==================================================

@app.route(
    "/students/delete/<int:student_id>"
)
def delete_student(student_id):

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT photo
        FROM students
        WHERE id = ?
        """,
        (student_id,)
    )

    student = cursor.fetchone()

    if student:

        if student["photo"]:

            photo_path = os.path.join(
                app.config[
                    "UPLOAD_FOLDER"
                ],
                student["photo"]
            )

            if os.path.exists(
                photo_path
            ):

                os.remove(
                    photo_path
                )

        cursor.execute(
            """
            DELETE FROM students
            WHERE id = ?
            """,
            (student_id,)
        )

        connection.commit()

    connection.close()

    return redirect(
        "/students"
    )


# ==================================================
# TEACHERS
# ==================================================

@app.route(
    "/teachers",
    methods=["GET", "POST"]
)
def teachers():

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    # ==============================================
    # GENERATE NEXT TEACHER ID
    # ==============================================

    cursor.execute(
        """
        SELECT teacher_id
        FROM teachers
        WHERE teacher_id LIKE 'TEA%'
        ORDER BY id DESC
        LIMIT 1
        """
    )

    last_teacher = cursor.fetchone()

    if last_teacher:

        try:

            last_number = int(
                last_teacher["teacher_id"][3:]
            )

            next_number = last_number + 1

        except ValueError:

            next_number = 1

    else:

        next_number = 1

    next_teacher_id = (
        f"TEA{next_number:03d}"
    )

    # ==============================================
    # REGISTER TEACHER
    # ==============================================

    if request.method == "POST":

        first_name = request.form.get(
            "first_name",
            ""
        )

        last_name = request.form.get(
            "last_name",
            ""
        )

        date_of_birth = request.form.get(
            "date_of_birth",
            ""
        )

        gender = request.form.get(
            "gender",
            ""
        )

        phone = request.form.get(
            "phone",
            ""
        )

        email = request.form.get(
            "email",
            ""
        )

        address = request.form.get(
            "address",
            ""
        )

        subject = request.form.get(
            "subject",
            ""
        )

        position = request.form.get(
            "position",
            ""
        )

        employment_date = request.form.get(
            "employment_date",
            ""
        )

        salary_rate = request.form.get(
            "salary_rate",
            "0"
        )

        status = request.form.get(
            "status",
            ""
        )

        # ==========================================
        # TEACHER PHOTO
        # ==========================================

        photo_filename = None

        photo = request.files.get(
            "photo"
        )

        if photo and photo.filename:

            if allowed_file(
                photo.filename
            ):

                extension = (
                    photo.filename
                    .rsplit(".", 1)[1]
                    .lower()
                )

                photo_filename = (
                    str(uuid.uuid4())
                    + "."
                    + extension
                )

                photo.save(
                    os.path.join(
                        app.config[
                            "UPLOAD_FOLDER"
                        ],
                        photo_filename
                    )
                )

        # ==========================================
        # SAVE TEACHER
        # ==========================================

        cursor.execute(
            """
            INSERT INTO teachers (

                teacher_id,
                first_name,
                last_name,
                date_of_birth,
                gender,
                phone,
                email,
                address,
                subject,
                position,
                employment_date,
                salary_rate,
                status,
                photo

            )

            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                next_teacher_id,
                first_name,
                last_name,
                date_of_birth,
                gender,
                phone,
                email,
                address,
                subject,
                position,
                employment_date,
                salary_rate,
                status,
                photo_filename
            )
        )

        connection.commit()

        connection.close()

        return redirect(
            "/teachers"
        )

    # ==============================================
    # TEACHER SEARCH
    # ==============================================

    search = request.args.get(
        "search",
        ""
    ).strip()

    if search:

        search_value = "%" + search + "%"

        cursor.execute(
            """
            SELECT *
            FROM teachers

            WHERE
                teacher_id LIKE ?
                OR first_name LIKE ?
                OR last_name LIKE ?
                OR phone LIKE ?
                OR subject LIKE ?
                OR position LIKE ?

            ORDER BY id DESC
            """,
            (
                search_value,
                search_value,
                search_value,
                search_value,
                search_value,
                search_value
            )
        )

    else:

        cursor.execute(
            """
            SELECT *
            FROM teachers
            ORDER BY id DESC
            """
        )

    teachers_list = cursor.fetchall()

    connection.close()

    return render_template(
        "teachers.html",
        teachers=teachers_list,
        next_teacher_id=next_teacher_id,
        search=search
    )


# ==================================================
# TEACHER PROFILE
# ==================================================

@app.route(
    "/teachers/profile/<int:teacher_id>"
)
def teacher_profile(teacher_id):

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM teachers
        WHERE id = ?
        """,
        (teacher_id,)
    )

    teacher = cursor.fetchone()

    connection.close()

    if not teacher:

        return "Teacher not found"

    return render_template(
        "teacher_profile.html",
        teacher=teacher
    )


# ==================================================
# EDIT TEACHER
# ==================================================

@app.route(
    "/teachers/edit/<int:teacher_id>",
    methods=["GET", "POST"]
)
def edit_teacher(teacher_id):

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    if request.method == "POST":

        first_name = request.form.get(
            "first_name",
            ""
        )

        last_name = request.form.get(
            "last_name",
            ""
        )

        date_of_birth = request.form.get(
            "date_of_birth",
            ""
        )

        gender = request.form.get(
            "gender",
            ""
        )

        phone = request.form.get(
            "phone",
            ""
        )

        email = request.form.get(
            "email",
            ""
        )

        address = request.form.get(
            "address",
            ""
        )

        subject = request.form.get(
            "subject",
            ""
        )

        position = request.form.get(
            "position",
            ""
        )

        employment_date = request.form.get(
            "employment_date",
            ""
        )

        salary_rate = request.form.get(
            "salary_rate",
            "0"
        )

        status = request.form.get(
            "status",
            ""
        )

        # ==========================================
        # GET CURRENT PHOTO
        # ==========================================

        cursor.execute(
            """
            SELECT photo
            FROM teachers
            WHERE id = ?
            """,
            (teacher_id,)
        )

        current_teacher = cursor.fetchone()

        photo_filename = (
            current_teacher["photo"]
            if current_teacher
            else None
        )

        # ==========================================
        # HANDLE NEW PHOTO
        # ==========================================

        photo = request.files.get(
            "photo"
        )

        if photo and photo.filename:

            if allowed_file(
                photo.filename
            ):

                extension = (
                    photo.filename
                    .rsplit(".", 1)[1]
                    .lower()
                )

                new_photo_filename = (
                    str(uuid.uuid4())
                    + "."
                    + extension
                )

                photo.save(
                    os.path.join(
                        app.config[
                            "UPLOAD_FOLDER"
                        ],
                        new_photo_filename
                    )
                )

                if photo_filename:

                    old_photo_path = os.path.join(
                        app.config[
                            "UPLOAD_FOLDER"
                        ],
                        photo_filename
                    )

                    if os.path.exists(
                        old_photo_path
                    ):

                        os.remove(
                            old_photo_path
                        )

                photo_filename = (
                    new_photo_filename
                )

        # ==========================================
        # UPDATE TEACHER
        # ==========================================

        cursor.execute(
            """
            UPDATE teachers

            SET
                first_name = ?,
                last_name = ?,
                date_of_birth = ?,
                gender = ?,
                phone = ?,
                email = ?,
                address = ?,
                subject = ?,
                position = ?,
                employment_date = ?,
                salary_rate = ?,
                status = ?,
                photo = ?

            WHERE id = ?
            """,
            (
                first_name,
                last_name,
                date_of_birth,
                gender,
                phone,
                email,
                address,
                subject,
                position,
                employment_date,
                salary_rate,
                status,
                photo_filename,
                teacher_id
            )
        )

        connection.commit()

        connection.close()

        return redirect(
            "/teachers/profile/"
            + str(teacher_id)
        )

    cursor.execute(
        """
        SELECT *
        FROM teachers
        WHERE id = ?
        """,
        (teacher_id,)
    )

    teacher = cursor.fetchone()

    connection.close()

    if not teacher:

        return "Teacher not found"

    return render_template(
        "edit_teacher.html",
        teacher=teacher
    )


# ==================================================
# DELETE TEACHER
# ==================================================

@app.route(
    "/teachers/delete/<int:teacher_id>"
)
def delete_teacher(teacher_id):

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT photo
        FROM teachers
        WHERE id = ?
        """,
        (teacher_id,)
    )

    teacher = cursor.fetchone()

    if teacher:

        if teacher["photo"]:

            photo_path = os.path.join(
                app.config[
                    "UPLOAD_FOLDER"
                ],
                teacher["photo"]
            )

            if os.path.exists(
                photo_path
            ):

                os.remove(
                    photo_path
                )

        cursor.execute(
            """
            DELETE FROM teachers
            WHERE id = ?
            """,
            (teacher_id,)
        )

        connection.commit()

    connection.close()

    return redirect(
        "/teachers"
    )


# ==================================================
# TEACHER ATTENDANCE
# ==================================================

@app.route(
    "/attendance",
    methods=["GET", "POST"]
)
def attendance():

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    # ==============================================
    # SAVE ATTENDANCE
    # ==============================================

    if request.method == "POST":

        teacher_id = request.form.get(
            "teacher_id"
        )

        academic_year_id = request.form.get(
            "academic_year_id"
        )

        period = request.form.get(
            "period"
        )

        attendance_date = request.form.get(
            "attendance_date"
        )

        status = request.form.get(
            "status"
        )

        remarks = request.form.get(
            "remarks",
            ""
        )

        time_recorded = datetime.now().strftime(
            "%H:%M:%S"
        )

        cursor.execute(
            """
            INSERT INTO teacher_attendance (

                teacher_id,
                academic_year_id,
                period,
                attendance_date,
                time_recorded,
                status,
                remarks

            )

            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                teacher_id,
                academic_year_id,
                period,
                attendance_date,
                time_recorded,
                status,
                remarks
            )
        )

        connection.commit()

        try:
            from modules.notifications import create_notification
            sid = session.get("school_id")
            if sid is not None:
                teacher_row = connection.execute(
                    "SELECT first_name, last_name FROM teachers WHERE id = ?",
                    (teacher_id,)
                ).fetchone()
                teacher_name = (
                    f"{teacher_row['first_name']} {teacher_row['last_name']}"
                    if teacher_row else f"Teacher ID {teacher_id}"
                )
                create_notification(
                    connection, sid, "teacher_attendance",
                    "Teacher Attendance Recorded",
                    f"Attendance recorded for {teacher_name} on {attendance_date}: {status}.",
                    related_name=teacher_name
                )
        except Exception as notification_error:
            print("Teacher attendance notification could not be created:", notification_error)

    # ==============================================
    # GET ACTIVE TEACHERS
    # ==============================================

    cursor.execute(
        """
        SELECT *
        FROM teachers
        WHERE status = 'Active'
        ORDER BY first_name, last_name
        """
    )

    teachers_list = cursor.fetchall()

    # ==============================================
    # GET ACADEMIC YEARS
    # ==============================================

    cursor.execute(
        """
        SELECT *
        FROM academic_years
        ORDER BY id DESC
        """
    )

    academic_years_list = cursor.fetchall()

    # ==============================================
    # GET ATTENDANCE RECORDS
    # ==============================================

    cursor.execute(
        """
        SELECT
            teacher_attendance.id,
            teachers.teacher_id,
            teachers.first_name,
            teachers.last_name,
            academic_years.year_name,
            teacher_attendance.period,
            teacher_attendance.attendance_date,
            teacher_attendance.time_recorded,
            teacher_attendance.status,
            teacher_attendance.remarks

        FROM teacher_attendance

        JOIN teachers
            ON teacher_attendance.teacher_id =
               teachers.id

        LEFT JOIN academic_years
            ON teacher_attendance.academic_year_id =
               academic_years.id

        ORDER BY
            teacher_attendance.attendance_date DESC,
            teacher_attendance.id DESC
        """
    )

    attendance_records = cursor.fetchall()

    connection.close()

    return render_template(
        "attendance.html",
        teachers=teachers_list,
        academic_years=academic_years_list,
        attendance_records=attendance_records
    )


# ==================================================
# EDIT TEACHER ATTENDANCE
# ==================================================

@app.route(
    "/attendance/edit/<int:attendance_id>",
    methods=["GET", "POST"]
)
def edit_attendance(attendance_id):

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    if request.method == "POST":

        teacher_id = request.form.get(
            "teacher_id"
        )

        academic_year_id = request.form.get(
            "academic_year_id"
        )

        period = request.form.get(
            "period"
        )

        attendance_date = request.form.get(
            "attendance_date"
        )

        status = request.form.get(
            "status"
        )

        remarks = request.form.get(
            "remarks",
            ""
        )

        cursor.execute(
            """
            UPDATE teacher_attendance

            SET
                teacher_id = ?,
                academic_year_id = ?,
                period = ?,
                attendance_date = ?,
                status = ?,
                remarks = ?

            WHERE id = ?
            """,
            (
                teacher_id,
                academic_year_id,
                period,
                attendance_date,
                status,
                remarks,
                attendance_id
            )
        )

        connection.commit()

        connection.close()

        return redirect(
            "/attendance"
        )

    cursor.execute(
        """
        SELECT *
        FROM teacher_attendance
        WHERE id = ?
        """,
        (attendance_id,)
    )

    attendance_record = cursor.fetchone()

    cursor.execute(
        """
        SELECT *
        FROM teachers
        WHERE status = 'Active'
        ORDER BY first_name, last_name
        """
    )

    teachers_list = cursor.fetchall()

    cursor.execute(
        """
        SELECT *
        FROM academic_years
        ORDER BY id DESC
        """
    )

    academic_years_list = cursor.fetchall()

    connection.close()

    if not attendance_record:

        return "Attendance record not found"

    return render_template(
        "edit_attendance.html",
        attendance=attendance_record,
        teachers=teachers_list,
        academic_years=academic_years_list
    )


# ==================================================
# DELETE TEACHER ATTENDANCE
# ==================================================

@app.route(
    "/attendance/delete/<int:attendance_id>"
)
def delete_attendance(attendance_id):

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM teacher_attendance
        WHERE id = ?
        """,
        (attendance_id,)
    )

    connection.commit()

    connection.close()

    return redirect(
        "/attendance"
    )


# ==================================================
# AUTOMATIC SALARY ATTENDANCE CALCULATION
# ==================================================

@app.route(
    "/salary-attendance"
)
def salary_attendance():

    if "user_id" not in session:

        return {
            "success": False,
            "message": "Not logged in"
        }, 401

    teacher_id = request.args.get(
        "teacher_id"
    )

    salary_month = request.args.get(
        "salary_month"
    )

    if not teacher_id or not salary_month:

        return {
            "success": False,
            "days_present": 0,
            "days_absent": 0,
            "leave_days": 0,
            "payable_days": 0,
            "amount_due": 0
        }

    connection = get_connection()

    cursor = connection.cursor()

    # ==============================================
    # GET TEACHER DAILY RATE
    # ==============================================

    cursor.execute(
        """
        SELECT salary_rate
        FROM teachers
        WHERE id = ?
        """,
        (teacher_id,)
    )

    teacher = cursor.fetchone()

    if not teacher:

        connection.close()

        return {
            "success": False,
            "message": "Teacher not found"
        }, 404

    try:

        daily_rate = float(
            teacher["salary_rate"] or 0
        )

    except (ValueError, TypeError):

        daily_rate = 0

    # ==============================================
    # GET UNPAID MONTHLY ATTENDANCE
    # ==============================================

    cursor.execute(
        """
        SELECT ta.id, ta.status
        FROM teacher_attendance ta
        LEFT JOIN teacher_salary_attendance tsa
          ON tsa.attendance_id = ta.id
        WHERE ta.teacher_id = ?
          AND substr(ta.attendance_date, 1, 7) = ?
          AND tsa.attendance_id IS NULL
        ORDER BY ta.attendance_date, ta.id
        """,
        (teacher_id, salary_month)
    )

    available_attendance = cursor.fetchall()
    payable_attendance = [
        row for row in available_attendance
        if row["status"] in ("Present", "Leave")
    ][:26]

    days_present = sum(
        1 for row in payable_attendance if row["status"] == "Present"
    )
    leave_days = sum(
        1 for row in payable_attendance if row["status"] == "Leave"
    )
    days_absent = sum(
        1 for row in available_attendance if row["status"] == "Absent"
    )
    payable_days = len(payable_attendance)
    amount_due = payable_days * daily_rate

    connection.close()

    return {
        "success": True,
        "daily_rate": round(
            daily_rate,
            2
        ),
        "days_present": days_present,
        "days_absent": days_absent,
        "leave_days": leave_days,
        "payable_days": payable_days,
        "amount_due": round(
            amount_due,
            2
        )
    }


# ==================================================
# TEACHER SALARIES
# ==================================================

@app.route(
    "/salaries",
    methods=["GET", "POST"]
)
def salaries():

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    # ==============================================
    # SAVE SALARY
    # ==============================================

    if request.method == "POST":

        teacher_id = request.form[
            "teacher_id"
        ]

        salary_month = request.form[
            "salary_month"
        ]

        payment_status = request.form[
            "payment_status"
        ]

        payment_date = request.form.get(
            "payment_date"
        )

        remarks = request.form.get(
            "remarks"
        )

        # ==========================================
        # GET DAILY RATE
        # ==========================================

        cursor.execute(
            """
            SELECT salary_rate
            FROM teachers
            WHERE id = ?
            """,
            (teacher_id,)
        )

        teacher = cursor.fetchone()

        if not teacher:

            connection.close()

            return "Teacher not found"

        try:

            daily_fee = float(
                teacher["salary_rate"] or 0
            )

        except (ValueError, TypeError):

            daily_fee = 0

        # ==========================================
        # GET ATTENDANCE
        # ==========================================

        cursor.execute(
            """
            SELECT ta.id, ta.status
            FROM teacher_attendance ta
            LEFT JOIN teacher_salary_attendance tsa
              ON tsa.attendance_id = ta.id
            WHERE ta.teacher_id = ?
              AND substr(ta.attendance_date, 1, 7) = ?
              AND tsa.attendance_id IS NULL
            ORDER BY ta.attendance_date, ta.id
            """,
            (teacher_id, salary_month)
        )

        available_attendance = cursor.fetchall()
        payable_attendance = [
            row for row in available_attendance
            if row["status"] in ("Present", "Leave")
        ][:26]
        days_present = sum(
            1 for row in payable_attendance if row["status"] == "Present"
        )
        leave_days = sum(
            1 for row in payable_attendance if row["status"] == "Leave"
        )
        days_absent = sum(
            1 for row in available_attendance if row["status"] == "Absent"
        )
        payable_days = len(payable_attendance)
        amount_due = payable_days * daily_fee

        # ==========================================
        # AMOUNT ACTUALLY PAID
        # ==========================================

        try:

            amount_paid = float(
                request.form.get(
                    "amount_paid",
                    0
                ) or 0
            )

        except (ValueError, TypeError):

            amount_paid = 0

        # ==========================================
        # AUTOMATIC TIME RECEIVED
        # ==========================================

        time_received = datetime.now().strftime(
            "%H:%M:%S"
        )

        # ==========================================
        # SAVE SALARY RECORD
        # ==========================================

        cursor.execute(
            """
            INSERT INTO teacher_salaries (

                teacher_id,
                salary_month,
                salary_rate,
                days_present,
                days_absent,
                leave_days,
                amount_paid,
                payment_status,
                payment_date,
                remarks,
                time_received,
                attendance_tracking_enabled

            )

            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                teacher_id,
                salary_month,
                daily_fee,
                days_present,
                days_absent,
                leave_days,
                amount_paid,
                payment_status,
                payment_date,
                remarks,
                time_received,
                1
            )
        )

        salary_id = cursor.lastrowid

        if payment_status == "Paid":
            for attendance_row in payable_attendance:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO teacher_salary_attendance
                        (salary_id, attendance_id, teacher_id, salary_month)
                    VALUES (?, ?, ?, ?)
                    """,
                    (salary_id, attendance_row["id"], teacher_id, salary_month)
                )

        connection.commit()

        connection.close()

        return redirect(
            "/salaries"
        )

    # ==============================================
    # GET ACTIVE TEACHERS
    # ==============================================

    cursor.execute(
        """
        SELECT *
        FROM teachers
        WHERE status = 'Active'
        ORDER BY first_name, last_name
        """
    )

    teachers_list = cursor.fetchall()

    # ==============================================
    # GET SALARY RECORDS
    # ==============================================

    cursor.execute(
        """
        SELECT

            teacher_salaries.id,

            teachers.teacher_id,

            teachers.first_name,

            teachers.last_name,

            teacher_salaries.salary_month,

            teacher_salaries.salary_rate,

            teacher_salaries.days_present,

            teacher_salaries.days_absent,

            teacher_salaries.leave_days,

            teacher_salaries.amount_paid,

            teacher_salaries.payment_status,

            teacher_salaries.payment_date,

            teacher_salaries.time_received,

            teacher_salaries.remarks

        FROM teacher_salaries

        JOIN teachers
            ON teacher_salaries.teacher_id =
               teachers.id

        ORDER BY
            teacher_salaries.salary_month DESC,
            teacher_salaries.id DESC
        """
    )

    salary_records = cursor.fetchall()

    connection.close()

    return render_template(
        "salaries.html",
        teachers=teachers_list,
        salary_records=salary_records
    )


# ==================================================
# EDIT TEACHER SALARY
# ==================================================

@app.route(
    "/salaries/edit/<int:salary_id>",
    methods=["GET", "POST"]
)
def edit_salary(salary_id):

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    ensure_salary_tracking_schema(connection)

    cursor.execute(
        """
        SELECT teacher_id, salary_rate, time_received,
               payment_status, salary_month, attendance_tracking_enabled
        FROM teacher_salaries
        WHERE id = ?
        """,
        (salary_id,)
    )
    existing_salary = cursor.fetchone()

    if not existing_salary:
        connection.close()
        return "Salary record not found"

    teacher_id = existing_salary["teacher_id"]
    daily_fee = existing_salary["salary_rate"]
    time_received = existing_salary["time_received"]

    if request.method == "POST":

        salary_month = request.form.get(
            "salary_month"
        )

        payment_status = request.form.get(
            "payment_status"
        )

        payment_date = request.form.get(
            "payment_date"
        )

        remarks = request.form.get(
            "remarks"
        )

        try:

            amount_paid = float(
                request.form.get(
                    "amount_paid",
                    0
                ) or 0
            )

        except (ValueError, TypeError):

            amount_paid = 0

        cursor.execute(
            """
            SELECT ta.id, ta.status
            FROM teacher_attendance ta
            LEFT JOIN teacher_salary_attendance tsa
              ON tsa.attendance_id = ta.id
            WHERE ta.teacher_id = ?
              AND substr(ta.attendance_date, 1, 7) = ?
              AND tsa.attendance_id IS NULL
            ORDER BY ta.attendance_date, ta.id
            """,
            (teacher_id, salary_month)
        )

        available_attendance = cursor.fetchall()
        payable_attendance = [
            row for row in available_attendance
            if row["status"] in ("Present", "Leave")
        ][:26]

        days_present = sum(
            1 for row in payable_attendance if row["status"] == "Present"
        )
        leave_days = sum(
            1 for row in payable_attendance if row["status"] == "Leave"
        )
        days_absent = sum(
            1 for row in available_attendance if row["status"] == "Absent"
        )

        cursor.execute(
            """
            UPDATE teacher_salaries

            SET
                salary_month = ?,
                salary_rate = ?,
                days_present = ?,
                days_absent = ?,
                leave_days = ?,
                amount_paid = ?,
                payment_status = ?,
                payment_date = ?,
                time_received = ?,
                remarks = ?

            WHERE id = ?
            """,
            (
                salary_month,
                daily_fee,
                days_present,
                days_absent,
                leave_days,
                amount_paid,
                payment_status,
                payment_date,
                time_received,
                remarks,
                salary_id
            )
        )

        if (
            payment_status == "Paid"
            and existing_salary["attendance_tracking_enabled"] == 1
        ):
            for attendance_row in payable_attendance:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO teacher_salary_attendance
                        (salary_id, attendance_id, teacher_id, salary_month)
                    VALUES (?, ?, ?, ?)
                    """,
                    (salary_id, attendance_row["id"], teacher_id, salary_month)
                )

        connection.commit()

        connection.close()

        return redirect(
            "/salaries"
        )

    cursor.execute(
        """
        SELECT

            teacher_salaries.*,

            teachers.teacher_id,
            teachers.first_name,
            teachers.last_name

        FROM teacher_salaries

        JOIN teachers
            ON teacher_salaries.teacher_id =
               teachers.id

        WHERE teacher_salaries.id = ?
        """,
        (salary_id,)
    )

    salary = cursor.fetchone()

    connection.close()

    if not salary:

        return "Salary record not found"

    return render_template(
        "edit_salary.html",
        salary=salary
    )


# ==================================================
# DELETE TEACHER SALARY
# ==================================================

@app.route(
    "/salaries/delete/<int:salary_id>"
)
def delete_salary(salary_id):

    if "user_id" not in session:

        return redirect("/")

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM teacher_salaries
        WHERE id = ?
        """,
        (salary_id,)
    )

    connection.commit()

    connection.close()

    return redirect(
        "/salaries"
    )


# ==================================================
# LOGOUT
# ==================================================


@app.route("/salary-records-report", methods=["GET"])
def teacher_salary_records_report():
    if "user_id" not in session:
        return redirect("/")

    connection = get_connection()
    try:
        # Read the school information saved in Settings for this school.
        settings_row = connection.execute("""
            SELECT * FROM school_settings
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        school = dict(settings_row) if settings_row else {}

        salary_columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(teacher_salaries)"
            ).fetchall()
        }
        teacher_columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(teachers)"
            ).fetchall()
        }

        required = {
            "teacher_id", "salary_month", "salary_rate",
            "days_present", "days_absent", "leave_days",
            "amount_paid", "payment_status"
        }
        if not required.issubset(salary_columns):
            return (
                "The salary table is missing required fields: "
                + ", ".join(sorted(required - salary_columns)),
                500
            )

        month = request.args.get("month", "").strip()
        status = request.args.get("status", "").strip()

        name_parts = []
        if "first_name" in teacher_columns:
            name_parts.append("COALESCE(t.first_name, '')")
        if "last_name" in teacher_columns:
            name_parts.append("COALESCE(t.last_name, '')")
        teacher_name = " || ' ' || ".join(name_parts) if name_parts else "''"

        teacher_code = (
            "t.teacher_id" if "teacher_id" in teacher_columns
            else "CAST(t.id AS TEXT)"
        )

        fields = [
            "s.id", "s.salary_month", "s.salary_rate",
            "s.days_present", "s.days_absent", "s.leave_days",
            "s.amount_paid", "s.payment_status",
            f"{teacher_name} AS teacher_name",
            f"{teacher_code} AS teacher_code"
        ]
        for col in ("payment_date", "remarks", "time_received"):
            if col in salary_columns:
                fields.append(f"s.{col}")

        sql = """
            SELECT """ + ", ".join(fields) + """
            FROM teacher_salaries s
            JOIN teachers t ON s.teacher_id = t.id
            WHERE 1=1
        """
        params = []
        if month:
            sql += " AND s.salary_month = ?"
            params.append(month)
        if status:
            sql += " AND s.payment_status = ?"
            params.append(status)

        sql += " ORDER BY s.salary_month DESC, teacher_name COLLATE NOCASE"

        records = [
            dict(row) for row in connection.execute(sql, params).fetchall()
        ]

        total_paid = sum(float(r.get("amount_paid") or 0) for r in records)
        total_present = sum(int(r.get("days_present") or 0) for r in records)
        total_leave = sum(int(r.get("leave_days") or 0) for r in records)
        total_absent = sum(int(r.get("days_absent") or 0) for r in records)

        return render_template(
            "teacher_salary_records_report.html",
            school=school,
            records=records,
            month=month,
            status=status,
            total_paid=total_paid,
            total_present=total_present,
            total_leave=total_leave,
            total_absent=total_absent
        )
    finally:
        connection.close()


@app.route("/settings", methods=["GET", "POST"])
def settings():

    if not login_required():
        return redirect("/")

    conn = get_connection()

    settings_data = conn.execute("""
        SELECT *
        FROM school_settings
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()

    if request.method == "POST":

        school_name = request.form.get(
            "school_name",
            ""
        ).strip()

        school_address = request.form.get(
            "school_address",
            ""
        ).strip()

        school_phone = request.form.get(
            "school_phone",
            ""
        ).strip()

        school_email = request.form.get(
            "school_email",
            ""
        ).strip()

        principal_name = request.form.get(
            "principal_name",
            ""
        ).strip()

        school_motto = request.form.get(
            "school_motto",
            ""
        ).strip()

        logo_filename = (
            settings_data["school_logo"]
            if settings_data
            else None
        )

        logo = request.files.get("school_logo")

        if logo and logo.filename:

            if allowed_file(logo.filename):

                extension = logo.filename.rsplit(
                    ".",
                    1
                )[1].lower()

                logo_filename = (
                    f"logo_{uuid.uuid4().hex}.{extension}"
                )

                logo.save(
                    os.path.join(
                        app.config["UPLOAD_FOLDER"],
                        logo_filename
                    )
                )

        now = datetime.now().isoformat()

        if settings_data:

            conn.execute("""
                UPDATE school_settings
                SET school_name = ?,
                    school_address = ?,
                    school_phone = ?,
                    school_email = ?,
                    phone_number = ?,
                    email = ?,
                    principal_name = ?,
                    school_motto = ?,
                    school_logo = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                school_name,
                school_address,
                school_phone,
                school_email,
                school_phone,
                school_email,
                principal_name,
                school_motto,
                logo_filename,
                now,
                settings_data["id"]
            ))

        else:

            conn.execute("""
                INSERT INTO school_settings (
                    school_name,
                    school_address,
                    school_phone,
                    school_email,
                    phone_number,
                    email,
                    principal_name,
                    school_motto,
                    school_logo,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                school_name,
                school_address,
                school_phone,
                school_email,
                school_phone,
                school_email,
                principal_name,
                school_motto,
                logo_filename,
                now,
                now
            ))

        conn.commit()

        flash("School settings saved successfully.")

        settings_data = conn.execute("""
            SELECT *
            FROM school_settings
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

    conn.close()

    return render_template(
        "settings.html",
        settings=settings_data
    )



@app.route("/color-settings", methods=["GET", "POST"])
def color_settings():
    if not login_required():
        return redirect("/")

    colors = {
        "page": "#FFFFFF",
        "sidebar": "#000080",
        "cards": "#FFFFFF",
        "buttons": "#000080",
        "links": "#FF8C00",
    }

    connection = get_connection()
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_color_settings (
                id INTEGER PRIMARY KEY,
                page_color TEXT NOT NULL,
                sidebar_color TEXT NOT NULL,
                cards_color TEXT NOT NULL,
                buttons_color TEXT NOT NULL,
                links_color TEXT NOT NULL,
                updated_at TEXT
            )
        """)

        saved = connection.execute(
            "SELECT * FROM dashboard_color_settings ORDER BY id DESC LIMIT 1"
        ).fetchone()

        if saved:
            colors.update({
                "page": saved["page_color"],
                "sidebar": saved["sidebar_color"],
                "cards": saved["cards_color"],
                "buttons": saved["buttons_color"],
                "links": saved["links_color"],
            })

        if request.method == "POST":
            allowed = set(COLOR_PALETTE.values())
            submitted = {
                "page": request.form.get("page_color", ""),
                "sidebar": request.form.get("sidebar_color", ""),
                "cards": request.form.get("cards_color", ""),
                "buttons": request.form.get("buttons_color", ""),
                "links": request.form.get("links_color", ""),
            }

            if not all(value in allowed for value in submitted.values()):
                flash("Please select a valid color from each dropdown.")
            else:
                connection.execute("""
                    INSERT INTO dashboard_color_settings
                    (page_color, sidebar_color, cards_color,
                     buttons_color, links_color, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    submitted["page"], submitted["sidebar"],
                    submitted["cards"], submitted["buttons"],
                    submitted["links"], datetime.now().isoformat()
                ))
                connection.commit()
                colors = submitted
                flash("Dashboard colors saved successfully.")
                return redirect("/color-settings")

        return render_template(
            "color_settings.html",
            colors=colors,
            palette=COLOR_PALETTE
        )
    finally:
        connection.close()


COLOR_PALETTE = {
    "AliceBlue": "#F0F8FF",
    "AntiqueWhite": "#FAEBD7",
    "Aqua": "#00FFFF",
    "Aquamarine": "#7FFFD4",
    "Azure": "#F0FFFF",
    "Beige": "#F5F5DC",
    "Bisque": "#FFE4C4",
    "Black": "#000000",
    "BlanchedAlmond": "#FFEBCD",
    "Blue": "#0000FF",
    "BlueViolet": "#8A2BE2",
    "Brown": "#A52A2A",
    "BurlyWood": "#DEB887",
    "CadetBlue": "#5F9EA0",
    "Chartreuse": "#7FFF00",
    "Chocolate": "#D2691E",
    "Coral": "#FF7F50",
    "CornflowerBlue": "#6495ED",
    "Cornsilk": "#FFF8DC",
    "Crimson": "#DC143C",
    "Cyan": "#00FFFF",
    "DarkBlue": "#00008B",
    "DarkCyan": "#008B8B",
    "DarkGoldenRod": "#B8860B",
    "DarkGray": "#A9A9A9",
    "DarkGreen": "#006400",
    "DarkKhaki": "#BDB76B",
    "DarkMagenta": "#8B008B",
    "DarkOliveGreen": "#556B2F",
    "DarkOrange": "#FF8C00",
    "DarkOrchid": "#9932CC",
    "DarkRed": "#8B0000",
    "DarkSalmon": "#E9967A",
    "DarkSeaGreen": "#8FBC8F",
    "DarkSlateBlue": "#483D8B",
    "DarkSlateGray": "#2F4F4F",
    "DarkTurquoise": "#00CED1",
    "DarkViolet": "#9400D3",
    "DeepPink": "#FF1493",
    "DeepSkyBlue": "#00BFFF",
    "DimGray": "#696969",
    "DodgerBlue": "#1E90FF",
    "FireBrick": "#B22222",
    "FloralWhite": "#FFFAF0",
    "ForestGreen": "#228B22",
    "Fuchsia": "#FF00FF",
    "Gainsboro": "#DCDCDC",
    "GhostWhite": "#F8F8FF",
    "Gold": "#FFD700",
    "GoldenRod": "#DAA520",
    "Gray": "#808080",
    "Green": "#008000",
    "GreenYellow": "#ADFF2F",
    "HoneyDew": "#F0FFF0",
    "HotPink": "#FF69B4",
    "IndianRed": "#CD5C5C",
    "Indigo": "#4B0082",
    "Ivory": "#FFFFF0",
    "Khaki": "#F0E68C",
    "Lavender": "#E6E6FA",
    "LavenderBlush": "#FFF0F5",
    "LawnGreen": "#7CFC00",
    "LemonChiffon": "#FFFACD",
    "LightBlue": "#ADD8E6",
    "LightCoral": "#F08080",
    "LightCyan": "#E0FFFF",
    "LightGoldenRodYellow": "#FAFAD2",
    "LightGray": "#D3D3D3",
    "LightGreen": "#90EE90",
    "LightPink": "#FFB6C1",
    "LightSalmon": "#FFA07A",
    "LightSeaGreen": "#20B2AA",
    "LightSkyBlue": "#87CEFA",
    "LightSlateGray": "#778899",
    "LightSteelBlue": "#B0C4DE",
    "LightYellow": "#FFFFE0",
    "Lime": "#00FF00",
    "LimeGreen": "#32CD32",
    "Linen": "#FAF0E6",
    "Magenta": "#FF00FF",
    "Maroon": "#800000",
    "MediumAquaMarine": "#66CDAA",
    "MediumBlue": "#0000CD",
    "MediumOrchid": "#BA55D3",
    "MediumPurple": "#9370DB",
    "MediumSeaGreen": "#3CB371",
    "MediumSlateBlue": "#7B68EE",
    "MediumSpringGreen": "#00FA9A",
    "MediumTurquoise": "#48D1CC",
    "MediumVioletRed": "#C71585",
    "MidnightBlue": "#191970",
    "MintCream": "#F5FFFA",
    "MistyRose": "#FFE4E1",
    "Moccasin": "#FFE4B5",
    "NavajoWhite": "#FFDEAD",
    "Navy": "#000080",
    "OldLace": "#FDF5E6",
    "Olive": "#808000",
    "OliveDrab": "#6B8E23",
    "Orange": "#FFA500",
    "OrangeRed": "#FF4500",
    "Orchid": "#DA70D6",
    "PaleGoldenRod": "#EEE8AA",
    "PaleGreen": "#98FB98",
    "PaleTurquoise": "#AFEEEE",
    "PaleVioletRed": "#DB7093",
    "PapayaWhip": "#FFEFD5",
    "PeachPuff": "#FFDAB9",
    "Peru": "#CD853F",
    "Pink": "#FFC0CB",
    "Plum": "#DDA0DD",
    "PowderBlue": "#B0E0E6",
    "Purple": "#800080",
    "RebeccaPurple": "#663399",
    "Red": "#FF0000",
    "RosyBrown": "#BC8F8F",
    "RoyalBlue": "#4169E1",
    "SaddleBrown": "#8B4513",
    "Salmon": "#FA8072",
    "SandyBrown": "#F4A460",
    "SeaGreen": "#2E8B57",
    "SeaShell": "#FFF5EE",
    "Sienna": "#A0522D",
    "Silver": "#C0C0C0",
    "SkyBlue": "#87CEEB",
    "SlateBlue": "#6A5ACD",
    "SlateGray": "#708090",
    "Snow": "#FFFAFA",
    "SpringGreen": "#00FF7F",
    "SteelBlue": "#4682B4",
    "Tan": "#D2B48C",
    "Teal": "#008080",
    "Thistle": "#D8BFD8",
    "Tomato": "#FF6347",
    "Turquoise": "#40E0D0",
    "Violet": "#EE82EE",
    "Wheat": "#F5DEB3",
    "White": "#FFFFFF",
    "WhiteSmoke": "#F5F5F5",
    "Yellow": "#FFFF00",
    "YellowGreen": "#9ACD32",
    "DeepForestGreen": "#064E3B",
    "OceanTeal": "#0F766E",
    "RoyalIndigo": "#3730A3",
    "SoftRose": "#F4A7B9",
    "WarmSand": "#D6B98C",
    "MidnightNavy": "#101B3D",
}


# ============================================================
# STUDENT FEES
# ============================================================


@app.route("/student-fees", methods=["GET", "POST"])
def student_fees():

    if not login_required():
        return redirect("/")

    # Ensure the scholarship reason column exists in the
    # currently selected school's database.
    migration_conn = get_connection()
    try:
        table_exists = migration_conn.execute(
            """SELECT name FROM sqlite_master
               WHERE type = 'table'
               AND name = 'student_fee_scholarships'"""
        ).fetchone()

        if table_exists:
            scholarship_columns = {
                row["name"]
                for row in migration_conn.execute(
                    "PRAGMA table_info(student_fee_scholarships)"
                ).fetchall()
            }

            if "scholarship_reason" not in scholarship_columns:
                migration_conn.execute(
                    """ALTER TABLE student_fee_scholarships
                       ADD COLUMN scholarship_reason TEXT DEFAULT ''"""
                )
                migration_conn.commit()
    finally:
        migration_conn.close()

    active_year = get_active_academic_year()

    if not active_year:

        flash(
            "Please activate an academic year before using Student Fees."
        )

        return redirect("/academic-years")

    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_fee_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            academic_year_id INTEGER NOT NULL,
            fee_type TEXT NOT NULL CHECK (fee_type IN ('class', 'specific')),
            fee_item_id INTEGER NOT NULL,
            adjusted_amount REAL NOT NULL CHECK (adjusted_amount >= 0),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (student_id, academic_year_id, fee_type, fee_item_id)
        )
    """)

    if request.method == "POST":

        action = request.form.get("action", "").strip()

        # ====================================================
        # ADD CLASS FEE
        # ====================================================


        if action == "adjust_fee_amount_bulk":
            try:
                student_id = int(request.form.get("student_id", ""))
                fee_item_ids = request.form.getlist("fee_item_id")
                fee_types = request.form.getlist("fee_type")
                amount_values = request.form.getlist("adjusted_amount")

                if not fee_item_ids or not (
                    len(fee_item_ids) == len(fee_types) == len(amount_values)
                ):
                    raise ValueError("Fee adjustment fields do not match.")

                student_exists = conn.execute(
                    "SELECT id FROM students WHERE id = ?",
                    (student_id,)
                ).fetchone()
                if not student_exists:
                    raise ValueError("Student not found.")

                prepared = []
                seen = set()

                for item_id_raw, fee_type, amount_raw in zip(
                    fee_item_ids, fee_types, amount_values
                ):
                    fee_item_id = int(item_id_raw)
                    fee_type = fee_type.strip()
                    adjusted_amount = float(amount_raw.strip())

                    if fee_type not in ("class", "specific"):
                        raise ValueError("Invalid fee type.")
                    if adjusted_amount < 0 or adjusted_amount != adjusted_amount:
                        raise ValueError("Invalid fee amount.")
                    if adjusted_amount == float("inf") or adjusted_amount == float("-inf"):
                        raise ValueError("Invalid fee amount.")

                    key = (fee_type, fee_item_id)
                    if key in seen:
                        raise ValueError("Duplicate fee item.")
                    seen.add(key)

                    if fee_type == "class":
                        fee_exists = conn.execute("""
                            SELECT id FROM student_fee_items
                            WHERE id = ? AND academic_year_id = ?
                        """, (fee_item_id, active_year["id"])).fetchone()
                    else:
                        fee_exists = conn.execute("""
                            SELECT id FROM student_specific_fees
                            WHERE id = ? AND student_id = ?
                              AND academic_year_id = ?
                        """, (
                            fee_item_id, student_id, active_year["id"]
                        )).fetchone()

                    if not fee_exists:
                        raise ValueError("Fee item not found for this student/year.")

                    prepared.append((
                        student_id, active_year["id"], fee_type,
                        fee_item_id, adjusted_amount
                    ))

                # Save all changes as one transaction. If any write fails,
                # the rollback prevents a partial bulk update.
                for values in prepared:
                    conn.execute("""
                        INSERT INTO student_fee_adjustments
                            (student_id, academic_year_id, fee_type,
                             fee_item_id, adjusted_amount, updated_at)
                        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(student_id, academic_year_id, fee_type, fee_item_id)
                        DO UPDATE SET
                            adjusted_amount = excluded.adjusted_amount,
                            updated_at = CURRENT_TIMESTAMP
                    """, values)

                conn.commit()
                flash("All student fee adjustments saved successfully.")

            except (ValueError, TypeError, OverflowError):
                conn.rollback()
                flash("Fee adjustments failed. Check the amounts and try again.")
            except Exception:
                conn.rollback()
                flash("Fee adjustments could not be saved.")

            conn.close()
            return redirect("/student-fees")

        if action == "adjust_fee_amount":
            try:
                student_id = int(request.form.get("student_id", ""))
                fee_item_id = int(request.form.get("fee_item_id", ""))
                fee_type = request.form.get("fee_type", "").strip()
                amount_raw = request.form.get("adjusted_amount", "").strip()
                adjusted_amount = float(amount_raw)

                if fee_type not in ("class", "specific"):
                    raise ValueError("Invalid fee type.")
                if adjusted_amount < 0 or adjusted_amount != adjusted_amount:
                    raise ValueError("Fee amount must be zero or greater.")
                if adjusted_amount == float("inf"):
                    raise ValueError("Invalid fee amount.")

                student_exists = conn.execute(
                    "SELECT id FROM students WHERE id = ?",
                    (student_id,)
                ).fetchone()
                if not student_exists:
                    raise ValueError("Student not found.")

                if fee_type == "class":
                    fee_exists = conn.execute("""
                        SELECT id FROM student_fee_items
                        WHERE id = ? AND academic_year_id = ?
                    """, (fee_item_id, active_year["id"])).fetchone()
                else:
                    fee_exists = conn.execute("""
                        SELECT id FROM student_specific_fees
                        WHERE id = ? AND student_id = ?
                          AND academic_year_id = ?
                    """, (fee_item_id, student_id, active_year["id"])).fetchone()

                if not fee_exists:
                    raise ValueError("Fee description not found for this student/year.")

                conn.execute("""
                    INSERT INTO student_fee_adjustments
                        (student_id, academic_year_id, fee_type,
                         fee_item_id, adjusted_amount, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(student_id, academic_year_id, fee_type, fee_item_id)
                    DO UPDATE SET
                        adjusted_amount = excluded.adjusted_amount,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    student_id, active_year["id"], fee_type,
                    fee_item_id, adjusted_amount
                ))
                conn.commit()
                flash("Student fee amount adjusted successfully.")
            except (ValueError, TypeError, OverflowError):
                conn.rollback()
                flash("Fee adjustment failed. Check the amount and try again.")
            except Exception:
                conn.rollback()
                flash("Fee adjustment could not be saved.")

            conn.close()
            return redirect("/student-fees")

        elif action == "add_fee":

            class_id = request.form.get("class_id", "").strip()
            description = request.form.get("description", "").strip()
            amount_raw = request.form.get("amount", "0").strip()

            try:
                amount = float(amount_raw or 0)
            except Exception:
                amount = 0

            if not class_id:
                flash("Please select a class.")

            elif not description:
                flash("Please enter the fee description.")

            elif amount <= 0:
                flash("Fee amount must be greater than zero.")

            else:

                existing = conn.execute("""
                    SELECT id
                    FROM student_fee_items
                    WHERE class_id = ?
                      AND academic_year_id = ?
                      AND LOWER(description) = LOWER(?)
                """, (
                    class_id,
                    active_year["id"],
                    description
                )).fetchone()

                if existing:
                    flash(
                        "This class fee already exists for the active academic year."
                    )

                else:

                    conn.execute("""
                        INSERT INTO student_fee_items (
                            class_id,
                            academic_year_id,
                            description,
                            amount,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        class_id,
                        active_year["id"],
                        description,
                        amount,
                        datetime.now().isoformat()
                    ))

                    conn.commit()

                    flash("Class fee stored successfully.")

        # ====================================================
        # ADD NEW STUDENT-SPECIFIC FEE + OPTIONAL PAYMENT
        # ====================================================

        elif action == "add_student_fee_payment":

            student_id = request.form.get("student_id", "").strip()
            description = request.form.get(
                "new_fee_description", ""
            ).strip()
            amount_raw = request.form.get(
                "new_fee_amount", "0"
            ).strip()
            payment_raw = request.form.get(
                "new_payment_amount", "0"
            ).strip()
            payment_date = request.form.get(
                "payment_date", ""
            ).strip()
            payment_method = request.form.get(
                "payment_method", "Cash"
            ).strip()
            reference = request.form.get(
                "reference", ""
            ).strip()

            if not payment_date:
                payment_date = datetime.now().strftime("%Y-%m-%d")

            try:
                charge_amount = float(amount_raw or 0)
            except Exception:
                charge_amount = 0

            try:
                first_payment = float(payment_raw or 0)
            except Exception:
                first_payment = 0

            student = None

            if student_id:
                student = conn.execute("""
                    SELECT
                        students.*,
                        COALESCE(student_class_placements.class_id, students.class_id) AS year_class_id,
                        COALESCE(placement_classes.class_name, classes.class_name) AS class_name
                    FROM students
                    LEFT JOIN student_class_placements
                        ON student_class_placements.student_db_id = students.id
                       AND student_class_placements.academic_year_id = ?
                    LEFT JOIN classes ON students.class_id = classes.id
                    LEFT JOIN classes AS placement_classes
                        ON student_class_placements.class_id = placement_classes.id
                    WHERE students.id = ?
                """, (
                    active_year["id"],
                    student_id
                )).fetchone()

            if not student:
                flash("Student record not found.")

            elif not student["year_class_id"]:
                flash("The student has no class assigned.")

            elif not description:
                flash("Please enter the new fee description.")

            elif charge_amount <= 0:
                flash("The new fee amount must be greater than zero.")

            elif first_payment < 0:
                flash("The first payment cannot be negative.")

            elif first_payment > charge_amount:
                flash("The first payment cannot be greater than the new fee amount.")

            else:

                conn.execute("""
                    INSERT INTO student_specific_fees (
                        student_id,
                        class_id,
                        academic_year_id,
                        description,
                        amount,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    student["id"],
                    student["year_class_id"],
                    active_year["id"],
                    description,
                    charge_amount,
                    datetime.now().isoformat()
                ))

                specific_fee_id = conn.execute(
                    "SELECT last_insert_rowid() AS id"
                ).fetchone()["id"]

                if first_payment > 0:

                    conn.execute("""
                        INSERT INTO student_fee_payments (
                            student_id,
                            class_id,
                            academic_year_id,
                            fee_item_id,
                            student_specific_fee_id,
                            amount,
                            payment_date,
                            description,
                            payment_method,
                            reference,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        student["id"],
                        student["year_class_id"],
                        active_year["id"],
                        None,
                        specific_fee_id,
                        first_payment,
                        payment_date,
                        description,
                        payment_method,
                        reference,
                        datetime.now().isoformat()
                    ))

                    flash(
                        f"New fee '{description}' added and payment of "
                        f"{first_payment:.2f} recorded successfully."
                    )

                else:

                    flash(
                        f"New fee '{description}' added to "
                        f"{student['first_name']} {student['last_name']}."
                    )

                conn.commit()

                if action == "save_specific_fee" and first_payment > 0:
                    try:
                        from modules.notifications import ensure_notification_tables, create_notification
                        ensure_notification_tables(conn)
                        sid = session.get("school_id")
                        if sid is not None:
                            student_name = f"{student['first_name']} {student['last_name']}"
                            create_notification(
                                conn, sid, "new_payment",
                                "Student Payment Received",
                                f"Payment of {first_payment:.2f} received from {student_name} for {description}.",
                                related_id=student["id"],
                                related_name=student_name
                            )
                    except Exception as notification_error:
                        print("Student payment notification could not be created:", notification_error)

        # ====================================================
        # SAVE / UPDATE STUDENT SCHOLARSHIP
        # ====================================================

        elif action == "save_scholarship":

            student_id = request.form.get("student_id", "").strip()
            scholarship_name = request.form.get(
                "scholarship_name", ""
            ).strip()
            scholarship_type = request.form.get(
                "scholarship_type", "Selected Bills"
            ).strip()
            notes = request.form.get("scholarship_notes", "").strip()
            scholarship_reason = request.form.get("scholarship_reason", "").strip()

            if scholarship_type not in {
                "None",
                "Full Scholarship",
                "Selected Bills"
            }:
                scholarship_type = "Selected Bills"

            student = conn.execute("""
                SELECT
                    students.*,
                    COALESCE(
                        student_class_placements.class_id,
                        students.class_id
                    ) AS year_class_id
                FROM students
                LEFT JOIN student_class_placements
                    ON student_class_placements.student_db_id = students.id
                   AND student_class_placements.academic_year_id = ?
                WHERE students.id = ?
                  AND students.academic_year_id = ?
            """, (
                active_year["id"],
                student_id,
                active_year["id"]
            )).fetchone()

            if not student:
                flash("Student record not found.")
            else:

                existing = conn.execute("""
                    SELECT id
                    FROM student_fee_scholarships
                    WHERE student_id = ?
                      AND academic_year_id = ?
                    LIMIT 1
                """, (student_id, active_year["id"])).fetchone()

                now = datetime.now().isoformat()

                if scholarship_type == "None":

                    conn.execute("""
                        DELETE FROM student_fee_scholarship_items
                        WHERE student_id = ?
                          AND academic_year_id = ?
                    """, (student_id, active_year["id"]))

                    conn.execute("""
                        DELETE FROM student_fee_scholarships
                        WHERE student_id = ?
                          AND academic_year_id = ?
                    """, (student_id, active_year["id"]))

                    conn.commit()
                    flash("Scholarship adjustment removed. Normal class billing is restored.")

                else:

                    if not scholarship_name:
                        scholarship_name = (
                            "Full Scholarship"
                            if scholarship_type == "Full Scholarship"
                            else "Scholarship"
                        )

                    if existing:
                        conn.execute("""
                            UPDATE student_fee_scholarships
                            SET class_id = ?,
                                scholarship_name = ?,
                                scholarship_type = ?,
                                scholarship_reason = ?,
                                notes = ?,
                                updated_at = ?
                            WHERE id = ?
                        """, (
                            student["year_class_id"],
                            scholarship_name,
                            scholarship_type,
                            scholarship_reason,
                            notes,
                            now,
                            existing["id"]
                        ))

                        scholarship_id = existing["id"]
                    else:
                        conn.execute("""
                            INSERT INTO student_fee_scholarships (
                                student_id,
                                class_id,
                                academic_year_id,
                                scholarship_name,
                                scholarship_type,
                                scholarship_reason,
                                notes,
                                created_at,
                                updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            student["id"],
                            student["year_class_id"],
                            active_year["id"],
                            scholarship_name,
                            scholarship_type,
                            scholarship_reason,
                            notes,
                            now,
                            now
                        ))

                        scholarship_id = conn.execute(
                            "SELECT last_insert_rowid() AS id"
                        ).fetchone()["id"]

                    conn.execute("""
                        DELETE FROM student_fee_scholarship_items
                        WHERE student_id = ?
                          AND academic_year_id = ?
                    """, (student["id"], active_year["id"]))

                    if scholarship_type == "Selected Bills":

                        class_fees_for_student = conn.execute("""
                            SELECT id
                            FROM student_fee_items
                            WHERE class_id = ?
                              AND academic_year_id = ?
                            ORDER BY id
                        """, (
                            student["year_class_id"],
                            active_year["id"]
                        )).fetchall()

                        saved_adjustments = 0

                        for fee in class_fees_for_student:

                            discount_raw = request.form.get(
                                f"scholarship_discount_{fee['id']}",
                                ""
                            ).strip()

                            if not discount_raw:
                                continue

                            try:
                                discount = float(discount_raw)
                            except Exception:
                                discount = 0

                            discount = max(0.0, min(discount, 100.0))

                            if discount <= 0:
                                continue

                            conn.execute("""
                                INSERT INTO student_fee_scholarship_items (
                                    student_id,
                                    class_id,
                                    academic_year_id,
                                    fee_item_id,
                                    discount_percent,
                                    created_at
                                )
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (
                                student["id"],
                                student["year_class_id"],
                                active_year["id"],
                                fee["id"],
                                discount,
                                now
                            ))

                            saved_adjustments += 1

                    conn.commit()

                    if scholarship_type == "Full Scholarship":
                        flash(
                            f"Full scholarship saved for {student['first_name']} {student['last_name']}. "
                            "All current and future class bills are exempted for this student."
                        )
                    else:
                        flash(
                            f"Scholarship billing adjustment saved for {student['first_name']} {student['last_name']}. "
                            f"{saved_adjustments} bill(s) adjusted."
                        )

        # ====================================================
        # RECORD PAYMENT AGAINST EXISTING FEE
        # ====================================================

        elif action == "record_payment":

            student_id = request.form.get("student_id", "").strip()
            payment_date = request.form.get("payment_date", "").strip()
            payment_method = request.form.get(
                "payment_method", "Cash"
            ).strip()
            reference = request.form.get("reference", "").strip()

            if not payment_date:
                payment_date = datetime.now().strftime("%Y-%m-%d")

            student = None

            if student_id:
                student = conn.execute("""
                    SELECT
                        students.*,
                        COALESCE(student_class_placements.class_id, students.class_id) AS year_class_id,
                        COALESCE(placement_classes.class_name, classes.class_name) AS class_name
                    FROM students
                    LEFT JOIN student_class_placements
                        ON student_class_placements.student_db_id = students.id
                       AND student_class_placements.academic_year_id = ?
                    LEFT JOIN classes ON students.class_id = classes.id
                    LEFT JOIN classes AS placement_classes
                        ON student_class_placements.class_id = placement_classes.id
                    WHERE students.id = ?
                """, (
                    active_year["id"],
                    student_id
                )).fetchone()

            if not student:

                flash("Please select a valid student.")

            elif not student["year_class_id"]:

                flash("The selected student has no class assigned.")

            else:

                saved_count = 0
                adjusted_count = 0
                total_saved = 0.0

                scholarship, scholarship_discounts = get_student_scholarship_discounts(
                    conn,
                    student["id"],
                    active_year["id"]
                )

                # Class-wide fees
                class_fees = conn.execute("""
                    SELECT *
                    FROM student_fee_items
                    WHERE class_id = ?
                      AND academic_year_id = ?
                    ORDER BY id
                """, (
                    student["year_class_id"],
                    active_year["id"]
                )).fetchall()

                for fee in class_fees:

                    paid_text = request.form.get(
                        f"fee_amount_{fee['id']}",
                        ""
                    ).strip()

                    if not paid_text:
                        continue

                    try:
                        paid_amount = float(paid_text)
                    except Exception:
                        continue

                    if paid_amount <= 0:
                        continue

                    previous = conn.execute("""
                        SELECT COALESCE(SUM(amount), 0) AS total_paid
                        FROM student_fee_payments
                        WHERE student_id = ?
                          AND class_id = ?
                          AND academic_year_id = ?
                          AND fee_item_id = ?
                    """, (
                        student["id"],
                        student["year_class_id"],
                        active_year["id"],
                        fee["id"]
                    )).fetchone()

                    previous_paid = float(previous["total_paid"] or 0)
                    normal_required = float(fee["amount"] or 0)

                    adjustment = conn.execute("""
                        SELECT adjusted_amount
                        FROM student_fee_adjustments
                        WHERE student_id = ?
                          AND academic_year_id = ?
                          AND fee_type = 'class'
                          AND fee_item_id = ?
                    """, (
                        student["id"],
                        active_year["id"],
                        fee["id"]
                    )).fetchone()

                    if adjustment:
                        normal_required = float(adjustment["adjusted_amount"] or 0)

                    discount_percent = get_class_fee_discount_percent(
                        scholarship,
                        scholarship_discounts,
                        fee["id"]
                    )

                    required = max(
                        normal_required * (1 - discount_percent / 100.0),
                        0
                    )
                    remaining = max(required - previous_paid, 0)

                    if remaining <= 0:
                        adjusted_count += 1
                        continue

                    if paid_amount > remaining:
                        paid_amount = remaining
                        adjusted_count += 1

                    if paid_amount <= 0:
                        continue

                    conn.execute("""
                        INSERT INTO student_fee_payments (
                            student_id,
                            class_id,
                            academic_year_id,
                            fee_item_id,
                            student_specific_fee_id,
                            amount,
                            payment_date,
                            description,
                            payment_method,
                            reference,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        student["id"],
                        student["year_class_id"],
                        active_year["id"],
                        fee["id"],
                        None,
                        paid_amount,
                        payment_date,
                        fee["description"],
                        payment_method,
                        reference,
                        datetime.now().isoformat()
                    ))

                    saved_count += 1
                    total_saved += paid_amount

                # Student-specific fees
                specific_fees = conn.execute("""
                    SELECT *
                    FROM student_specific_fees
                    WHERE student_id = ?
                      AND academic_year_id = ?
                    ORDER BY id
                """, (
                    student["id"],
                    active_year["id"]
                )).fetchall()

                for fee in specific_fees:

                    paid_text = request.form.get(
                        f"specific_fee_amount_{fee['id']}",
                        ""
                    ).strip()

                    if not paid_text:
                        continue

                    try:
                        paid_amount = float(paid_text)
                    except Exception:
                        continue

                    if paid_amount <= 0:
                        continue

                    previous = conn.execute("""
                        SELECT COALESCE(SUM(amount), 0) AS total_paid
                        FROM student_fee_payments
                        WHERE student_id = ?
                          AND class_id = ?
                          AND academic_year_id = ?
                          AND student_specific_fee_id = ?
                    """, (
                        student["id"],
                        student["year_class_id"],
                        active_year["id"],
                        fee["id"]
                    )).fetchone()

                    previous_paid = float(previous["total_paid"] or 0)
                    required = float(fee["amount"] or 0)

                    adjustment = conn.execute("""
                        SELECT adjusted_amount
                        FROM student_fee_adjustments
                        WHERE student_id = ?
                          AND academic_year_id = ?
                          AND fee_type = 'specific'
                          AND fee_item_id = ?
                    """, (
                        student["id"],
                        active_year["id"],
                        fee["id"]
                    )).fetchone()

                    if adjustment:
                        required = float(adjustment["adjusted_amount"] or 0)

                    remaining = max(required - previous_paid, 0)

                    if remaining <= 0:
                        adjusted_count += 1
                        continue

                    if paid_amount > remaining:
                        paid_amount = remaining
                        adjusted_count += 1

                    if paid_amount <= 0:
                        continue

                    conn.execute("""
                        INSERT INTO student_fee_payments (
                            student_id,
                            class_id,
                            academic_year_id,
                            fee_item_id,
                            student_specific_fee_id,
                            amount,
                            payment_date,
                            description,
                            payment_method,
                            reference,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        student["id"],
                        student["year_class_id"],
                        active_year["id"],
                        None,
                        fee["id"],
                        paid_amount,
                        payment_date,
                        fee["description"],
                        payment_method,
                        reference,
                        datetime.now().isoformat()
                    ))

                    saved_count += 1
                    total_saved += paid_amount

                conn.commit()

                if saved_count > 0:
                    try:
                        from modules.notifications import ensure_notification_tables, create_notification
                        ensure_notification_tables(conn)
                        sid = session.get("school_id")
                        if sid is not None:
                            student_row = conn.execute(
                                "SELECT first_name, last_name FROM students WHERE id = ?",
                                (student_id,)
                            ).fetchone()
                            student_name = (
                                f"{student_row['first_name']} {student_row['last_name']}"
                                if student_row else f"Student ID {student_id}"
                            )
                            create_notification(
                                conn, sid, "new_payment",
                                "Student Payment Received",
                                f"Payment of {total_saved:.2f} received from {student_name}.",
                                related_id=student_id,
                                related_name=student_name
                            )
                    except Exception as notification_error:
                        print("Student payment notification could not be created:", notification_error)

                    message = (
                        f"Payment recorded successfully. "
                        f"Total received: {total_saved:.2f}"
                    )

                    if adjusted_count > 0:
                        message += (
                            " Some amounts were adjusted "
                            "to the remaining balance."
                        )

                    flash(message)

                else:

                    flash(
                        "No payment was entered. Enter an amount greater "
                        "than zero for at least one unpaid fee."
                    )

    # ========================================================
    # CLASSES
    # ========================================================

    classes_list = conn.execute("""
        SELECT *
        FROM classes
        ORDER BY class_name
    """).fetchall()

    # ========================================================
    # ACTIVE YEAR STUDENTS
    # ========================================================

    students_list = conn.execute("""
        SELECT
            students.*,
            COALESCE(
                student_class_placements.class_id,
                students.class_id
            ) AS year_class_id,
            COALESCE(
                placement_classes.class_name,
                classes.class_name
            ) AS class_name
        FROM students
        LEFT JOIN student_class_placements
            ON student_class_placements.student_db_id = students.id
           AND student_class_placements.academic_year_id = ?
        LEFT JOIN classes
            ON students.class_id = classes.id
        LEFT JOIN classes AS placement_classes
            ON student_class_placements.class_id = placement_classes.id
        ORDER BY students.first_name, students.last_name
    """, (
        active_year["id"],
    )).fetchall()

    # ========================================================
    # STORED CLASS FEES
    # ========================================================

    fee_items = conn.execute("""
        SELECT
            student_fee_items.*,
            classes.class_name
        FROM student_fee_items
        LEFT JOIN classes
            ON student_fee_items.class_id = classes.id
        WHERE student_fee_items.academic_year_id = ?
        ORDER BY classes.class_name,
                 student_fee_items.id
    """, (
        active_year["id"],
    )).fetchall()

    # ========================================================
    # PAYMENT HISTORY
    # ========================================================

    payments = conn.execute("""
        SELECT
            student_fee_payments.*,
            students.student_id AS student_number,
            students.first_name,
            students.last_name,
            classes.class_name,
            COALESCE(
                student_fee_items.description,
                student_specific_fees.description,
                student_fee_payments.description
            ) AS fee_description,
            COALESCE(
                student_fee_items.amount,
                student_specific_fees.amount,
                0
            ) AS fee_amount
        FROM student_fee_payments
        LEFT JOIN students
            ON student_fee_payments.student_id = students.id
        LEFT JOIN classes
            ON student_fee_payments.class_id = classes.id
        LEFT JOIN student_fee_items
            ON student_fee_payments.fee_item_id = student_fee_items.id
        LEFT JOIN student_specific_fees
            ON student_fee_payments.student_specific_fee_id = student_specific_fees.id
        WHERE student_fee_payments.academic_year_id = ?
        ORDER BY student_fee_payments.student_id,
                 student_fee_payments.id DESC
    """, (
        active_year["id"],
    )).fetchall()

    payment_grand_total = float(
        conn.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total_paid
            FROM student_fee_payments
            WHERE academic_year_id = ?
        """, (active_year["id"],)).fetchone()["total_paid"] or 0
    )

    # ========================================================
    # COMPLETE BALANCE DATA
    # Class fees + student-specific fees.
    # ========================================================

    balances = []

    balance_grand_required = 0.0
    balance_grand_paid = 0.0
    balance_grand_remaining = 0.0

    for student in students_list:

        scholarship, scholarship_discounts = get_student_scholarship_discounts(
            conn,
            student["id"],
            active_year["id"]
        )

        # Class-wide fees for the student's class.
        class_fee_rows = conn.execute("""
            SELECT
                id AS fee_item_id,
                description,
                amount AS required_amount,
                'class' AS fee_type
            FROM student_fee_items
            WHERE class_id = ?
              AND academic_year_id = ?
            ORDER BY id
        """, (
            student["year_class_id"],
            active_year["id"]
        )).fetchall()

        # Student-only charges.
        specific_fee_rows = conn.execute("""
            SELECT
                id AS fee_item_id,
                description,
                amount AS required_amount,
                'specific' AS fee_type
            FROM student_specific_fees
            WHERE student_id = ?
              AND academic_year_id = ?
            ORDER BY id
        """, (
            student["id"],
            active_year["id"]
        )).fetchall()

        for fee in list(class_fee_rows) + list(specific_fee_rows):

            if fee["fee_type"] == "class":

                paid_row = conn.execute("""
                    SELECT COALESCE(SUM(amount), 0) AS total_paid
                    FROM student_fee_payments
                    WHERE student_id = ?
                      AND class_id = ?
                      AND academic_year_id = ?
                      AND fee_item_id = ?
                """, (
                    student["id"],
                    student["year_class_id"],
                    active_year["id"],
                    fee["fee_item_id"]
                )).fetchone()

            else:

                paid_row = conn.execute("""
                    SELECT COALESCE(SUM(amount), 0) AS total_paid
                    FROM student_fee_payments
                    WHERE student_id = ?
                      AND class_id = ?
                      AND academic_year_id = ?
                      AND student_specific_fee_id = ?
                """, (
                    student["id"],
                    student["year_class_id"],
                    active_year["id"],
                    fee["fee_item_id"]
                )).fetchone()

            normal_required_amount = float(fee["required_amount"] or 0)

            adjustment = conn.execute("""
                SELECT adjusted_amount
                FROM student_fee_adjustments
                WHERE student_id = ?
                  AND academic_year_id = ?
                  AND fee_type = ?
                  AND fee_item_id = ?
            """, (
                student["id"], active_year["id"],
                fee["fee_type"], fee["fee_item_id"]
            )).fetchone()

            if adjustment:
                normal_required_amount = float(adjustment["adjusted_amount"])


            if fee["fee_type"] == "class":
                discount_percent = get_class_fee_discount_percent(
                    scholarship,
                    scholarship_discounts,
                    fee["fee_item_id"]
                )
            else:
                discount_percent = 0.0

            scholarship_amount = normal_required_amount * (
                discount_percent / 100.0
            )

            required_amount = max(
                normal_required_amount - scholarship_amount,
                0
            )

            paid_amount = float(paid_row["total_paid"] or 0)
            balance_amount = max(required_amount - paid_amount, 0)

            balances.append({
                "student_id": student["id"],
                "student_number": student["student_id"],
                "first_name": student["first_name"],
                "last_name": student["last_name"],
                "class_id": student["year_class_id"],
                "class_name": student["class_name"],
                "fee_item_id": fee["fee_item_id"],
                "fee_type": fee["fee_type"],
                "description": fee["description"],
                "normal_required_amount": normal_required_amount,
                "scholarship_percent": discount_percent,
                "scholarship_amount": scholarship_amount,
                "required_amount": required_amount,
                "paid_amount": paid_amount,
                "balance": balance_amount
            })

            balance_grand_required += required_amount
            balance_grand_paid += paid_amount
            balance_grand_remaining += balance_amount

    # ========================================================
    # GROUP EACH STUDENT
    # ========================================================

    payment_groups = {}

    for payment in payments:
        payment_groups.setdefault(
            payment["student_id"],
            []
        ).append(payment)

    student_groups = []

    for student in students_list:

        student_balance_rows = [
            row for row in balances
            if row["student_id"] == student["id"]
        ]

        student_scholarship, student_scholarship_discounts = get_student_scholarship_discounts(
            conn,
            student["id"],
            active_year["id"]
        )

        student_payments = payment_groups.get(
            student["id"], []
        )

        total_required = sum(
            float(row["required_amount"] or 0)
            for row in student_balance_rows
        )

        total_paid = sum(
            float(row["paid_amount"] or 0)
            for row in student_balance_rows
        )

        total_balance = sum(
            float(row["balance"] or 0)
            for row in student_balance_rows
        )

        payment_total = sum(
            float(payment["amount"] or 0)
            for payment in student_payments
        )

        class_fee_controls = [
            row for row in student_balance_rows
            if row["fee_type"] == "class"
        ]

        scholarship_percent_map = {
            row["fee_item_id"]: row.get("scholarship_percent", 0)
            for row in class_fee_controls
        }

        student_groups.append({
            "student_id": student["id"],
            "student_number": student["student_id"],
            "photo": student["photo"],
            "first_name": student["first_name"],
            "last_name": student["last_name"],
            "class_id": student["class_id"],
            "class_name": student["class_name"],
            "balance_rows": student_balance_rows,
            "payments": student_payments,
            "total_required": total_required,
            "total_paid": total_paid,
            "total_balance": total_balance,
            "payment_total": payment_total,
            "scholarship": student_scholarship,
            "scholarship_percent_map": scholarship_percent_map,
            "class_fee_controls": class_fee_controls
        })

    # ========================================================
    # CLASS BILL SUMMARIES
    # Show one compact row per class in Stored Class Fees.
    # ========================================================

    class_fee_summaries = []

    for class_item in classes_list:

        fee_rows_for_class = conn.execute("""
            SELECT
                id,
                description,
                amount
            FROM student_fee_items
            WHERE class_id = ?
              AND academic_year_id = ?
            ORDER BY id
        """, (
            class_item["id"],
            active_year["id"]
        )).fetchall()

        class_total = sum(
            float(row["amount"] or 0)
            for row in fee_rows_for_class
        )

        class_fee_summaries.append({
            "class_id": class_item["id"],
            "class_name": class_item["class_name"],
            "fee_count": len(fee_rows_for_class),
            "total_bill": class_total
        })

    conn.close()

    return render_template(
        "student_fees.html",
        active_year=active_year,
        classes=classes_list,
        students=students_list,
        fee_items=fee_items,
        payments=payments,
        balances=balances,
        student_groups=student_groups,
        class_fee_summaries=class_fee_summaries,
        payment_grand_total=payment_grand_total,
        balance_grand_required=balance_grand_required,
        balance_grand_paid=balance_grand_paid,
        balance_grand_remaining=balance_grand_remaining,
        current_date=datetime.now().strftime("%Y-%m-%d")
    )


# ============================================================
# AJAX:
# GET STORED FEES FOR CLASS
# ============================================================

@app.route("/student-fees/class-fees/<int:class_id>")
def get_class_fees(class_id):

    if not login_required():

        return jsonify({
            "success": False,
            "message": "Please log in first."
        }), 401

    active_year = get_active_academic_year()

    if not active_year:

        return jsonify({
            "success": False,
            "message": "No active academic year."
        }), 400

    conn = get_connection()

    fees = conn.execute("""
        SELECT
            id,
            description,
            amount
        FROM student_fee_items
        WHERE class_id = ?
          AND academic_year_id = ?
        ORDER BY id
    """, (
        class_id,
        active_year["id"]
    )).fetchall()

    result = []

    for fee in fees:

        result.append({
            "id": fee["id"],
            "description": fee["description"],
            "amount": float(fee["amount"] or 0)
        })

    conn.close()

    return jsonify({
        "success": True,
        "academic_year": active_year["year_name"],
        "fees": result
    })


# ============================================================
# AJAX:
# GET STUDENT'S CLASS AND STORED FEES
# ============================================================

@app.route("/student-fees/student/<int:student_id>")
def get_student_fees(student_id):

    if not login_required():

        return jsonify({
            "success": False,
            "message": "Please log in first."
        }), 401

    active_year = get_active_academic_year()

    if not active_year:

        return jsonify({
            "success": False,
            "message": "No active academic year."
        }), 400

    conn = get_connection()

    student = conn.execute("""
        SELECT
            students.id,
            students.student_id,
            students.first_name,
            students.last_name,
            students.class_id,
            classes.class_name
        FROM students
        LEFT JOIN classes
            ON students.class_id = classes.id
                            WHERE students.id = ?
                """, (
                    student_id,
                )).fetchone()

    if not student:

        conn.close()

        return jsonify({
            "success": False,
            "message": "Student not found."
        }), 404

    if not student["class_id"]:

        conn.close()

        return jsonify({
            "success": False,
            "message": "This student has no class assigned."
        }), 400

    fees = conn.execute("""
        SELECT
            id,
            description,
            amount
        FROM student_fee_items
        WHERE class_id = ?
          AND academic_year_id = ?
        ORDER BY id
    """, (
        student["class_id"],
        active_year["id"]
    )).fetchall()

    scholarship, scholarship_discounts = get_student_scholarship_discounts(
        conn,
        student["id"],
        active_year["id"]
    )

    result = []

    for fee in fees:

        paid = conn.execute("""
            SELECT
                COALESCE(SUM(amount), 0) AS total_paid
            FROM student_fee_payments
            WHERE student_id = ?
              AND class_id = ?
              AND academic_year_id = ?
              AND fee_item_id = ?
        """, (
            student_id,
            student["class_id"],
            active_year["id"],
            fee["id"]
        )).fetchone()

        total_paid = float(
            paid["total_paid"] or 0
        )

        normal_required = float(
            fee["amount"] or 0
        )

        discount_percent = get_class_fee_discount_percent(
            scholarship,
            scholarship_discounts,
            fee["id"]
        )

        required = normal_required * (
            1 - (discount_percent / 100.0)
        )

        balance = max(
            required - total_paid,
            0
        )

        result.append({
            "id": fee["id"],
            "description": fee["description"],
            "amount": required,
            "normal_amount": normal_required,
            "discount_percent": discount_percent,
            "paid": total_paid,
            "balance": balance
        })

    conn.close()

    return jsonify({
        "success": True,
        "student_id": student["student_id"],
        "student_name": (
            f"{student['first_name']} "
            f"{student['last_name']}"
        ),
        "class_id": student["class_id"],
        "class_name": student["class_name"],
        "academic_year": active_year["year_name"],
        "fees": result
    })


# ============================================================
# OLD AJAX URL KEPT FOR COMPATIBILITY
# ============================================================

@app.route(
    "/student-fees/student/<int:student_id>/class/<int:class_id>"
)
def get_student_fee_balances(student_id, class_id):

    if not login_required():

        return jsonify({
            "success": False,
            "message": "Please log in first."
        }), 401

    active_year = get_active_academic_year()

    if not active_year:

        return jsonify({
            "success": False,
            "message": "No active academic year."
        }), 400

    conn = get_connection()

    student = conn.execute("""
        SELECT
            students.*,
            classes.class_name
        FROM students
        LEFT JOIN classes
            ON students.class_id = classes.id
        WHERE students.id = ?
          AND students.class_id = ?
          AND students.academic_year_id = ?
    """, (
        student_id,
        class_id,
        active_year["id"]
    )).fetchone()

    if not student:

        conn.close()

        return jsonify({
            "success": False,
            "message": "Student does not belong to the selected class."
        }), 400

    fees = conn.execute("""
        SELECT
            id,
            description,
            amount
        FROM student_fee_items
        WHERE class_id = ?
          AND academic_year_id = ?
        ORDER BY id
    """, (
        class_id,
        active_year["id"]
    )).fetchall()

    scholarship, scholarship_discounts = get_student_scholarship_discounts(
        conn,
        student_id,
        active_year["id"]
    )

    result = []

    for fee in fees:

        paid = conn.execute("""
            SELECT
                COALESCE(SUM(amount), 0) AS total_paid
            FROM student_fee_payments
            WHERE student_id = ?
              AND class_id = ?
              AND academic_year_id = ?
              AND fee_item_id = ?
        """, (
            student_id,
            class_id,
            active_year["id"],
            fee["id"]
        )).fetchone()

        total_paid = float(
            paid["total_paid"] or 0
        )

        normal_required = float(
            fee["amount"] or 0
        )

        discount_percent = get_class_fee_discount_percent(
            scholarship,
            scholarship_discounts,
            fee["id"]
        )

        required = normal_required * (
            1 - (discount_percent / 100.0)
        )

        balance = max(
            required - total_paid,
            0
        )

        result.append({
            "id": fee["id"],
            "description": fee["description"],
            "amount": required,
            "normal_amount": normal_required,
            "discount_percent": discount_percent,
            "paid": total_paid,
            "balance": balance
        })

    conn.close()

    return jsonify({
        "success": True,
        "academic_year": active_year["year_name"],
        "fees": result
    })


# ============================================================
# EDIT STUDENT PAYMENT
# ============================================================

@app.route("/student-fees/edit-payment", methods=["POST"])
def edit_student_fee_payment():

    if not login_required():
        return redirect("/")

    payment_id = request.form.get("payment_id", "").strip()
    amount_raw = request.form.get("amount", "0").strip()
    payment_date = request.form.get("payment_date", "").strip()
    payment_method = request.form.get("payment_method", "Cash").strip()
    reference = request.form.get("reference", "").strip()

    if not payment_date:
        payment_date = datetime.now().strftime("%Y-%m-%d")

    try:
        new_amount = float(amount_raw or 0)
    except Exception:
        flash("Invalid payment amount.")
        return redirect("/student-fees")

    if new_amount < 0:
        flash("Payment amount cannot be negative.")
        return redirect("/student-fees")

    conn = get_connection()

    payment = conn.execute("""
        SELECT *
        FROM student_fee_payments
        WHERE id = ?
    """, (payment_id,)).fetchone()

    if not payment:
        conn.close()
        flash("Payment record not found.")
        return redirect("/student-fees")

    description = payment["description"] or ""

    # Class-wide fee payment
    if payment["fee_item_id"]:

        fee = conn.execute("""
            SELECT *
            FROM student_fee_items
            WHERE id = ?
        """, (payment["fee_item_id"],)).fetchone()

        if fee:

            other_paid_row = conn.execute("""
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM student_fee_payments
                WHERE fee_item_id = ?
                  AND student_id = ?
                  AND academic_year_id = ?
                  AND id != ?
            """, (
                payment["fee_item_id"],
                payment["student_id"],
                payment["academic_year_id"],
                payment_id
            )).fetchone()

            other_paid = float(other_paid_row["total"] or 0)

            scholarship, scholarship_discounts = get_student_scholarship_discounts(
                conn,
                payment["student_id"],
                payment["academic_year_id"]
            )

            normal_required = float(fee["amount"] or 0)

            adjustment = conn.execute("""
                SELECT adjusted_amount
                FROM student_fee_adjustments
                WHERE student_id = ?
                  AND academic_year_id = ?
                  AND fee_type = 'class'
                  AND fee_item_id = ?
            """, (
                payment["student_id"],
                payment["academic_year_id"],
                fee["id"]
            )).fetchone()

            if adjustment:
                normal_required = float(adjustment["adjusted_amount"] or 0)

            discount_percent = get_class_fee_discount_percent(
                scholarship,
                scholarship_discounts,
                fee["id"]
            )
            adjusted_required = normal_required * (
                1 - (discount_percent / 100.0)
            )

            maximum_allowed = max(
                adjusted_required - other_paid,
                0
            )

            if new_amount > maximum_allowed:
                conn.close()
                flash(
                    "The edited payment cannot exceed the remaining "
                    f"balance. Maximum allowed: {maximum_allowed:.2f}"
                )
                return redirect("/student-fees")

            description = fee["description"]

    # Student-specific fee payment
    elif payment["student_specific_fee_id"]:

        fee = conn.execute("""
            SELECT *
            FROM student_specific_fees
            WHERE id = ?
        """, (payment["student_specific_fee_id"],)).fetchone()

        if fee:

            other_paid_row = conn.execute("""
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM student_fee_payments
                WHERE student_specific_fee_id = ?
                  AND student_id = ?
                  AND academic_year_id = ?
                  AND id != ?
            """, (
                payment["student_specific_fee_id"],
                payment["student_id"],
                payment["academic_year_id"],
                payment_id
            )).fetchone()

            other_paid = float(other_paid_row["total"] or 0)

            normal_required = float(fee["amount"] or 0)
            adjustment = conn.execute("""
                SELECT adjusted_amount
                FROM student_fee_adjustments
                WHERE student_id = ?
                  AND academic_year_id = ?
                  AND fee_type = 'specific'
                  AND fee_item_id = ?
            """, (
                payment["student_id"],
                payment["academic_year_id"],
                payment["student_specific_fee_id"]
            )).fetchone()

            if adjustment:
                normal_required = float(adjustment["adjusted_amount"] or 0)

            maximum_allowed = max(normal_required - other_paid, 0)

            if new_amount > maximum_allowed:
                conn.close()
                flash(
                    "The edited payment cannot exceed the remaining "
                    f"balance. Maximum allowed: {maximum_allowed:.2f}"
                )
                return redirect("/student-fees")

            description = fee["description"]

    conn.execute("""
        UPDATE student_fee_payments
        SET amount = ?,
            payment_date = ?,
            payment_method = ?,
            reference = ?,
            description = ?
        WHERE id = ?
    """, (
        new_amount,
        payment_date,
        payment_method,
        reference,
        description,
        payment_id
    ))

    conn.commit()
    conn.close()

    flash("Student payment updated successfully.")
    return redirect("/student-fees")


# ============================================================
# STUDENT FEE PAYMENT RECEIPT
# ============================================================

@app.route("/student-fees/receipt/<int:payment_id>")
def student_fee_receipt(payment_id):

    if not login_required():
        return redirect("/")

    conn = get_connection()

    payment = conn.execute("""
        SELECT
            id,
            student_id,
            academic_year_id
        FROM student_fee_payments
        WHERE id = ?
        LIMIT 1
    """, (payment_id,)).fetchone()

    conn.close()

    if not payment:
        flash("Payment record not found.")
        return redirect("/student-fees")

    return redirect(
        "/student-fees/student-receipt/"
        f"{payment['student_id']}"
        f"?academic_year_id={payment['academic_year_id']}"
    )


# ============================================================
# STUDENT FEE RECEIPT / STATEMENT
# ============================================================

@app.route("/student-fees/student-receipt/<int:student_id>")
def student_fee_student_receipt(student_id):

    if not login_required():
        return redirect("/")

    conn = get_connection()

    # --------------------------------------------------------
    # SCHOOL INFORMATION
    # --------------------------------------------------------

    school_settings = conn.execute("""
        SELECT *
        FROM school_settings
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()

    # --------------------------------------------------------
    # ACTIVE ACADEMIC YEARS
    # --------------------------------------------------------

    active_years = conn.execute("""
        SELECT *
        FROM academic_years
        WHERE is_active = 1
        ORDER BY id DESC
    """).fetchall()

    if not active_years:

        latest_year = conn.execute("""
            SELECT *
            FROM academic_years
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

        if latest_year:
            active_years = [latest_year]

    if not active_years:

        conn.close()
        flash("No academic year is available.")
        return redirect("/academic-years")

    requested_year = request.args.get(
        "academic_year_id",
        ""
    ).strip()

    selected_year = None

    if requested_year.isdigit():

        for year in active_years:

            if year["id"] == int(requested_year):
                selected_year = year
                break

    if selected_year is None:
        selected_year = active_years[0]

    # --------------------------------------------------------
    # SOURCE STUDENT NUMBER
    # --------------------------------------------------------

    source_student = conn.execute("""
        SELECT student_id
        FROM students
        WHERE id = ?
        LIMIT 1
    """, (student_id,)).fetchone()

    if not source_student:

        conn.close()
        flash("Student record not found.")
        return redirect("/student-fees")

    # --------------------------------------------------------
    # STUDENT LOOKUP
    # Use the student's existing registration if they have no
    # separate registration record for the selected academic year.

    student = conn.execute("""
        SELECT
            students.id,
            students.student_id,
            students.first_name,
            students.last_name,
            students.class_id,
            classes.class_name
        FROM students
        LEFT JOIN classes ON students.class_id = classes.id
        WHERE students.student_id = ?
        ORDER BY
            CASE
                WHEN students.academic_year_id = ? THEN 0
                ELSE 1
            END,
            students.id DESC
        LIMIT 1
    """, (
        source_student["student_id"],
        selected_year["id"]
    )).fetchone()

    if not student:
        conn.close()
        flash("Student record not found.")
        return redirect("/student-fees")

    # SCHOLARSHIP / BILLING ADJUSTMENT
    # --------------------------------------------------------

    scholarship, scholarship_discounts = get_student_scholarship_discounts(
        conn,
        student["id"],
        selected_year["id"]
    )

    # --------------------------------------------------------
    # ALL CLASS FEES
    # --------------------------------------------------------

    class_fees = conn.execute("""
        SELECT
            id,
            description,
            amount
        FROM student_fee_items
        WHERE class_id = ?
          AND academic_year_id = ?
        ORDER BY id
    """, (
        student["class_id"],
        selected_year["id"]
    )).fetchall()

    # --------------------------------------------------------
    # ALL STUDENT-SPECIFIC FEES
    # --------------------------------------------------------

    specific_fees = conn.execute("""
        SELECT
            id,
            description,
            amount
        FROM student_specific_fees
        WHERE student_id = ?
          AND academic_year_id = ?
        ORDER BY id
    """, (
        student["id"],
        selected_year["id"]
    )).fetchall()

    fee_rows = []

    total_required = 0.0
    total_paid = 0.0
    total_balance = 0.0

    for fee in class_fees:

        normal_required = float(fee["amount"] or 0)

        adjustment = conn.execute("""
            SELECT adjusted_amount
            FROM student_fee_adjustments
            WHERE student_id = ?
              AND academic_year_id = ?
              AND fee_type = 'class'
              AND fee_item_id = ?
        """, (
            student["id"],
            selected_year["id"],
            fee["id"]
        )).fetchone()

        if adjustment:
            normal_required = float(adjustment["adjusted_amount"] or 0)

        discount_percent = get_class_fee_discount_percent(
            scholarship,
            scholarship_discounts,
            fee["id"]
        )
        scholarship_amount = normal_required * (
            discount_percent / 100.0
        )
        required = max(normal_required - scholarship_amount, 0)

        paid_row = conn.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total_paid
            FROM student_fee_payments
            WHERE student_id = ?
              AND class_id = ?
              AND academic_year_id = ?
              AND fee_item_id = ?
        """, (
            student["id"],
            student["class_id"],
            selected_year["id"],
            fee["id"]
        )).fetchone()

        paid = float(paid_row["total_paid"] or 0)
        balance = max(required - paid, 0)

        fee_rows.append({
            "description": fee["description"],
            "normal_required": normal_required,
            "discount_percent": discount_percent,
            "scholarship_amount": scholarship_amount,
            "required": required,
            "paid": paid,
            "balance": balance,
            "type": "Class Fee"
        })

        total_required += required
        total_paid += paid
        total_balance += balance

    for fee in specific_fees:

        required = float(fee["amount"] or 0)

        adjustment = conn.execute("""
            SELECT adjusted_amount
            FROM student_fee_adjustments
            WHERE student_id = ?
              AND academic_year_id = ?
              AND fee_type = 'specific'
              AND fee_item_id = ?
        """, (
            student["id"],
            selected_year["id"],
            fee["id"]
        )).fetchone()

        if adjustment:
            required = float(adjustment["adjusted_amount"] or 0)

        paid_row = conn.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total_paid
            FROM student_fee_payments
            WHERE student_id = ?
              AND class_id = ?
              AND academic_year_id = ?
              AND student_specific_fee_id = ?
        """, (
            student["id"],
            student["class_id"],
            selected_year["id"],
            fee["id"]
        )).fetchone()

        paid = float(paid_row["total_paid"] or 0)
        balance = max(required - paid, 0)

        fee_rows.append({
            "description": fee["description"],
            "normal_required": required,
            "discount_percent": 0.0,
            "scholarship_amount": 0.0,
            "required": required,
            "paid": paid,
            "balance": balance,
            "type": "Student Fee"
        })

        total_required += required
        total_paid += paid
        total_balance += balance

    # --------------------------------------------------------
    # ALL PAYMENT RECORDS
    # --------------------------------------------------------

    payments = conn.execute("""
        SELECT
            student_fee_payments.id,
            student_fee_payments.amount,
            student_fee_payments.payment_date,
            student_fee_payments.payment_method,
            student_fee_payments.reference,
            student_fee_payments.description,
            COALESCE(
                student_fee_items.description,
                student_specific_fees.description,
                student_fee_payments.description
            ) AS fee_description
        FROM student_fee_payments
        LEFT JOIN student_fee_items
            ON student_fee_payments.fee_item_id = student_fee_items.id
        LEFT JOIN student_specific_fees
            ON student_fee_payments.student_specific_fee_id = student_specific_fees.id
        WHERE student_fee_payments.student_id = ?
          AND student_fee_payments.academic_year_id = ?
        ORDER BY student_fee_payments.payment_date DESC,
                 student_fee_payments.id DESC
    """, (
        student["id"],
        selected_year["id"]
    )).fetchall()

    payment_total = sum(
        float(payment["amount"] or 0)
        for payment in payments
    )

    logo_url = None

    if school_settings and school_settings["school_logo"]:
        logo_url = (
            "/static/uploads/"
            + school_settings["school_logo"]
        )

    receipt_number = (
        f"REC-{student['student_id']}-"
        f"{selected_year['id']:03d}"
    )

    conn.close()

    return render_template(
        "student_fee_receipt.html",
        school_settings=school_settings,
        logo_url=logo_url,
        active_years=active_years,
        selected_year=selected_year,
        student=student,
        scholarship=scholarship,
        fee_rows=fee_rows,
        payments=payments,
        total_required=total_required,
        total_paid=total_paid,
        total_balance=total_balance,
        payment_total=payment_total,
        receipt_number=receipt_number,
        printed_at=datetime.now().strftime("%Y-%m-%d %H:%M")
    )


# ============================================================
# DELETE STUDENT PAYMENT
# ============================================================

@app.route("/student-fees/delete-payment/<int:payment_id>")
@app.route("/student-fees/payment/delete/<int:payment_id>")
def delete_student_payment(payment_id):

    if not login_required():
        return redirect("/")

    conn = get_connection()

    payment = conn.execute("""
        SELECT id
        FROM student_fee_payments
        WHERE id = ?
    """, (
        payment_id,
    )).fetchone()

    if not payment:

        conn.close()

        flash(
            "Payment record not found."
        )

        return redirect("/student-fees")

    conn.execute("""
        DELETE FROM student_fee_payments
        WHERE id = ?
    """, (
        payment_id,
    ))

    conn.commit()
    conn.close()

    flash(
        "Student payment deleted successfully."
    )

    return redirect("/student-fees")


# ============================================================
# CLASS BILL - VIEW, ADD, UPDATE AND DELETE
# ============================================================

@app.route("/student-fees/class-bill/<int:class_id>", methods=["GET", "POST"])
def class_fee_bill(class_id):

    if not login_required():
        return redirect("/")

    active_year = get_active_academic_year()

    if not active_year:
        flash("Please activate an academic year first.")
        return redirect("/academic-years")

    conn = get_connection()

    school_class = conn.execute("""
        SELECT id, class_name, description
        FROM classes
        WHERE id = ?
        LIMIT 1
    """, (class_id,)).fetchone()

    if not school_class:
        conn.close()
        flash("Class not found.")
        return redirect("/student-fees")

    if request.method == "POST":

        action = request.form.get("action", "").strip()

        if action == "add_class_fee":

            description = request.form.get("description", "").strip()
            amount_raw = request.form.get("amount", "0").strip()

            try:
                amount = float(amount_raw or 0)
            except Exception:
                amount = 0

            if not description:
                flash("Please enter the bill description.")

            elif amount <= 0:
                flash("Bill amount must be greater than zero.")

            else:
                duplicate = conn.execute("""
                    SELECT id
                    FROM student_fee_items
                    WHERE class_id = ?
                      AND academic_year_id = ?
                      AND LOWER(TRIM(description)) = LOWER(TRIM(?))
                    LIMIT 1
                """, (
                    class_id,
                    active_year["id"],
                    description
                )).fetchone()

                if duplicate:
                    flash(
                        "A bill with this description already exists "
                        "for this class and academic year."
                    )
                else:
                    conn.execute("""
                        INSERT INTO student_fee_items (
                            class_id,
                            academic_year_id,
                            description,
                            amount,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        class_id,
                        active_year["id"],
                        description,
                        amount,
                        datetime.now().isoformat()
                    ))
                    conn.commit()
                    flash(
                        f"{description} has been added to the "
                        f"{school_class['class_name']} class bill. "
                        "It will automatically appear in every student "
                        "record for this class."
                    )

            conn.close()
            return redirect(f"/student-fees/class-bill/{class_id}")

        elif action == "update_class_fee":

            fee_id = request.form.get("fee_id", "").strip()
            description = request.form.get("description", "").strip()
            amount_raw = request.form.get("amount", "0").strip()

            try:
                amount = float(amount_raw or 0)
            except Exception:
                amount = 0

            fee = conn.execute("""
                SELECT *
                FROM student_fee_items
                WHERE id = ?
                  AND class_id = ?
                  AND academic_year_id = ?
                LIMIT 1
            """, (
                fee_id,
                class_id,
                active_year["id"]
            )).fetchone()

            if not fee:
                flash("Class bill record not found.")

            elif not description:
                flash("Bill description cannot be empty.")

            elif amount <= 0:
                flash("Bill amount must be greater than zero.")

            else:
                duplicate = conn.execute("""
                    SELECT id
                    FROM student_fee_items
                    WHERE class_id = ?
                      AND academic_year_id = ?
                      AND LOWER(TRIM(description)) = LOWER(TRIM(?))
                      AND id != ?
                    LIMIT 1
                """, (
                    class_id,
                    active_year["id"],
                    description,
                    fee_id
                )).fetchone()

                if duplicate:
                    flash(
                        "Another bill with this description already "
                        "exists for this class."
                    )

                else:
                    paid_row = conn.execute("""
                        SELECT COALESCE(SUM(amount), 0) AS total_paid
                        FROM student_fee_payments
                        WHERE fee_item_id = ?
                          AND academic_year_id = ?
                    """, (
                        fee_id,
                        active_year["id"]
                    )).fetchone()

                    already_paid = float(paid_row["total_paid"] or 0)

                    if amount < already_paid:
                        flash(
                            "The bill amount cannot be reduced below "
                            f"the amount already paid ({already_paid:.2f})."
                        )

                    else:
                        conn.execute("""
                            UPDATE student_fee_items
                            SET description = ?,
                                amount = ?
                            WHERE id = ?
                              AND class_id = ?
                              AND academic_year_id = ?
                        """, (
                            description,
                            amount,
                            fee_id,
                            class_id,
                            active_year["id"]
                        ))
                        conn.commit()
                        flash(
                            "Class bill updated successfully. The change "
                            "is now reflected in all student records for "
                            "this class."
                        )

            conn.close()
            return redirect(f"/student-fees/class-bill/{class_id}")

        elif action == "delete_class_fee":

            fee_id = request.form.get("fee_id", "").strip()

            fee = conn.execute("""
                SELECT id, description
                FROM student_fee_items
                WHERE id = ?
                  AND class_id = ?
                  AND academic_year_id = ?
                LIMIT 1
            """, (
                fee_id,
                class_id,
                active_year["id"]
            )).fetchone()

            if not fee:
                flash("Class bill record not found.")

            else:
                conn.execute("""
                    DELETE FROM student_fee_items
                    WHERE id = ?
                      AND class_id = ?
                      AND academic_year_id = ?
                """, (
                    fee_id,
                    class_id,
                    active_year["id"]
                ))
                conn.commit()
                flash(
                    f"Class bill '{fee['description']}' deleted. "
                    "It has been removed from all student balances "
                    "for this class."
                )

            conn.close()
            return redirect(f"/student-fees/class-bill/{class_id}")

        elif action == "delete_all_class_fees":

            count_row = conn.execute("""
                SELECT COUNT(*) AS total
                FROM student_fee_items
                WHERE class_id = ?
                  AND academic_year_id = ?
            """, (
                class_id,
                active_year["id"]
            )).fetchone()

            count = int(count_row["total"] or 0)

            conn.execute("""
                DELETE FROM student_fee_items
                WHERE class_id = ?
                  AND academic_year_id = ?
            """, (
                class_id,
                active_year["id"]
            ))
            conn.commit()

            if count:
                flash(
                    f"The complete bill for {school_class['class_name']} "
                    f"has been deleted for {active_year['year_name']}. "
                    "The deleted bills will no longer appear in student "
                    "balances for this class."
                )
            else:
                flash("There were no class bills to delete.")

            conn.close()
            return redirect("/student-fees")

    fees = conn.execute("""
        SELECT
            id,
            description,
            amount
        FROM student_fee_items
        WHERE class_id = ?
          AND academic_year_id = ?
        ORDER BY id
    """, (
        class_id,
        active_year["id"]
    )).fetchall()

    student_count = conn.execute("""
        SELECT COUNT(*) AS total
        FROM students
        WHERE class_id = ?
          AND academic_year_id = ?
    """, (
        class_id,
        active_year["id"]
    )).fetchone()["total"]

    total_bill = sum(
        float(fee["amount"] or 0)
        for fee in fees
    )

    conn.close()

    return render_template(
        "class_fee_bill.html",
        school_class=school_class,
        active_year=active_year,
        fees=fees,
        total_bill=total_bill,
        student_count=student_count
    )


# ============================================================
# DELETE STORED FEE
# ============================================================

@app.route("/student-fees/item/delete/<int:item_id>")
def delete_fee_item(item_id):

    if not login_required():
        return redirect("/")

    conn = get_connection()

    payment_count = conn.execute("""
        SELECT COUNT(*) AS total
        FROM student_fee_payments
        WHERE fee_item_id = ?
    """, (
        item_id,
    )).fetchone()["total"]

    if payment_count > 0:

        conn.close()

        flash(
            "This fee cannot be deleted because payments "
            "have already been recorded against it."
        )

        return redirect("/student-fees")

    conn.execute("""
        DELETE FROM student_fee_items
        WHERE id = ?
    """, (
        item_id,
    ))

    conn.commit()
    conn.close()

    flash(
        "Stored fee deleted."
    )

    return redirect("/student-fees")


# ============================================================
# REPORTS
# ============================================================




def build_financial_student_filters(student_ids, student_status, fee_descriptions, student_id_fallback=""):
    """
    Builds reusable SQL filter fragments for financial reports.
    Returns (sql_fragment, params).
    """

    conditions = []
    params = []

    selected_students = [str(x).strip() for x in (student_ids or []) if str(x).strip()]

    if not selected_students and student_id_fallback:
        selected_students = [str(student_id_fallback).strip()]

    if selected_students:
        placeholders = ",".join(["?"] * len(selected_students))
        conditions.append(f"students.id IN ({placeholders})")
        params.extend(selected_students)

    if student_status == "active":
        conditions.append("LOWER(COALESCE(students.status, 'Active')) = 'active'")
    elif student_status == "inactive":
        conditions.append("LOWER(COALESCE(students.status, 'Active')) <> 'active'")

    if fee_descriptions:
        cleaned_descriptions = [
            str(x).strip().lower()
            for x in fee_descriptions
            if str(x).strip() and str(x).strip() != "__ALL__"
        ]

        if cleaned_descriptions:
            placeholders = ",".join(["?"] * len(cleaned_descriptions))
            conditions.append(
                f"LOWER(TRIM(student_fee_items.description)) IN ({placeholders})"
            )
            params.extend(cleaned_descriptions)

    if conditions:
        return " AND " + " AND ".join(conditions), params

    return "", []



# ============================================================
# FINANCIAL REPORTS - READ ONLY
# This route only reads existing student fee payment records.
# ============================================================

@app.route("/financial-reports")
def financial_reports():
    if not login_required():
        return redirect("/")

    conn = get_connection()

    try:
        settings = conn.execute("""
            SELECT * FROM school_settings ORDER BY id DESC LIMIT 1
        """).fetchone()

        classes = conn.execute("""
            SELECT id, class_name FROM classes ORDER BY class_name
        """).fetchall()

        descriptions = conn.execute("""
            SELECT DISTINCT TRIM(description) AS description
            FROM student_fee_payments
            WHERE academic_year_id = (
                SELECT id FROM academic_years
                WHERE is_active = 1 ORDER BY id DESC LIMIT 1
            )
              AND description IS NOT NULL AND TRIM(description) <> ''
            ORDER BY description
        """).fetchall()

        class_id = request.args.get("class_id", "").strip()
        description = request.args.get("description", "").strip()
        student_status = request.args.get("student_status", "all").strip().lower()
        period = request.args.get("period", "monthly").strip().lower()

        today = datetime.now().date()
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()

        if period == "daily":
            selected_day = start_date or end_date or today.isoformat()
            datetime.strptime(selected_day, "%Y-%m-%d")
            start_date = end_date = selected_day

        elif period == "weekly":
            monday = today - __import__("datetime").timedelta(days=today.weekday())
            sunday = monday + __import__("datetime").timedelta(days=6)
            start_date, end_date = monday.isoformat(), sunday.isoformat()

        elif period == "monthly":
            first = today.replace(day=1)
            if today.month == 12:
                next_month = today.replace(year=today.year + 1, month=1, day=1)
            else:
                next_month = today.replace(month=today.month + 1, day=1)
            last = next_month - __import__("datetime").timedelta(days=1)
            start_date, end_date = first.isoformat(), last.isoformat()

        elif period == "yearly":
            start_date = f"{today.year}-01-01"
            end_date = f"{today.year}-12-31"

        elif period == "custom":
            for value in (start_date, end_date):
                if value:
                    datetime.strptime(value, "%Y-%m-%d")
            if start_date and end_date and start_date > end_date:
                raise ValueError("Start date cannot be after end date.")

        elif period == "all":
            start_date = end_date = ""

        else:
            period = "monthly"
            first = today.replace(day=1)
            if today.month == 12:
                next_month = today.replace(year=today.year + 1, month=1, day=1)
            else:
                next_month = today.replace(month=today.month + 1, day=1)
            last = next_month - __import__("datetime").timedelta(days=1)
            start_date, end_date = first.isoformat(), last.isoformat()

        active_year = conn.execute("""
            SELECT id, year_name FROM academic_years
            WHERE is_active = 1
            ORDER BY id DESC LIMIT 1
        """).fetchone()

        if not active_year:
            raise ValueError("Please activate an academic year before running this report.")

        active_year_id = active_year["id"]

        # Calculate fees and payments separately for each student.
        student_conditions = ["s.class_id IS NOT NULL"]
        student_params = []

        if class_id:
            student_conditions.append("s.class_id = ?")
            student_params.append(class_id)

        if student_status in ("active", "inactive"):
            student_conditions.append(
                "LOWER(TRIM(COALESCE(s.status, 'active'))) = ?"
            )
            student_params.append(student_status)

        fee_conditions = ["fi.academic_year_id = ?"]
        fee_params = [active_year_id]

        if description:
            fee_conditions.append(
                "LOWER(TRIM(fi.description)) = LOWER(?)"
            )
            fee_params.append(description)

        payment_conditions = ["p.academic_year_id = ?"]
        payment_params = [active_year_id]

        if class_id:
            payment_conditions.append("p.class_id = ?")
            payment_params.append(class_id)

        if description:
            payment_conditions.append(
                "LOWER(TRIM(COALESCE(p.description, ''))) = LOWER(?)"
            )
            payment_params.append(description)

        if student_status in ("active", "inactive"):
            payment_conditions.append(
                "LOWER(TRIM(COALESCE(s.status, 'active'))) = ?"
            )
            payment_params.append(student_status)

        if start_date:
            payment_conditions.append("date(p.payment_date) >= date(?)")
            payment_params.append(start_date)

        if end_date:
            payment_conditions.append("date(p.payment_date) <= date(?)")
            payment_params.append(end_date)

        # Use the student's placement in the active academic year,
        # matching the student-selection logic in View All Reports.
        placement_join = ""
        placement_class = "s.class_id"

        if conn.execute("""
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'student_class_placements'
        """).fetchone():
            placement_join = """
                LEFT JOIN student_class_placements placement
                    ON placement.student_db_id = s.id
                    AND placement.academic_year_id = ?
            """
            placement_class = "COALESCE(placement.class_id, s.class_id)"
            student_params.insert(0, active_year_id)

        student_conditions = [
            "s.academic_year_id = ?",
            f"{placement_class} IS NOT NULL"
        ]
        student_params.append(active_year_id)

        if class_id:
            student_conditions.append(f"{placement_class} = ?")
            student_params.append(class_id)

        if student_status in ("active", "inactive"):
            student_conditions.append(
                "LOWER(TRIM(COALESCE(s.status, 'active'))) = ?"
            )
            student_params.append(student_status)

        fee_conditions = ["fi.academic_year_id = ?"]
        fee_params = [active_year_id]

        if description:
            fee_conditions.append(
                "LOWER(TRIM(fi.description)) = LOWER(?)"
            )
            fee_params.append(description)

        payment_conditions = ["p.academic_year_id = ?"]
        payment_params = [active_year_id]

        if class_id:
            payment_conditions.append("p.class_id = ?")
            payment_params.append(class_id)

        if description:
            payment_conditions.append(
                "LOWER(TRIM(COALESCE(p.description, ''))) = LOWER(?)"
            )
            payment_params.append(description)

        if student_status in ("active", "inactive"):
            payment_conditions.append(
                "LOWER(TRIM(COALESCE(s.status, 'active'))) = ?"
            )
            payment_params.append(student_status)

        if start_date:
            payment_conditions.append("date(p.payment_date) >= date(?)")
            payment_params.append(start_date)

        if end_date:
            payment_conditions.append("date(p.payment_date) <= date(?)")
            payment_params.append(end_date)

        # Include students with zero payments using LEFT JOIN.
        rows = conn.execute("""
            WITH fee_totals AS (
                SELECT
                    fi.class_id,
                    SUM(COALESCE(fi.amount, 0)) AS required_amount
                FROM student_fee_items fi
                WHERE """ + " AND ".join(fee_conditions) + """
                GROUP BY fi.class_id
            ),
            payment_totals AS (
                SELECT
                    p.student_id,
                    p.class_id,
                    SUM(COALESCE(p.amount, 0)) AS amount_paid,
                    MAX(p.payment_date) AS payment_date,
                    COUNT(*) AS payment_records
                FROM student_fee_payments p
                JOIN students s ON s.id = p.student_id
                WHERE """ + " AND ".join(payment_conditions) + """
                GROUP BY p.student_id, p.class_id
            )
            SELECT
                s.id AS student_record_id,
                s.student_id AS admission_number,
                s.first_name,
                s.last_name,
                COALESCE(s.status, 'Unknown') AS student_status,
                """ + placement_class + """ AS class_id,
                c.class_name,
                COALESCE(f.required_amount, 0) AS required_amount,
                COALESCE(p.amount_paid, 0) AS amount_paid,
                p.payment_date,
                COALESCE(p.payment_records, 0) AS payment_records
            FROM students s
            """ + placement_join + """
            LEFT JOIN classes c ON c.id = """ + placement_class + """
            LEFT JOIN fee_totals f ON f.class_id = """ + placement_class + """
            LEFT JOIN payment_totals p
                ON p.student_id = s.id
                AND p.class_id = """ + placement_class + """
            WHERE """ + " AND ".join(student_conditions) + """
            ORDER BY c.class_name, s.last_name, s.first_name
        """, fee_params + payment_params + student_params).fetchall()

        # Calculate each student's required fees after scholarship discounts.
        # This changes report calculations only; it does not update the database.
        adjusted_rows = []

        fee_item_sql = """
            SELECT id, amount
            FROM student_fee_items
            WHERE academic_year_id = ?
              AND class_id = ?
        """
        fee_item_params_base = [active_year_id]

        if description:
            fee_item_sql += " AND LOWER(TRIM(description)) = LOWER(?)"

        for report_row in rows:
            student_row = dict(report_row)
            student_class_id = student_row["class_id"]
            student_id = student_row["student_record_id"]

            if student_class_id is None:
                required_after_discount = 0.0
            else:
                fee_item_params = list(fee_item_params_base)
                fee_item_params.append(student_class_id)

                if description:
                    fee_item_params.append(description)

                student_fee_items = conn.execute(
                    fee_item_sql,
                    fee_item_params
                ).fetchall()

                scholarship, discount_map = get_student_scholarship_discounts(
                    conn,
                    student_id,
                    active_year_id
                )

                required_after_discount = 0.0

                for fee_item in student_fee_items:
                    original_amount = float(fee_item["amount"] or 0)

                    adjustment = conn.execute("""
                        SELECT adjusted_amount
                        FROM student_fee_adjustments
                        WHERE student_id = ?
                          AND academic_year_id = ?
                          AND fee_type = 'class'
                          AND fee_item_id = ?
                    """, (
                        student_id,
                        active_year_id,
                        fee_item["id"]
                    )).fetchone()

                    if adjustment:
                        original_amount = float(
                            adjustment["adjusted_amount"] or 0
                        )

                    discount_percent = get_class_fee_discount_percent(
                        scholarship,
                        discount_map,
                        fee_item["id"]
                    )

                    discounted_amount = original_amount * (
                        1.0 - discount_percent / 100.0
                    )
                    required_after_discount += max(discounted_amount, 0.0)

            student_row["required_amount"] = required_after_discount
            adjusted_rows.append(student_row)

        # Daily collection report: include only students who actually paid
        # within the selected date range. Do not count their full fees as collections.
        rows = adjusted_rows

        if period == "daily":
            rows = [
                r for r in rows
                if int(r.get("payment_records") or 0) > 0
            ]
            for r in rows:
                r["required_amount"] = 0.0

        total_required = sum(
            float(r["required_amount"] or 0) for r in rows
        )
        total_collected = sum(
            float(r["amount_paid"] or 0) for r in rows
        )
        balance = 0.0 if period == "daily" else sum(
            max(
                float(r["required_amount"] or 0)
                - float(r["amount_paid"] or 0),
                0
            )
            for r in rows
        )
        payment_count = sum(
            int(r["payment_records"] or 0) for r in rows
        )

        logo_url = (
            "/static/uploads/" + settings["school_logo"]
            if settings and settings["school_logo"] else None
        )

        return render_template(
            "financial_reports.html",
            school_settings=settings,
            active_year=active_year,
            logo_url=logo_url,
            classes=classes,
            descriptions=descriptions,
            rows=rows,
            total_required=total_required,
            total_collected=total_collected,
            balance=balance,
            payment_count=payment_count,
            class_id=class_id,
            description=description,
            student_status=student_status,
            period=period,
            start_date=start_date,
            end_date=end_date,
            error_message=None
        )

    except ValueError as exc:
        settings = conn.execute(
            "SELECT * FROM school_settings ORDER BY id DESC LIMIT 1"
        ).fetchone()
        classes = conn.execute(
            "SELECT id, class_name FROM classes ORDER BY class_name"
        ).fetchall()
        descriptions = conn.execute("""
            SELECT DISTINCT TRIM(description) AS description
            FROM student_fee_payments
            WHERE academic_year_id = (
                SELECT id FROM academic_years
                WHERE is_active = 1 ORDER BY id DESC LIMIT 1
            )
              AND description IS NOT NULL AND TRIM(description) <> ''
            ORDER BY description
        """).fetchall()

        active_year = conn.execute("""
            SELECT id, year_name FROM academic_years
            WHERE is_active = 1 ORDER BY id DESC LIMIT 1
        """).fetchone()

        return render_template(
            "financial_reports.html",
            school_settings=settings,
            active_year=active_year,
            logo_url=(
                "/static/uploads/" + settings["school_logo"]
                if settings and settings["school_logo"] else None
            ),
            classes=classes,
            descriptions=descriptions,
            rows=[],
            total_required=0,
            total_collected=0,
            balance=0,
            payment_count=0,
            class_id=request.args.get("class_id", ""),
            description=request.args.get("description", ""),
            student_status=request.args.get("student_status", "all"),
            period=request.args.get("period", "monthly"),
            start_date=request.args.get("start_date", ""),
            end_date=request.args.get("end_date", ""),
            error_message=str(exc)
        )

    finally:
        conn.close()

@app.route("/teacher-attendance-reports")
def teacher_attendance_reports():
    if not login_required():
        return redirect("/")

    conn = get_connection()

    try:
        active_year = get_active_academic_year()

        if not active_year:
            flash("Please activate an academic year before generating teacher attendance reports.")
            return redirect("/academic-years")

        report_type = request.args.get("report_type", "daily").lower()
        if report_type not in ("daily", "weekly", "monthly", "yearly"):
            report_type = "daily"

        today = date.today()
        report_date = request.args.get("report_date", today.isoformat())
        report_month = request.args.get("report_month", today.strftime("%Y-%m"))
        report_year = request.args.get("report_year", str(today.year))
        teacher_id = request.args.get("teacher_id", "").strip()

        # Validate date inputs before using them.
        try:
            chosen_date = date.fromisoformat(report_date)
        except (TypeError, ValueError):
            chosen_date = today
            report_date = today.isoformat()

        try:
            chosen_month = datetime.strptime(report_month, "%Y-%m").date()
        except (TypeError, ValueError):
            chosen_month = today.replace(day=1)
            report_month = chosen_month.strftime("%Y-%m")

        try:
            chosen_year = int(report_year)
            if not 2000 <= chosen_year <= 2100:
                raise ValueError
        except (TypeError, ValueError):
            chosen_year = today.year
            report_year = str(today.year)

        teachers = conn.execute("""
            SELECT id, teacher_id, first_name, last_name
            FROM teachers
            ORDER BY first_name, last_name
        """).fetchall()

        query = """
            SELECT
                teachers.teacher_id AS teacher_code,
                teachers.first_name,
                teachers.last_name,
                teacher_attendance.period,
                teacher_attendance.attendance_date,
                teacher_attendance.time_recorded,
                teacher_attendance.status,
                teacher_attendance.remarks
            FROM teacher_attendance
            JOIN teachers
                ON teacher_attendance.teacher_id = teachers.id
            WHERE teacher_attendance.academic_year_id = ?
        """
        params = [active_year["id"]]

        if teacher_id:
            try:
                params.append(int(teacher_id))
                query += " AND teachers.id = ?"
            except ValueError:
                teacher_id = ""

        if report_type == "daily":
            query += " AND teacher_attendance.attendance_date = ?"
            params.append(chosen_date.isoformat())

        elif report_type == "weekly":
            week_start = chosen_date - timedelta(days=chosen_date.weekday())
            week_end = week_start + timedelta(days=6)
            query += """
                AND teacher_attendance.attendance_date >= ?
                AND teacher_attendance.attendance_date <= ?
            """
            params.extend([week_start.isoformat(), week_end.isoformat()])

        elif report_type == "monthly":
            month_start = chosen_month.replace(day=1)
            if chosen_month.month == 12:
                next_month = date(chosen_month.year + 1, 1, 1)
            else:
                next_month = date(chosen_month.year, chosen_month.month + 1, 1)

            query += """
                AND teacher_attendance.attendance_date >= ?
                AND teacher_attendance.attendance_date < ?
            """
            params.extend([month_start.isoformat(), next_month.isoformat()])

        elif report_type == "yearly":
            query += """
                AND teacher_attendance.attendance_date >= ?
                AND teacher_attendance.attendance_date < ?
            """
            params.extend([
                date(chosen_year, 1, 1).isoformat(),
                date(chosen_year + 1, 1, 1).isoformat()
            ])

        query += """
            ORDER BY
                teacher_attendance.attendance_date DESC,
                teacher_attendance.id DESC
        """

        attendance_records = conn.execute(query, params).fetchall()

        total_records = len(attendance_records)
        present_count = sum(
            1 for row in attendance_records
            if str(row["status"] or "").strip().lower() == "present"
        )
        absent_count = sum(
            1 for row in attendance_records
            if str(row["status"] or "").strip().lower() == "absent"
        )
        leave_count = sum(
            1 for row in attendance_records
            if str(row["status"] or "").strip().lower() == "leave"
        )
        attendance_percentage = (
            round(present_count * 100 / total_records, 1)
            if total_records else 0
        )

        return render_template(
            "teacher_attendance_report.html",
            active_year=active_year,
            teachers=teachers,
            report_type=report_type,
            report_date=report_date,
            report_month=report_month,
            report_year=report_year,
            selected_teacher=teacher_id,
            attendance_records=attendance_records,
            total_records=total_records,
            present_count=present_count,
            absent_count=absent_count,
            leave_count=leave_count,
            attendance_percentage=attendance_percentage
        )

    finally:
        conn.close()


@app.route("/reports")
def reports():
    if not login_required():
        return redirect("/")

    conn = get_connection()

    school_settings = conn.execute("""
        SELECT *
        FROM school_settings
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()


    report_type = request.args.get("report_type", "students")
    print("REPORT DEBUG - RECEIVED report_type =", repr(report_type), "URL =", request.url)
    academic_year_id = request.args.get("academic_year_id", "")
    class_id = request.args.get("class_id", "")
    teacher_id = request.args.get("teacher_id", "")
    student_id = request.args.get("student_id", "")
    student_ids = request.args.getlist("student_ids")
    fee_item_id = request.args.get("fee_item_id", "")
    fee_item_ids = request.args.getlist("fee_item_ids")
    student_status = request.args.get("student_status", "all")
    search = request.args.get("search", "").strip()
    student_ids = [x.strip() for x in student_ids if x and x.strip()]
    if not student_ids and student_id:
        student_ids = [student_id]
    fee_descriptions = request.args.getlist("fee_descriptions")
    fee_descriptions = [x.strip() for x in fee_descriptions if x and x.strip() and x.strip() != "__ALL__"]

    academic_years = conn.execute(
        "SELECT * FROM academic_years ORDER BY id DESC"
    ).fetchall()

    classes = conn.execute(
        "SELECT * FROM classes ORDER BY class_name"
    ).fetchall()

    teachers = conn.execute(
        "SELECT * FROM teachers ORDER BY first_name, last_name"
    ).fetchall()

    students = conn.execute(
        "SELECT * FROM students ORDER BY first_name, last_name"
    ).fetchall()

    # All stored fee descriptions are available for custom payment reports.
    fee_items_query = """
        SELECT MIN(id) AS id, description
        FROM student_fee_items
        WHERE description IS NOT NULL
          AND TRIM(description) <> ''
    """
    fee_items_params = []

    if academic_year_id:
        fee_items_query += " AND academic_year_id = ?"
        fee_items_params.append(academic_year_id)

    fee_items_query += " GROUP BY description ORDER BY description"
    fee_items = conn.execute(
        fee_items_query,
        fee_items_params
    ).fetchall()

    records = []
    totals = {
        "records": 0,
        "required": 0.0,
        "paid": 0.0,
        "balance": 0.0
    }

    report_title = "School Report"

    # --------------------------------------------------------
    # BASIC STUDENT REPORT
    # --------------------------------------------------------
    if report_type == "students":

        report_title = "Students Report"

        q = """
            SELECT
                students.student_id,
                students.first_name,
                students.last_name,
                students.gender,
                students.status,
                classes.class_name,
                academic_years.year_name
            FROM students
            LEFT JOIN classes
                ON students.class_id = classes.id
            LEFT JOIN academic_years
                ON students.academic_year_id = academic_years.id
            WHERE 1=1
        """
        params = []

        if academic_year_id:
            q += " AND students.academic_year_id = ?"
            params.append(academic_year_id)

        if class_id:
            q += " AND students.class_id = ?"
            params.append(class_id)

        # Support one or multiple selected students.
        selected_student_ids = [str(x).strip() for x in student_ids if str(x).strip()]

        if selected_student_ids:
            placeholders = ",".join(["?"] * len(selected_student_ids))
            q += f" AND students.id IN ({placeholders})"
            params.extend(selected_student_ids)
        elif student_id:
            q += " AND students.id = ?"
            params.append(student_id)

        # Support Active, Inactive, or All students.
        if student_status == "active":
            q += " AND LOWER(COALESCE(students.status, 'Active')) = 'active'"
        elif student_status == "inactive":
            q += " AND LOWER(COALESCE(students.status, 'Active')) <> 'active'"

        if search:
            q += " AND (students.student_id LIKE ? OR students.first_name LIKE ? OR students.last_name LIKE ?)"
            value = f"%{search}%"
            params.extend([value, value, value])

        q += " ORDER BY students.id DESC"
        records = conn.execute(q, params).fetchall()

    # --------------------------------------------------------
    # STUDENT STATISTICS REPORT
    # --------------------------------------------------------
    elif report_type == "student_statistics":

        report_title = "Student Statistics"

        # Use the active academic year unless the user selected one.
        selected_year = None
        if academic_year_id:
            selected_year = next(
                (y for y in academic_years
                 if str(y["id"]) == str(academic_year_id)),
                None
            )
        else:
            selected_year = next(
                (y for y in academic_years if int(y["is_active"] or 0) == 1),
                None
            )
            if selected_year:
                academic_year_id = str(selected_year["id"])

        year_name = selected_year["year_name"] if selected_year else ""
        year_parts = str(year_name).split("/")
        try:
            year_start = int(year_parts[0][:4])
            year_end = int(year_parts[1][:4]) if len(year_parts) > 1 else year_start
        except (ValueError, IndexError):
            year_start = year_end = None

        student_sql = """
            SELECT s.class_id, s.gender, s.status, s.admission_date
            FROM students AS s
            WHERE 1=1
        """
        student_params = []

        if academic_year_id:
            student_sql += """
                AND (
                    s.academic_year_id = ?
                    OR EXISTS (
                        SELECT 1 FROM student_payments AS sp
                        WHERE sp.student_id = s.id
                          AND sp.academic_year_id = ?
                    )
                    OR EXISTS (
                        SELECT 1 FROM student_fee_payments AS sfp
                        WHERE sfp.student_id = s.id
                          AND sfp.academic_year_id = ?
                    )
                    OR EXISTS (
                        SELECT 1 FROM student_attendance AS sa
                        WHERE sa.student_id = s.id
                          AND sa.academic_year_id = ?
                    )
                )
            """
            student_params.extend([academic_year_id] * 4)

        if class_id:
            student_sql += " AND s.class_id = ?"
            student_params.append(class_id)

        statistic_students = conn.execute(
            student_sql, student_params
        ).fetchall()

        def empty_class_stats(class_name):
            return {
                "class_name": class_name,
                "total": 0,
                "male": 0,
                "female": 0,
                "unspecified_gender": 0,
                "new": 0,
                "old": 0,
                "unclassified": 0,
                "active": 0,
                "inactive": 0
            }

        class_stats = {}
        for school_class in classes:
            if class_id and str(school_class["id"]) != str(class_id):
                continue
            class_stats[str(school_class["id"])] = empty_class_stats(
                school_class["class_name"]
            )

        overall = empty_class_stats("OVERALL TOTAL")

        for student in statistic_students:
            key = str(student["class_id"]) if student["class_id"] is not None else ""
            if key not in class_stats:
                if class_id:
                    continue
                class_stats[key] = empty_class_stats("Unassigned")

            row = class_stats[key]
            row["total"] += 1
            overall["total"] += 1

            gender = str(student["gender"] or "").strip().lower()
            if gender == "male":
                row["male"] += 1
                overall["male"] += 1
            elif gender == "female":
                row["female"] += 1
                overall["female"] += 1
            else:
                row["unspecified_gender"] += 1
                overall["unspecified_gender"] += 1

            status = str(student["status"] or "Active").strip().lower()
            status_key = "active" if status == "active" else "inactive"
            row[status_key] += 1
            overall[status_key] += 1

            admission_date = str(student["admission_date"] or "").strip()
            try:
                admission_year = int(admission_date[:4])
            except (ValueError, TypeError):
                admission_year = None

            if admission_year is None or year_start is None:
                classification = "unclassified"
            elif year_start <= admission_year <= year_end:
                classification = "new"
            elif admission_year < year_start:
                classification = "old"
            else:
                classification = "unclassified"

            row[classification] += 1
            overall[classification] += 1

        records = list(class_stats.values())
        records.sort(key=lambda item: item["class_name"].lower())
        records.append(overall)

        totals["records"] = len(records)
        totals.update({
            "total_students": overall["total"],
            "male": overall["male"],
            "female": overall["female"],
            "unspecified_gender": overall["unspecified_gender"],
            "new": overall["new"],
            "old": overall["old"],
            "unclassified": overall["unclassified"],
            "active": overall["active"],
            "inactive": overall["inactive"]
        })

    # --------------------------------------------------------
    # TEACHER REPORT
    # --------------------------------------------------------
    elif report_type == "teachers":

        report_title = "Teachers Report"

        q = """
            SELECT
                teacher_id,
                first_name,
                last_name,
                subject,
                position,
                phone,
                status
            FROM teachers
            WHERE 1=1
        """
        params = []

        if teacher_id:
            q += " AND id = ?"
            params.append(teacher_id)

        if search:
            q += " AND (teacher_id LIKE ? OR first_name LIKE ? OR last_name LIKE ? OR subject LIKE ?)"
            value = f"%{search}%"
            params.extend([value, value, value, value])

        q += " ORDER BY first_name, last_name"
        records = conn.execute(q, params).fetchall()

    # --------------------------------------------------------
    # STUDENT ATTENDANCE
    # --------------------------------------------------------
    elif report_type == "student_attendance":

        report_title = "Student Attendance Report"

        q = """
            SELECT
                students.student_id,
                students.first_name || ' ' || students.last_name AS student_name,
                classes.class_name,
                student_attendance.attendance_date,
                student_attendance.status
            FROM student_attendance
            JOIN students
                ON student_attendance.student_id = students.id
            LEFT JOIN classes
                ON students.class_id = classes.id
            WHERE 1=1
        """
        params = []

        if academic_year_id:
            q += " AND students.academic_year_id = ?"
            params.append(academic_year_id)

        if class_id:
            q += " AND students.class_id = ?"
            params.append(class_id)

        if student_id:
            q += " AND students.id = ?"
            params.append(student_id)

        q += " ORDER BY student_attendance.attendance_date DESC"
        records = conn.execute(q, params).fetchall()

    # --------------------------------------------------------
    # TEACHER ATTENDANCE
    # --------------------------------------------------------
    elif report_type == "teacher_attendance":

        report_title = "Teacher Attendance Report"

        q = """
            SELECT
                teachers.teacher_id,
                teachers.first_name || ' ' || teachers.last_name AS teacher_name,
                teacher_attendance.attendance_date,
                teacher_attendance.status
            FROM teacher_attendance
            JOIN teachers
                ON teacher_attendance.teacher_id = teachers.id
            WHERE 1=1
        """
        params = []

        if teacher_id:
            q += " AND teachers.id = ?"
            params.append(teacher_id)

        q += " ORDER BY teacher_attendance.attendance_date DESC"
        records = conn.execute(q, params).fetchall()

    # --------------------------------------------------------
    # ALL STUDENT PAYMENT HISTORY
    # --------------------------------------------------------

    elif report_type == "financial":
        # ========================================================
        # FINANCIAL REPORT
        # ========================================================

        report_title = "Financial Report"

        # If no individual fee descriptions are selected, the user
        # requested ALL FEES. In that case we summarize each student
        # into one row instead of repeating the student for every fee.
        all_fees_mode = not fee_descriptions

        q = """
            SELECT
                students.id AS student_db_id,
                students.student_id,
                students.first_name,
                students.last_name,
                students.status,
                students.class_id,
                students.academic_year_id,
                classes.class_name,
                academic_years.year_name,
                student_fee_items.id AS fee_item_id,
                student_fee_items.description,
                student_fee_items.amount AS required_amount
            FROM students
            JOIN student_fee_items
                ON students.class_id = student_fee_items.class_id
               AND students.academic_year_id = student_fee_items.academic_year_id
            LEFT JOIN classes
                ON students.class_id = classes.id
            LEFT JOIN academic_years
                ON students.academic_year_id = academic_years.id
            WHERE 1=1
        """

        params = []

        if academic_year_id:
            q += " AND students.academic_year_id = ?"
            params.append(academic_year_id)

        if class_id:
            q += " AND students.class_id = ?"
            params.append(class_id)

        if student_ids:
            placeholders = ",".join(["?"] * len(student_ids))
            q += f" AND students.id IN ({placeholders})"
            params.extend(student_ids)

        if student_status == "active":
            q += """
                AND LOWER(COALESCE(students.status, 'Active')) = 'active'
            """
        elif student_status == "inactive":
            q += """
                AND LOWER(COALESCE(students.status, 'Active')) <> 'active'
            """

        if fee_descriptions:
            placeholders = ",".join(["?"] * len(fee_descriptions))
            q += f"""
                AND LOWER(TRIM(student_fee_items.description))
                    IN ({placeholders})
            """
            params.extend([x.lower() for x in fee_descriptions])

        if search:
            q += """
                AND (
                    students.student_id LIKE ?
                    OR students.first_name LIKE ?
                    OR students.last_name LIKE ?
                    OR student_fee_items.description LIKE ?
                )
            """
            value = f"%{search}%"
            params.extend([value, value, value, value])

        q += """
            ORDER BY
                classes.class_name,
                students.first_name,
                students.last_name,
                student_fee_items.id
        """

        rows = conn.execute(q, params).fetchall()

        class_groups = {}

        for row in rows:

            paid_row = conn.execute("""
                SELECT COALESCE(SUM(amount), 0) AS total_paid
                FROM student_fee_payments
                WHERE student_id = ?
                  AND fee_item_id = ?
                  AND academic_year_id = ?
            """, (
                row["student_db_id"],
                row["fee_item_id"],
                row["academic_year_id"]
            )).fetchone()

            required = float(row["required_amount"] or 0)
            paid = float(paid_row["total_paid"] or 0)
            balance = max(required - paid, 0)

            status = row["status"] or "Active"
            class_name = row["class_name"] or "Unassigned"

            if class_name not in class_groups:
                class_groups[class_name] = {
                    "class_name": class_name,
                    "rows": [],
                    "required": 0,
                    "paid": 0,
                    "balance": 0,
                    "students": {}
                }

            group = class_groups[class_name]

            student_key = str(row["student_db_id"])

            if student_key not in group["students"]:
                group["students"][student_key] = {
                    "student_id": row["student_id"],
                    "student_name": (
                        f'{row["first_name"]} {row["last_name"]}'
                    ).strip(),
                    "class_name": class_name,
                    "status": status,
                    "year_name": row["year_name"] or "",
                    "required_amount": 0,
                    "paid_amount": 0,
                    "balance": 0
                }

            group["students"][student_key]["required_amount"] += required
            group["students"][student_key]["paid_amount"] += paid
            group["students"][student_key]["balance"] += balance

            # Keep individual fee rows available when specific
            # payment descriptions have been selected.
            group["rows"].append({
                "student_id": row["student_id"],
                "student_name": (
                    f'{row["first_name"]} {row["last_name"]}'
                ).strip(),
                "class_name": class_name,
                "status": status,
                "year_name": row["year_name"] or "",
                "description": row["description"] or "",
                "required_amount": required,
                "paid_amount": paid,
                "balance": balance
            })

        # Build class totals from student totals.
        for group in class_groups.values():

            group["required"] = sum(
                student["required_amount"]
                for student in group["students"].values()
            )

            group["paid"] = sum(
                student["paid_amount"]
                for student in group["students"].values()
            )

            group["balance"] = sum(
                student["balance"]
                for student in group["students"].values()
            )

        level_order = {
            "KINDERGARTEN": ["ABC", "K-I", "K-II"],
            "ELEMENTARY": [
                "GRADE 1", "GRADE 2", "GRADE 3",
                "GRADE 4", "GRADE 5", "GRADE 6"
            ],
            "JUNIOR HIGH": ["GRADE 7", "GRADE 8", "GRADE 9"],
            "SENIOR HIGH": ["GRADE 10", "GRADE 11", "GRADE 12"]
        }

        normalized_groups = {
            str(k).strip().upper(): v
            for k, v in class_groups.items()
        }

        financial_records = []

        for level_name, level_classes in level_order.items():

            level_required = 0
            level_paid = 0
            level_balance = 0
            level_has_rows = False

            for cname in level_classes:

                group = normalized_groups.get(cname)

                if not group:
                    continue

                level_has_rows = True

                if all_fees_mode:
                    # ALL FEES MODE:
                    # One row per student.
                    for student in group["students"].values():
                        financial_records.append({
                            "record_type": "student",
                            "display_mode": "student_total",
                            **student
                        })
                else:
                    # SPECIFIC FEE MODE:
                    # Show each selected fee description.
                    for item in group["rows"]:
                        financial_records.append({
                            "record_type": "student",
                            "display_mode": "fee_detail",
                            **item
                        })

                financial_records.append({
                    "record_type": "class_total",
                    "class_name": group["class_name"],
                    "required_amount": group["required"],
                    "paid_amount": group["paid"],
                    "balance": group["balance"]
                })

                level_required += group["required"]
                level_paid += group["paid"]
                level_balance += group["balance"]

            if level_has_rows:
                financial_records.append({
                    "record_type": "level_total",
                    "level_name": level_name,
                    "required_amount": level_required,
                    "paid_amount": level_paid,
                    "balance": level_balance
                })

        standard_classes = set()

        for values in level_order.values():
            standard_classes.update(values)

        for cname, group in normalized_groups.items():

            if cname in standard_classes:
                continue

            if all_fees_mode:
                for student in group["students"].values():
                    financial_records.append({
                        "record_type": "student",
                        "display_mode": "student_total",
                        **student
                    })
            else:
                for item in group["rows"]:
                    financial_records.append({
                        "record_type": "student",
                        "display_mode": "fee_detail",
                        **item
                    })

            financial_records.append({
                "record_type": "class_total",
                "class_name": group["class_name"],
                "required_amount": group["required"],
                "paid_amount": group["paid"],
                "balance": group["balance"]
            })

        # Overall school totals.
        # IMPORTANT:
        # When status = All, inactive students are excluded from the
        # school's overall totals, while they can still be displayed.
        school_required = 0
        school_paid = 0
        school_balance = 0

        for group in class_groups.values():

            for student in group["students"].values():

                item_status = str(
                    student["status"] or "Active"
                ).strip().lower()

                include_school_total = (
                    student_status != "all"
                    or item_status == "active"
                )

                if include_school_total:
                    school_required += student["required_amount"]
                    school_paid += student["paid_amount"]
                    school_balance += student["balance"]

        totals["required"] = school_required
        totals["paid"] = school_paid
        totals["balance"] = school_balance

        totals["records"] = sum(
            1
            for item in financial_records
            if item.get("record_type") == "student"
        )

        records = financial_records

    elif report_type == "student_payments":
        report_title = "Student Payment History"

        q = """
            SELECT
                students.id AS student_db_id,
                students.student_id,
                students.first_name || ' ' || students.last_name AS student_name,
                students.status,
                classes.class_name,
                student_fee_payments.description,
                student_fee_payments.payment_date,
                student_fee_payments.amount,
                student_fee_payments.payment_method
            FROM student_fee_payments
            JOIN students
                ON student_fee_payments.student_id = students.id
            LEFT JOIN classes
                ON students.class_id = classes.id
            LEFT JOIN student_fee_items
                ON student_fee_payments.fee_item_id = student_fee_items.id
            WHERE 1=1
        """
        params = []

        if academic_year_id:
            q += " AND student_fee_payments.academic_year_id = ?"
            params.append(academic_year_id)

        if class_id:
            q += " AND students.class_id = ?"
            params.append(class_id)

        if student_ids:
            placeholders = ",".join(["?"] * len(student_ids))
            q += f" AND students.id IN ({placeholders})"
            params.extend(student_ids)

        if student_status == "active":
            q += " AND LOWER(COALESCE(students.status, 'Active')) = 'active'"
        elif student_status == "inactive":
            q += " AND LOWER(COALESCE(students.status, 'Active')) <> 'active'"

        if fee_descriptions:
            placeholders = ",".join(["?"] * len(fee_descriptions))
            q += """
                AND LOWER(TRIM(COALESCE(
                    student_fee_payments.description,
                    student_fee_items.description,
                    ''
                ))) IN (
            """ + placeholders + ")"
            params.extend([x.lower() for x in fee_descriptions])

        if search:
            q += """
                AND (
                    students.student_id LIKE ?
                    OR students.first_name LIKE ?
                    OR students.last_name LIKE ?
                    OR student_fee_payments.description LIKE ?
                )
            """
            value = f"%{search}%"
            params.extend([value, value, value, value])

        q += """
            ORDER BY
                student_fee_payments.payment_date DESC,
                student_fee_payments.id DESC
        """

        records = []
        payment_rows = conn.execute(q, params).fetchall()

        for row in payment_rows:
            records.append({
                "student_id": row["student_id"],
                "student_name": row["student_name"],
                "class_name": row["class_name"],
                "status": row["status"] or "Active",
                "description": row["description"],
                "payment_date": row["payment_date"],
                "amount": float(row["amount"] or 0),
                "payment_method": row["payment_method"]
            })

            row_status = str(row["status"] or "Active").strip().lower()

            if student_status != "all" or row_status == "active":
                totals["paid"] += float(row["amount"] or 0)

    elif report_type == "total_paid":
        report_title = "Total Fees Paid By Each Student"

        q = """
            SELECT
                students.id,
                students.student_id,
                students.first_name,
                students.last_name,
                students.status,
                classes.class_name,
                academic_years.year_name,
                students.academic_year_id,
                students.class_id
            FROM students
            LEFT JOIN classes
                ON students.class_id = classes.id
            LEFT JOIN academic_years
                ON students.academic_year_id = academic_years.id
            WHERE 1=1
        """
        params = []

        if academic_year_id:
            q += " AND students.academic_year_id = ?"
            params.append(academic_year_id)

        if class_id:
            q += " AND students.class_id = ?"
            params.append(class_id)

        if student_ids:
            placeholders = ",".join(["?"] * len(student_ids))
            q += f" AND students.id IN ({placeholders})"
            params.extend(student_ids)

        if student_status == "active":
            q += " AND LOWER(COALESCE(students.status, 'Active')) = 'active'"
        elif student_status == "inactive":
            q += " AND LOWER(COALESCE(students.status, 'Active')) <> 'active'"

        if search:
            q += """
                AND (
                    students.student_id LIKE ?
                    OR students.first_name LIKE ?
                    OR students.last_name LIKE ?
                )
            """
            value = f"%{search}%"
            params.extend([value, value, value])

        q += " ORDER BY students.first_name, students.last_name"

        student_rows = conn.execute(q, params).fetchall()

        for row in student_rows:

            fee_query = """
                SELECT COALESCE(SUM(amount), 0) AS total_required
                FROM student_fee_items
                WHERE class_id = ?
                  AND academic_year_id = ?
            """
            fee_params = [
                row["class_id"],
                row["academic_year_id"]
            ]

            if fee_descriptions:
                placeholders = ",".join(["?"] * len(fee_descriptions))
                fee_query += f"""
                    AND LOWER(TRIM(description)) IN ({placeholders})
                """
                fee_params.extend([x.lower() for x in fee_descriptions])

            required_row = conn.execute(
                fee_query,
                fee_params
            ).fetchone()

            paid_query = """
                SELECT COALESCE(SUM(p.amount), 0) AS total_paid
                FROM student_fee_payments p
                LEFT JOIN student_fee_items f
                    ON p.fee_item_id = f.id
                WHERE p.student_id = ?
                  AND p.academic_year_id = ?
            """
            paid_params = [
                row["id"],
                row["academic_year_id"]
            ]

            if fee_descriptions:
                placeholders = ",".join(["?"] * len(fee_descriptions))
                paid_query += f"""
                    AND LOWER(TRIM(COALESCE(
                        p.description,
                        f.description,
                        ''
                    ))) IN ({placeholders})
                """
                paid_params.extend([x.lower() for x in fee_descriptions])

            paid_row = conn.execute(
                paid_query,
                paid_params
            ).fetchone()

            required = float(required_row["total_required"] or 0)
            paid = float(paid_row["total_paid"] or 0)
            balance = max(required - paid, 0)

            status = row["status"] or "Active"

            records.append({
                "student_id": row["student_id"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "class_name": row["class_name"],
                "year_name": row["year_name"],
                "status": status,
                "required_amount": required,
                "paid_amount": paid,
                "balance": balance
            })

            include_totals = (
                student_status != "all"
                or str(status).strip().lower() == "active"
            )

            if include_totals:
                totals["required"] += required
                totals["paid"] += paid
                totals["balance"] += balance

    elif report_type in {
        "id_card",
        "first_installment",
        "track",
        "last_installment",
        "fee_item"
    }:
        names = {
            "id_card": "ID Card",
            "first_installment": "First Installment",
            "track": "Track",
            "last_installment": "Last Installment"
        }

        selected_descriptions = list(fee_descriptions)

        if report_type == "fee_item":
            if not selected_descriptions and fee_item_id:
                fee_row = conn.execute(
                    "SELECT description FROM student_fee_items WHERE id = ? LIMIT 1",
                    (fee_item_id,)
                ).fetchone()

                if fee_row:
                    selected_descriptions = [fee_row["description"]]

            report_title = (
                "Payment Report By Selected Fee Items"
                if selected_descriptions
                else "Payment Report By Fee Item"
            )

        else:
            if not selected_descriptions:
                selected_descriptions = [names[report_type]]

            report_title = f"{names[report_type]} Payment Report"

        q = """
            SELECT
                students.id,
                students.student_id,
                students.first_name,
                students.last_name,
                students.status,
                students.class_id,
                students.academic_year_id,
                classes.class_name,
                academic_years.year_name
            FROM students
            LEFT JOIN classes
                ON students.class_id = classes.id
            LEFT JOIN academic_years
                ON students.academic_year_id = academic_years.id
            WHERE 1=1
        """
        params = []

        if academic_year_id:
            q += " AND students.academic_year_id = ?"
            params.append(academic_year_id)

        if class_id:
            q += " AND students.class_id = ?"
            params.append(class_id)

        if student_ids:
            placeholders = ",".join(["?"] * len(student_ids))
            q += f" AND students.id IN ({placeholders})"
            params.extend(student_ids)

        if student_status == "active":
            q += " AND LOWER(COALESCE(students.status, 'Active')) = 'active'"
        elif student_status == "inactive":
            q += " AND LOWER(COALESCE(students.status, 'Active')) <> 'active'"

        if search:
            q += """
                AND (
                    students.student_id LIKE ?
                    OR students.first_name LIKE ?
                    OR students.last_name LIKE ?
                )
            """
            value = f"%{search}%"
            params.extend([value, value, value])

        q += " ORDER BY students.first_name, students.last_name"

        student_rows = conn.execute(q, params).fetchall()

        for row in student_rows:

            fee_query = """
                SELECT COALESCE(SUM(amount), 0) AS required_amount
                FROM student_fee_items
                WHERE class_id = ?
                  AND academic_year_id = ?
            """
            fee_params = [
                row["class_id"],
                row["academic_year_id"]
            ]

            if selected_descriptions:
                placeholders = ",".join(["?"] * len(selected_descriptions))
                fee_query += f"""
                    AND LOWER(TRIM(description)) IN ({placeholders})
                """
                fee_params.extend(
                    [x.lower() for x in selected_descriptions]
                )

            required_row = conn.execute(
                fee_query,
                fee_params
            ).fetchone()

            paid_query = """
                SELECT COALESCE(SUM(p.amount), 0) AS paid_amount
                FROM student_fee_payments p
                LEFT JOIN student_fee_items f
                    ON p.fee_item_id = f.id
                WHERE p.student_id = ?
                  AND p.academic_year_id = ?
            """
            paid_params = [
                row["id"],
                row["academic_year_id"]
            ]

            if selected_descriptions:
                placeholders = ",".join(["?"] * len(selected_descriptions))
                paid_query += f"""
                    AND LOWER(TRIM(COALESCE(
                        f.description,
                        p.description,
                        ''
                    ))) IN ({placeholders})
                """
                paid_params.extend(
                    [x.lower() for x in selected_descriptions]
                )

            paid_row = conn.execute(
                paid_query,
                paid_params
            ).fetchone()

            required = float(required_row["required_amount"] or 0)
            paid = float(paid_row["paid_amount"] or 0)
            balance = max(required - paid, 0)

            if required == 0 and paid == 0:
                continue

            status = row["status"] or "Active"

            records.append({
                "student_id": row["student_id"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "class_name": row["class_name"],
                "year_name": row["year_name"],
                "status": status,
                "description": ", ".join(selected_descriptions),
                "required_amount": required,
                "paid_amount": paid,
                "balance": balance
            })

            include_totals = (
                student_status != "all"
                or str(status).strip().lower() == "active"
            )

            if include_totals:
                totals["required"] += required
                totals["paid"] += paid
                totals["balance"] += balance

    elif report_type == "balances":
        report_title = "Student Fee Balances"

        q = """
            SELECT
                students.id AS student_id,
                students.student_id AS student_number,
                students.first_name,
                students.last_name,
                students.status,
                students.class_id,
                students.academic_year_id,
                classes.class_name,
                student_fee_items.id AS fee_item_id,
                student_fee_items.description,
                student_fee_items.amount AS required_amount
            FROM students
            JOIN student_fee_items
                ON students.class_id = student_fee_items.class_id
               AND students.academic_year_id = student_fee_items.academic_year_id
            LEFT JOIN classes
                ON students.class_id = classes.id
            WHERE 1=1
        """
        params = []

        if academic_year_id:
            q += " AND students.academic_year_id = ?"
            params.append(academic_year_id)

        if class_id:
            q += " AND students.class_id = ?"
            params.append(class_id)

        if student_ids:
            placeholders = ",".join(["?"] * len(student_ids))
            q += f" AND students.id IN ({placeholders})"
            params.extend(student_ids)

        if student_status == "active":
            q += " AND LOWER(COALESCE(students.status, 'Active')) = 'active'"
        elif student_status == "inactive":
            q += " AND LOWER(COALESCE(students.status, 'Active')) <> 'active'"

        if fee_descriptions:
            placeholders = ",".join(["?"] * len(fee_descriptions))
            q += f"""
                AND LOWER(TRIM(student_fee_items.description))
                    IN ({placeholders})
            """
            params.extend([x.lower() for x in fee_descriptions])

        elif fee_item_id:
            q += """
                AND student_fee_items.description =
                    (SELECT description
                     FROM student_fee_items
                     WHERE id = ?)
            """
            params.append(fee_item_id)

        if search:
            q += """
                AND (
                    students.student_id LIKE ?
                    OR students.first_name LIKE ?
                    OR students.last_name LIKE ?
                    OR student_fee_items.description LIKE ?
                )
            """
            value = f"%{search}%"
            params.extend([value, value, value, value])

        q += """
            ORDER BY
                students.first_name,
                students.last_name,
                student_fee_items.id
        """

        rows = conn.execute(q, params).fetchall()

        for row in rows:
            paid_row = conn.execute("""
                SELECT COALESCE(SUM(amount), 0) AS total_paid
                FROM student_fee_payments
                WHERE student_id = ?
                  AND fee_item_id = ?
                  AND academic_year_id = ?
            """, (
                row["student_id"],
                row["fee_item_id"],
                row["academic_year_id"]
            )).fetchone()

            required = float(row["required_amount"] or 0)
            paid = float(paid_row["total_paid"] or 0)
            balance = max(required - paid, 0)
            status = row["status"] or "Active"

            records.append({
                "student_number": row["student_number"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "class_name": row["class_name"],
                "status": status,
                "description": row["description"],
                "required_amount": required,
                "paid_amount": paid,
                "balance": balance
            })

            include_totals = (
                student_status != "all"
                or str(status).strip().lower() == "active"
            )

            if include_totals:
                totals["required"] += required
                totals["paid"] += paid
                totals["balance"] += balance

    # --------------------------------------------------------
    # TEACHER SALARY
    # --------------------------------------------------------
    elif report_type == "teacher_salary":

        report_title = "Teacher Salary Report"

        q = """
            SELECT
                teachers.id,
                teachers.teacher_id,
                teachers.first_name,
                teachers.last_name,
                teachers.salary_rate,
                teacher_attendance.status
            FROM teachers
            LEFT JOIN teacher_attendance
                ON teachers.id = teacher_attendance.teacher_id
            WHERE 1=1
        """
        params = []

        if teacher_id:
            q += " AND teachers.id = ?"
            params.append(teacher_id)

        rows = conn.execute(q, params).fetchall()
        grouped = {}

        for row in rows:
            key = row["id"]

            grouped.setdefault(key, {
                "teacher_id": row["teacher_id"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "salary_rate": float(row["salary_rate"] or 0),
                "days_present": 0,
                "leave_days": 0,
                "days_absent": 0
            })

            if row["status"] == "Present":
                grouped[key]["days_present"] += 1
            elif row["status"] == "Leave":
                grouped[key]["leave_days"] += 1
            elif row["status"] == "Absent":
                grouped[key]["days_absent"] += 1

        for row in grouped.values():
            amount_due = min(
                (row["days_present"] or 0) + (row["leave_days"] or 0),
                26
            ) * row["salary_rate"]

            row["salary_month"] = ""
            row["amount_due"] = amount_due
            row["amount_paid"] = 0
            records.append(row)
            totals["required"] += amount_due

    # --------------------------------------------------------
    # ACADEMIC YEARS
    # --------------------------------------------------------
    elif report_type == "academic_years":

        report_title = "Academic Years Report"
        records = conn.execute("""
            SELECT id, year_name, is_active
            FROM academic_years
            ORDER BY id DESC
        """).fetchall()

    totals["records"] = len(records)

    conn.close()

    return render_template(
        "reports.html",
        report_type=report_type,
        report_title=report_title,
        academic_years=academic_years,
        classes=classes,
        teachers=teachers,
        students=students,
        fee_items=fee_items,
        academic_year_id=academic_year_id,
        class_id=class_id,
        teacher_id=teacher_id,
        student_id=student_id,
        student_ids=student_ids,
        fee_item_id=fee_item_id,
        fee_item_ids=fee_item_ids,
        student_status=student_status,
        fee_descriptions=fee_descriptions,
        search=search,
        records=records,
        totals=totals,
        school_settings=school_settings
    )


@app.route("/reports/export")
def export_report():
    if not login_required():
        return redirect("/")

    report_type = request.args.get("report_type", "students")
    academic_year_id = request.args.get("academic_year_id", "")
    class_id = request.args.get("class_id", "")
    teacher_id = request.args.get("teacher_id", "")
    student_id = request.args.get("student_id", "")
    student_ids = request.args.getlist("student_ids")
    fee_item_id = request.args.get("fee_item_id", "")
    fee_item_ids = request.args.getlist("fee_item_ids")
    student_status = request.args.get("student_status", "all")
    search = request.args.get("search", "").strip()
    student_ids = [x.strip() for x in student_ids if x and x.strip()]
    if not student_ids and student_id:
        student_ids = [student_id]
    fee_descriptions = request.args.getlist("fee_descriptions")
    fee_descriptions = [x.strip() for x in fee_descriptions if x and x.strip() and x.strip() != "__ALL__"]

    conn = get_connection()
    output = StringIO()
    writer = csv.writer(output)

    if report_type == "students":
        writer.writerow([
            "Student ID", "First Name", "Last Name", "Class",
            "Academic Year", "Gender", "Status"
        ])
        q = """
            SELECT students.student_id, students.first_name,
                   students.last_name, classes.class_name,
                   academic_years.year_name, students.gender,
                   students.status
            FROM students
            LEFT JOIN classes ON students.class_id = classes.id
            LEFT JOIN academic_years ON students.academic_year_id = academic_years.id
            WHERE 1=1
        """
        params = []
        if academic_year_id:
            q += " AND students.academic_year_id = ?"; params.append(academic_year_id)
        if class_id:
            q += " AND students.class_id = ?"; params.append(class_id)

        if student_ids:
            placeholders = ",".join(["?"] * len(student_ids))
            q += f" AND students.id IN ({placeholders})"
            params.extend(student_ids)

        if student_status == "active":
            q += " AND LOWER(COALESCE(students.status, 'Active')) = 'active'"
        elif student_status == "inactive":
            q += " AND LOWER(COALESCE(students.status, 'Active')) <> 'active'"

        if student_id and not student_ids:
            q += " AND students.id = ?"; params.append(student_id)

        if search:
            q += " AND (students.student_id LIKE ? OR students.first_name LIKE ? OR students.last_name LIKE ?)"
            v = f"%{search}%"; params.extend([v, v, v])
        q += " ORDER BY students.id DESC"
        rows = conn.execute(q, params).fetchall()
        for r in rows:
            writer.writerow([
                r["student_id"], r["first_name"], r["last_name"],
                r["class_name"], r["year_name"], r["gender"], r["status"]
            ])

    elif report_type == "teachers":
        writer.writerow([
            "Teacher ID", "First Name", "Last Name", "Subject",
            "Position", "Phone", "Status"
        ])
        rows = conn.execute("""
            SELECT teacher_id, first_name, last_name, subject,
                   position, phone, status
            FROM teachers
            ORDER BY first_name, last_name
        """).fetchall()
        for r in rows:
            writer.writerow([
                r["teacher_id"], r["first_name"], r["last_name"],
                r["subject"], r["position"], r["phone"], r["status"]
            ])

    elif report_type in {
        "total_paid",
        "id_card",
        "first_installment",
        "track",
        "last_installment",
        "fee_item",
        "balances",
        "student_payments"
    }:

        if report_type == "student_payments":

            writer.writerow([
                "Student ID",
                "Student Name",
                "Class",
                "Status",
                "Academic Year",
                "Fee Description",
                "Amount Paid",
                "Payment Date",
                "Payment Method"
            ])

            q = """
                SELECT
                    students.student_id,
                    students.first_name || ' ' || students.last_name AS student_name,
                    students.status,
                    classes.class_name,
                    academic_years.year_name,
                    COALESCE(
                        student_fee_payments.description,
                        student_fee_items.description,
                        ''
                    ) AS description,
                    student_fee_payments.amount AS paid_amount,
                    student_fee_payments.payment_date,
                    student_fee_payments.payment_method
                FROM student_fee_payments
                JOIN students
                    ON student_fee_payments.student_id = students.id
                LEFT JOIN classes
                    ON students.class_id = classes.id
                LEFT JOIN academic_years
                    ON students.academic_year_id = academic_years.id
                LEFT JOIN student_fee_items
                    ON student_fee_payments.fee_item_id = student_fee_items.id
                WHERE 1=1
            """

            params = []

            if academic_year_id:
                q += " AND student_fee_payments.academic_year_id = ?"
                params.append(academic_year_id)

            if class_id:
                q += " AND students.class_id = ?"
                params.append(class_id)

            if student_ids:
                placeholders = ",".join(["?"] * len(student_ids))
                q += f" AND students.id IN ({placeholders})"
                params.extend(student_ids)

            if student_status == "active":
                q += " AND LOWER(COALESCE(students.status, 'Active')) = 'active'"
            elif student_status == "inactive":
                q += " AND LOWER(COALESCE(students.status, 'Active')) <> 'active'"

            if fee_descriptions:
                placeholders = ",".join(["?"] * len(fee_descriptions))
                q += f"""
                    AND LOWER(TRIM(COALESCE(
                        student_fee_payments.description,
                        student_fee_items.description,
                        ''
                    ))) IN ({placeholders})
                """
                params.extend([x.lower() for x in fee_descriptions])

            if search:
                q += """
                    AND (
                        students.student_id LIKE ?
                        OR students.first_name LIKE ?
                        OR students.last_name LIKE ?
                        OR student_fee_payments.description LIKE ?
                    )
                """
                v = f"%{search}%"
                params.extend([v, v, v, v])

            q += """
                ORDER BY
                    student_fee_payments.payment_date DESC,
                    student_fee_payments.id DESC
            """

            rows = conn.execute(q, params).fetchall()

            for r in rows:
                writer.writerow([
                    r["student_id"],
                    r["student_name"],
                    r["class_name"],
                    r["status"] or "Active",
                    r["year_name"],
                    r["description"],
                    r["paid_amount"],
                    r["payment_date"],
                    r["payment_method"]
                ])

        else:

            writer.writerow([
                "Student ID",
                "Student Name",
                "Class",
                "Status",
                "Academic Year",
                "Fee Description",
                "Required",
                "Total Paid",
                "Balance"
            ])

            q = """
                SELECT
                    students.id,
                    students.student_id,
                    students.first_name,
                    students.last_name,
                    students.status,
                    students.class_id,
                    students.academic_year_id,
                    classes.class_name,
                    academic_years.year_name
                FROM students
                LEFT JOIN classes
                    ON students.class_id = classes.id
                LEFT JOIN academic_years
                    ON students.academic_year_id = academic_years.id
                WHERE 1=1
            """

            params = []

            if academic_year_id:
                q += " AND students.academic_year_id = ?"
                params.append(academic_year_id)

            if class_id:
                q += " AND students.class_id = ?"
                params.append(class_id)

            if student_ids:
                placeholders = ",".join(["?"] * len(student_ids))
                q += f" AND students.id IN ({placeholders})"
                params.extend(student_ids)

            if student_status == "active":
                q += " AND LOWER(COALESCE(students.status, 'Active')) = 'active'"
            elif student_status == "inactive":
                q += " AND LOWER(COALESCE(students.status, 'Active')) <> 'active'"

            if search:
                q += """
                    AND (
                        students.student_id LIKE ?
                        OR students.first_name LIKE ?
                        OR students.last_name LIKE ?
                    )
                """
                v = f"%{search}%"
                params.extend([v, v, v])

            q += " ORDER BY students.first_name, students.last_name"

            students_rows = conn.execute(q, params).fetchall()

            patterns = {
                "id_card": "ID Card",
                "first_installment": "First Installment",
                "track": "Track",
                "last_installment": "Last Installment"
            }

            for r in students_rows:

                selected_descriptions = list(fee_descriptions)

                if report_type == "fee_item" and not selected_descriptions:
                    if fee_item_id:
                        fee = conn.execute(
                            "SELECT description FROM student_fee_items WHERE id = ? LIMIT 1",
                            (fee_item_id,)
                        ).fetchone()

                        if fee:
                            selected_descriptions = [fee["description"]]

                elif report_type != "total_paid" and not selected_descriptions:
                    selected_descriptions = [patterns.get(report_type, "")]

                if report_type == "total_paid":
                    required_query = """
                        SELECT COALESCE(SUM(amount), 0) AS total
                        FROM student_fee_items
                        WHERE class_id = ?
                          AND academic_year_id = ?
                    """
                    required_params = [
                        r["class_id"],
                        r["academic_year_id"]
                    ]

                    if fee_descriptions:
                        placeholders = ",".join(["?"] * len(fee_descriptions))
                        required_query += f"""
                            AND LOWER(TRIM(description))
                                IN ({placeholders})
                        """
                        required_params.extend(
                            [x.lower() for x in fee_descriptions]
                        )

                    required = conn.execute(
                        required_query,
                        required_params
                    ).fetchone()["total"]

                    paid_query = """
                        SELECT COALESCE(SUM(p.amount), 0) AS total
                        FROM student_fee_payments p
                        LEFT JOIN student_fee_items f
                            ON p.fee_item_id = f.id
                        WHERE p.student_id = ?
                          AND p.academic_year_id = ?
                    """
                    paid_params = [
                        r["id"],
                        r["academic_year_id"]
                    ]

                    if fee_descriptions:
                        placeholders = ",".join(["?"] * len(fee_descriptions))
                        paid_query += f"""
                            AND LOWER(TRIM(COALESCE(
                                p.description,
                                f.description,
                                ''
                            ))) IN ({placeholders})
                        """
                        paid_params.extend(
                            [x.lower() for x in fee_descriptions]
                        )

                    paid = conn.execute(
                        paid_query,
                        paid_params
                    ).fetchone()["total"]

                    description = (
                        ", ".join(fee_descriptions)
                        if fee_descriptions
                        else "All Fees"
                    )

                else:

                    if not selected_descriptions:
                        continue

                    placeholders = ",".join(
                        ["?"] * len(selected_descriptions)
                    )

                    required_query = f"""
                        SELECT COALESCE(SUM(amount), 0) AS total
                        FROM student_fee_items
                        WHERE class_id = ?
                          AND academic_year_id = ?
                          AND LOWER(TRIM(description))
                              IN ({placeholders})
                    """

                    required_params = [
                        r["class_id"],
                        r["academic_year_id"]
                    ]
                    required_params.extend(
                        [x.lower() for x in selected_descriptions]
                    )

                    required = conn.execute(
                        required_query,
                        required_params
                    ).fetchone()["total"]

                    paid_query = f"""
                        SELECT COALESCE(SUM(p.amount), 0) AS total
                        FROM student_fee_payments p
                        LEFT JOIN student_fee_items f
                            ON p.fee_item_id = f.id
                        WHERE p.student_id = ?
                          AND p.academic_year_id = ?
                          AND LOWER(TRIM(COALESCE(
                              f.description,
                              p.description,
                              ''
                          ))) IN ({placeholders})
                    """

                    paid_params = [
                        r["id"],
                        r["academic_year_id"]
                    ]
                    paid_params.extend(
                        [x.lower() for x in selected_descriptions]
                    )

                    paid = conn.execute(
                        paid_query,
                        paid_params
                    ).fetchone()["total"]

                    description = ", ".join(selected_descriptions)

                required = float(required or 0)
                paid = float(paid or 0)
                balance = max(required - paid, 0)

                if required == 0 and paid == 0:
                    continue

                writer.writerow([
                    r["student_id"],
                    f'{r["first_name"]} {r["last_name"]}',
                    r["class_name"],
                    r["status"] or "Active",
                    r["year_name"],
                    description,
                    required,
                    paid,
                    balance
                ])

    else:
        writer.writerow(["Report Type", report_type])

    conn.close()

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=school_report.csv"
    response.headers["Content-Type"] = "text/csv"
    return response


# ============================================================
# LOGOUT
# ============================================================


# ============================================================
# NOTIFICATION SETTINGS
# ============================================================

@app.route("/notification-settings", methods=["GET", "POST"])
def notification_settings():

    if "user_id" not in session:
        return redirect("/")

    from modules.notifications import (
        ensure_notification_tables,
        NOTIFICATION_TYPES
    )

    connection = get_connection()

    try:
        ensure_notification_tables(connection)

        school_id = session.get("school_id")
        current_user_id = session.get("user_id")

        users = connection.execute("""
            SELECT
                id,
                username,
                role
            FROM users
            ORDER BY username
        """).fetchall()

        if request.method == "POST":

            for user in users:

                for notification_type in NOTIFICATION_TYPES:

                    field_name = (
                        f"notification_{notification_type}_{user['id']}"
                    )

                    enabled = (
                        1
                        if request.form.get(field_name) == "1"
                        else 0
                    )

                    connection.execute("""
                        INSERT INTO notification_settings
                        (
                            school_id,
                            user_id,
                            notification_type,
                            enabled
                        )
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(
                            school_id,
                            user_id,
                            notification_type
                        )
                        DO UPDATE SET
                            enabled = excluded.enabled
                    """, (
                        school_id,
                        user["id"],
                        notification_type,
                        enabled
                    ))

            connection.commit()

            flash(
                "Notification settings saved successfully.",
                "success"
            )

            return redirect("/notification-settings")

        saved_settings = connection.execute("""
            SELECT
                user_id,
                notification_type,
                enabled
            FROM notification_settings
            WHERE school_id = ?
        """, (
            school_id,
        )).fetchall()

        settings = {}

        for row in saved_settings:
            settings[
                (row["notification_type"], row["user_id"])
            ] = row["enabled"]

        return render_template(
            "notification_settings.html",
            users=users,
            notification_types=NOTIFICATION_TYPES,
            settings=settings
        )

    finally:
        connection.close()

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/")
# ============================================================
# CURRENT SCHOOL USER ACCOUNTS
# ============================================================

CURRENT_USER_ROLES = {
    "Administrator": "Administrator",
    "Registrar": "Registrar",
    "VPI": "Vice Principal",
    "Teacher": "Teacher",
    "Business": "Business",
    "Student": "Student"
}



# SCHOOL_PERMISSION_GUARD_REPAIR_V1
SCHOOL_PERMISSION_ITEMS = [('dashboard', 'Dashboard'), ('students', 'Students'), ('student_attendance', 'Student Attendance'), ('student_fees', 'Student Fees and Payments'), ('results', 'Student Results'), ('teachers', 'Teachers'), ('teacher_attendance', 'Teacher Attendance'), ('salaries', 'Teacher Salaries'), ('salary_records_report', 'Teacher Salary Records Report'), ('academic_years', 'Academic Years'), ('classes', 'Classes'), ('subjects', 'Subjects'), ('reports', 'School Reports'), ('financial_reports', 'Financial Reports'), ('yearly_history', 'Yearly History'), ('students_by_class', 'Students by Class'), ('expenditures', 'Expenditures'), ('documents', 'Student Documents'), ('notifications', 'Notifications'), ('settings', 'School Settings'), ('school_users', 'User Accounts (Administrator only)')]

def _school_is_admin():
    role = str(session.get("role", "")).strip().lower()
    return role in {"administrator", "head administrator", "admin"}

def _school_permission_for_path(path):
    if path == "/school-users" or path.startswith("/school-users/"):
        return "school_users"

    routes = [
        ("/student-attendance", "student_attendance"),
        ("/student-fees", "student_fees"),
        ("/salary-records-report", "salary_records_report"),
        ("/financial-reports", "financial_reports"),
        ("/yearly-history", "yearly_history"),
        ("/students-by-class", "students_by_class"),
        ("/notification-settings", "notifications"),
        ("/notifications", "notifications"),
        ("/academic-years", "academic_years"),
        ("/expenditures", "expenditures"),
        ("/documents", "documents"),
        ("/students", "students"),
        ("/results", "results"),
        ("/teachers", "teachers"),
        ("/attendance", "teacher_attendance"),
        ("/salaries", "salaries"),
        ("/classes", "classes"),
        ("/subjects", "subjects"),
        ("/reports", "reports"),
        ("/settings", "settings"),
    ]
    for prefix, key in routes:
        if path == prefix or path.startswith(prefix + "/"):
            return key
    return None

def _school_allowed_permissions():
    if not session.get("user_id"):
        return set()
    if _school_is_admin():
        return {key for key, label in SCHOOL_PERMISSION_ITEMS}

    connection = None
    try:
        connection = get_connection()
        connection.execute("""
            CREATE TABLE IF NOT EXISTS user_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                permission TEXT NOT NULL,
                allowed INTEGER NOT NULL DEFAULT 1,
                UNIQUE(user_id, permission)
            )
        """)
        rows = connection.execute("""
            SELECT permission FROM user_permissions
            WHERE user_id = ? AND allowed = 1
        """, (session["user_id"],)).fetchall()
        return {row["permission"] for row in rows}
    except Exception:
        return set()
    finally:
        if connection is not None:
            connection.close()

@app.before_request
def _school_permission_guard():
    path = request.path

    # Account administration is reserved for an Administrator.
    if path == "/school-users" or path.startswith("/school-users/"):
        if session.get("username") and not _school_is_admin():
            return "Access denied: Administrator privileges are required.", 403
        return None

    permission = _school_permission_for_path(path)
    if permission is None or not session.get("username"):
        return None
    if _school_is_admin():
        return None
    if permission not in _school_allowed_permissions():
        return "Access denied: your account has not been granted this permission.", 403
    return None

@app.context_processor
def _school_permission_template_context():
    admin = _school_is_admin()
    return {
        "is_school_admin": admin,
        "allowed_permission_keys": sorted(_school_allowed_permissions()),
    }
# END SCHOOL_PERMISSION_GUARD_REPAIR_V1


@app.route("/school-users", methods=["GET", "POST"])
def school_users():
    if not session.get("username"):
        return redirect("/")

    connection = get_connection()
    cursor = connection.cursor()
    message = None
    error = None

    try:
        columns = [row["name"] for row in cursor.execute(
            "PRAGMA table_info(users)"
        ).fetchall()]
        if "is_active" not in columns:
            cursor.execute(
                "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                permission TEXT NOT NULL,
                allowed INTEGER NOT NULL DEFAULT 1,
                UNIQUE(user_id, permission),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        connection.commit()

        if request.method == "POST" and request.args.get("user_id"):
            try:
                target_id = int(request.args.get("user_id"))
            except (TypeError, ValueError):
                target_id = -1

            action = request.form.get("account_action", "").strip().lower()
            current_id = session.get("user_id", session.get("platform_user_id"))

            if target_id < 1:
                error = "Invalid user account."
            elif action not in ("activate", "suspend"):
                error = "Invalid account action."
            elif action == "suspend" and str(current_id) == str(target_id):
                error = "You cannot suspend the account you are currently using."
            else:
                cursor.execute(
                    "UPDATE users SET is_active = ? WHERE id = ?",
                    (1 if action == "activate" else 0, target_id)
                )
                if cursor.rowcount:
                    connection.commit()
                    message = (
                        "User account activated successfully."
                        if action == "activate"
                        else "User account suspended successfully."
                    )
                else:
                    error = "User account was not found."

        elif request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "Administrator").strip()

            if not username:
                error = "Username is required."
            elif not password:
                error = "Password is required."
            elif len(password) < 6:
                error = "Password must be at least 6 characters."
            elif role not in CURRENT_USER_ROLES:
                error = "Invalid user role."
            else:
                existing = cursor.execute(
                    "SELECT id FROM users WHERE LOWER(username) = LOWER(?)",
                    (username,)
                ).fetchone()
                if existing:
                    error = "That username already exists."
                else:
                    try:
                        password_hash = generate_password_hash(password)
                        cursor.execute("""
                            INSERT INTO users (username, password, role, is_active)
                            VALUES (?, ?, ?, 1)
                        """, (username, password_hash, role))
                        connection.commit()
                        message = "User account created successfully."
                    except Exception as exc:
                        connection.rollback()
                        error = f"Unable to create user account: {exc}"

        users = cursor.execute("""
            SELECT id, username, role, created_at, COALESCE(is_active, 1) AS is_active
            FROM users
            ORDER BY id
        """).fetchall()

    except Exception as exc:
        connection.rollback()
        users = []
        error = f"Unable to load user accounts: {exc}"
    finally:
        connection.close()

    return render_template(
        "school_users.html",
        users=users,
        roles=CURRENT_USER_ROLES,
        message=message,
        error=error
    )


@app.route("/school-users/permissions/<int:user_id>", methods=["GET", "POST"])
def school_user_permissions(user_id):
    if not session.get("username"):
        return redirect("/")

    permission_items = SCHOOL_PERMISSION_ITEMS

    connection = get_connection()
    cursor = connection.cursor()
    try:
        columns = [row["name"] for row in cursor.execute(
            "PRAGMA table_info(users)"
        ).fetchall()]
        if "is_active" not in columns:
            cursor.execute(
                "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                permission TEXT NOT NULL,
                allowed INTEGER NOT NULL DEFAULT 1,
                UNIQUE(user_id, permission),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        user = cursor.execute("""
            SELECT id, username, role, created_at, COALESCE(is_active, 1) AS is_active
            FROM users WHERE id = ?
        """, (user_id,)).fetchone()

        if not user:
            return redirect("/school-users")

        if request.method == "POST":
            selected = set(request.form.getlist("permissions"))
            valid_keys = {key for key, label in permission_items}
            selected &= valid_keys
            cursor.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
            cursor.executemany("""
                INSERT INTO user_permissions (user_id, permission, allowed)
                VALUES (?, ?, 1)
            """, [(user_id, key) for key in selected])
            connection.commit()
            return redirect("/school-users")

        saved = cursor.execute("""
            SELECT permission FROM user_permissions
            WHERE user_id = ? AND allowed = 1
        """, (user_id,)).fetchall()
        selected = {row["permission"] for row in saved}
        return render_template(
            "school_user_permissions.html",
            user=user,
            permission_items=permission_items,
            selected_permissions=selected
        )
    finally:
        connection.close()


@app.route("/school-users/edit/<int:user_id>", methods=["GET", "POST"])
def edit_school_user(user_id):

    if not session.get("username"):
        return redirect("/")

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id, username, role, created_at
        FROM users
        WHERE id = ?
        """,
        (user_id,)
    )

    user = cursor.fetchone()

    if not user:

        connection.close()

        return redirect("/school-users")

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        role = request.form.get("role", "Administrator").strip()
        password = request.form.get("password", "")

        error = None

        if not username:

            error = "Username is required."

        elif role not in CURRENT_USER_ROLES:

            error = "Invalid user role."

        else:

            cursor.execute(
                """
                SELECT id
                FROM users
                WHERE LOWER(username) = LOWER(?)
                AND id != ?
                """,
                (username, user_id)
            )

            duplicate = cursor.fetchone()

            if duplicate:

                error = "That username is already being used by another account."

        if error:

            connection.close()

            return render_template(
                "edit_school_user.html",
                user=user,
                roles=CURRENT_USER_ROLES,
                error=error
            )

        try:

            if password:

                if len(password) < 6:

                    connection.close()

                    return render_template(
                        "edit_school_user.html",
                        user=user,
                        roles=CURRENT_USER_ROLES,
                        error="Password must be at least 6 characters."
                    )

                password_hash = generate_password_hash(password)

                cursor.execute(
                    """
                    UPDATE users
                    SET username = ?,
                        role = ?,
                        password = ?
                    WHERE id = ?
                    """,
                    (
                        username,
                        role,
                        password_hash,
                        user_id
                    )
                )

            else:

                cursor.execute(
                    """
                    UPDATE users
                    SET username = ?,
                        role = ?
                    WHERE id = ?
                    """,
                    (
                        username,
                        role,
                        user_id
                    )
                )

            connection.commit()

            connection.close()

            return redirect("/school-users")

        except Exception as e:

            connection.rollback()

            connection.close()

            return render_template(
                "edit_school_user.html",
                user=user,
                roles=CURRENT_USER_ROLES,
                error=f"Unable to update user account: {e}"
            )

    connection.close()

    return render_template(
        "edit_school_user.html",
        user=user,
        roles=CURRENT_USER_ROLES
    )


@app.route("/school-users/reset-password/<int:user_id>", methods=["GET", "POST"])
def reset_user_password(user_id):

    if not session.get("username"):
        return redirect("/")

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id, username, role, created_at
        FROM users
        WHERE id = ?
        """,
        (user_id,)
    )

    user = cursor.fetchone()

    if not user:

        connection.close()

        return redirect("/school-users")

    if request.method == "POST":

        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(password) < 6:

            connection.close()

            return render_template(
                "reset_user_password.html",
                user=user,
                error="Password must be at least 6 characters."
            )

        if password != confirm_password:

            connection.close()

            return render_template(
                "reset_user_password.html",
                user=user,
                error="The passwords do not match."
            )

        try:

            password_hash = generate_password_hash(password)

            cursor.execute(
                """
                UPDATE users
                SET password = ?
                WHERE id = ?
                """,
                (
                    password_hash,
                    user_id
                )
            )

            connection.commit()

            connection.close()

            return redirect("/school-users")

        except Exception as e:

            connection.rollback()

            connection.close()

            return render_template(
                "reset_user_password.html",
                user=user,
                error=f"Unable to reset password: {e}"
            )

    connection.close()

    return render_template(
        "reset_user_password.html",
        user=user
    )


@app.route("/school-users/delete/<int:user_id>", methods=["POST"])
def delete_school_user(user_id):

    if not session.get("username"):
        return redirect("/")

    connection = get_connection()
    cursor = connection.cursor()

    try:

        cursor.execute(
            """
            SELECT id, username, role
            FROM users
            WHERE id = ?
            """,
            (user_id,)
        )

        user = cursor.fetchone()

        if not user:

            connection.close()

            return redirect("/school-users")

        # Protect the currently logged-in account.
        if user["username"] == session.get("username"):

            connection.close()

            flash("You cannot delete the account you are currently using.")

            return redirect("/school-users")

        # Protect the only Administrator account.
        if user["role"] == "Administrator":

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM users
                WHERE role = 'Administrator'
                """
            )

            administrator_count = cursor.fetchone()[0]

            if administrator_count <= 1:

                connection.close()

                flash(
                    "The only Administrator account cannot be deleted."
                )

                return redirect("/school-users")

        cursor.execute(
            """
            DELETE FROM users
            WHERE id = ?
            """,
            (user_id,)
        )

        connection.commit()

        connection.close()

        flash("User account deleted successfully.")

        return redirect("/school-users")

    except Exception as e:

        connection.rollback()

        connection.close()

        flash(f"Unable to delete user account: {e}")

        return redirect("/school-users")


# ============================================================
# SCHOOL LOGIN ALIAS
# ============================================================

@app.route("/school-login", methods=["GET", "POST"])
def school_login():
    return login()


# ============================================================
# PLATFORM CREATOR ROUTES
# ============================================================

@app.route("/creator-login", methods=["GET", "POST"])
def creator_login():

    from modules.multischool import authenticate_creator

    if request.method == "POST":

        pin = request.form.get("pin", "")

        if authenticate_creator(pin):

            session.clear()

            session["creator_authenticated"] = True

            return redirect("/creator")

        flash("Incorrect creator PIN.", "danger")

    return render_template("creator_login.html")


@app.route("/creator")
def creator_dashboard():

    from modules.multischool import get_creator_schools

    if not session.get("creator_authenticated"):
        return redirect("/creator-login")

    schools = get_creator_schools()

    return render_template(
        "creator_dashboard.html",
        schools=schools
    )


# ============================================================
# CREATE SCHOOL ACCOUNT
# ============================================================

@app.route("/creator/schools/create", methods=["GET", "POST"])
def creator_create_school():

    from modules.multischool import create_school_account

    if not session.get("creator_authenticated"):
        return redirect("/creator-login")

    if request.method == "POST":

        school_name = request.form.get("school_name", "").strip()
        location = request.form.get("location", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        admin_username = request.form.get("admin_username", "").strip()
        admin_password = request.form.get("admin_password", "")

        success, result = create_school_account(
            school_name,
            location,
            phone,
            email,
            admin_username,
            admin_password
        )

        if success:
            flash(
                "School account created successfully.",
                "success"
            )
            return redirect("/creator")

        flash(
            "School creation failed: " + str(result),
            "danger"
        )

    return render_template("creator_create_school.html")



# ============================================================
# CREATOR SCHOOL STATUS
# ============================================================

@app.route("/creator/schools/<int:school_id>/deactivate")
def creator_deactivate_school(school_id):

    from modules.multischool import set_school_active

    if not session.get("creator_authenticated"):
        return redirect("/creator-login")

    if set_school_active(school_id, False):
        flash("School deactivated successfully.", "success")
    else:
        flash("Unable to deactivate the school.", "danger")

    return redirect("/creator")


@app.route("/creator/schools/<int:school_id>/activate")
def creator_activate_school(school_id):

    from modules.multischool import set_school_active

    if not session.get("creator_authenticated"):
        return redirect("/creator-login")

    if set_school_active(school_id, True):
        flash("School activated successfully.", "success")
    else:
        flash("Unable to activate the school.", "danger")

    return redirect("/creator")


@app.route("/creator/logout")
def creator_logout():

    session.pop("creator_authenticated", None)

    return redirect("/creator-login")

# ==================================================
# RUN APPLICATION
# ==================================================


@app.route("/financial-balance-lists")
def financial_balance_lists():
    if not login_required():
        return redirect("/")

    conn = get_connection()

    try:
        settings = conn.execute("""
            SELECT * FROM school_settings ORDER BY id DESC LIMIT 1
        """).fetchone()

        active_year = conn.execute("""
            SELECT id, year_name
            FROM academic_years
            WHERE is_active = 1
            ORDER BY id DESC LIMIT 1
        """).fetchone()

        logo_url = (
            "/static/uploads/" + settings["school_logo"]
            if settings and settings["school_logo"] else None
        )

        if not active_year:
            return render_template(
                "financial_balance_lists.html",
                school_settings=settings,
                logo_url=logo_url,
                active_year=None,
                classes=[],
                fee_items=[],
                students=[],
                report_status="owing",
                class_id="",
                fee_item_id="",
                error_message="Please activate an academic year first."
            )

        year_id = active_year["id"]
        class_id = request.args.get("class_id", "").strip()
        fee_item_id = request.args.get("fee_item_id", "").strip()
        report_status = request.args.get("status", "owing").strip().lower()

        if report_status not in ("owing", "paid"):
            report_status = "owing"

        classes = conn.execute("""
            SELECT id, class_name FROM classes ORDER BY class_name
        """).fetchall()

        fee_items = conn.execute("""
            SELECT id, class_id, description, amount
            FROM student_fee_items
            WHERE academic_year_id = ?
              AND (? = '' OR CAST(class_id AS TEXT) = ?)
            ORDER BY description, class_id
        """, (year_id, class_id, class_id)).fetchall()

        student_rows = conn.execute("""
            SELECT s.id, s.student_id, s.first_name, s.last_name,
                   s.class_id, s.status, c.class_name
            FROM students s
            LEFT JOIN classes c ON c.id = s.class_id
            WHERE s.class_id IS NOT NULL
            ORDER BY c.class_name, s.last_name, s.first_name
        """).fetchall()

        results = []

        for student in student_rows:
            sid = student["id"]
            cid = student["class_id"]

            if class_id and str(cid) != class_id:
                continue

            scholarship = conn.execute("""
                SELECT scholarship_type
                FROM student_fee_scholarships
                WHERE student_id = ? AND class_id = ?
                  AND academic_year_id = ?
                ORDER BY id DESC LIMIT 1
            """, (sid, cid, year_id)).fetchone()

            discount_rows = conn.execute("""
                SELECT fee_item_id, discount_percent
                FROM student_fee_scholarship_items
                WHERE student_id = ? AND class_id = ?
                  AND academic_year_id = ?
            """, (sid, cid, year_id)).fetchall()

            discounts = {
                row["fee_item_id"]: max(
                    0.0, min(float(row["discount_percent"] or 0), 100.0)
                )
                for row in discount_rows
            }

            full_scholarship = bool(
                scholarship and
                (scholarship["scholarship_type"] or "").strip().lower()
                in ("full", "full scholarship")
            )

            student_fees = []
            total_due = 0.0
            total_paid = 0.0

            class_fees = conn.execute("""
                SELECT id, description, amount
                FROM student_fee_items
                WHERE class_id = ? AND academic_year_id = ?
                ORDER BY description
            """, (cid, year_id)).fetchall()

            for fee in class_fees:
                if fee_item_id and str(fee["id"]) != fee_item_id:
                    continue

                normal_amount = float(fee["amount"] or 0)

                adjustment = conn.execute("""
                    SELECT adjusted_amount
                    FROM student_fee_adjustments
                    WHERE student_id = ?
                      AND academic_year_id = ?
                      AND fee_type = 'class'
                      AND fee_item_id = ?
                """, (
                    sid, year_id, fee["id"]
                )).fetchone()

                if adjustment:
                    normal_amount = float(
                        adjustment["adjusted_amount"] or 0
                    )

                # Keep the displayed fee amount tied to the saved
                # student-specific adjustment for this exact fee item.

                discount_percent = (
                    100.0 if full_scholarship
                    else discounts.get(fee["id"], 0.0)
                )
                due = max(normal_amount * (1 - discount_percent / 100.0), 0.0)

                paid_row = conn.execute("""
                    SELECT COALESCE(SUM(amount), 0) AS paid
                    FROM student_fee_payments
                    WHERE student_id = ? AND class_id = ?
                      AND academic_year_id = ? AND fee_item_id = ?
                """, (sid, cid, year_id, fee["id"])).fetchone()

                paid = float(paid_row["paid"] or 0)
                outstanding = max(due - paid, 0.0)

                student_fees.append({
                    "description": fee["description"] or "Unnamed fee",
                    "due": due,
                    "paid": paid,
                    "balance": outstanding
                })
                total_due += due
                total_paid += paid

            # Include individual student bills when no class fee item
            # is selected in the filter.
            if not fee_item_id:
                specific_fees = conn.execute("""
                    SELECT id, description, amount
                    FROM student_specific_fees
                    WHERE student_id = ? AND class_id = ?
                      AND academic_year_id = ?
                    ORDER BY description
                """, (sid, cid, year_id)).fetchall()

                for fee in specific_fees:
                    due = float(fee["amount"] or 0)

                    adjustment = conn.execute("""
                        SELECT adjusted_amount
                        FROM student_fee_adjustments
                        WHERE student_id = ?
                          AND academic_year_id = ?
                          AND fee_type = 'specific'
                          AND fee_item_id = ?
                    """, (
                        sid, year_id, fee["id"]
                    )).fetchone()

                    if adjustment:
                        due = float(adjustment["adjusted_amount"] or 0)

                    # Use this adjusted due amount in the report's
                    # fee details and outstanding-balance calculation.

                    paid_row = conn.execute("""
                        SELECT COALESCE(SUM(amount), 0) AS paid
                        FROM student_fee_payments
                        WHERE student_id = ? AND class_id = ?
                          AND academic_year_id = ?
                          AND student_specific_fee_id = ?
                    """, (sid, cid, year_id, fee["id"])).fetchone()

                    paid = float(paid_row["paid"] or 0)
                    outstanding = max(due - paid, 0.0)

                    student_fees.append({
                        "description": fee["description"] or "Student-specific fee",
                        "due": due,
                        "paid": paid,
                        "balance": outstanding
                    })
                    total_due += due
                    total_paid += paid

            if fee_item_id and not student_fees:
                continue

            total_balance = sum(item["balance"] for item in student_fees)

            if report_status == "owing" and total_balance <= 0.009:
                continue
            if report_status == "paid" and total_balance > 0.009:
                continue

            results.append({
                "student_id": student["student_id"] or "",
                "first_name": student["first_name"] or "",
                "last_name": student["last_name"] or "",
                "class_name": student["class_name"] or "Unassigned",
                "status": student["status"] or "Unknown",
                "fees": student_fees,
                "total_due": total_due,
                "total_paid": total_paid,
                "balance": total_balance
            })

        return render_template(
            "financial_balance_lists.html",
            school_settings=settings,
            logo_url=logo_url,
            active_year=active_year,
            classes=classes,
            fee_items=fee_items,
            students=results,
            report_status=report_status,
            class_id=class_id,
            fee_item_id=fee_item_id,
            error_message=None
        )

    finally:
        conn.close()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )















