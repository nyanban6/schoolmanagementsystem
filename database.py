import sqlite3
import os
import sys
from werkzeug.security import generate_password_hash

# ============================================================
# SCHOOL MANAGEMENT SYSTEM DATABASE
# ============================================================

def _resolve_app_base():
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        for candidate in (exe_dir, os.path.dirname(exe_dir)):
            if (os.path.isfile(os.path.join(candidate, 'database', 'platform.db'))
                    or os.path.isdir(os.path.join(candidate, 'database', 'schools'))):
                return candidate
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = _resolve_app_base()

# Original school database containing the existing school records
DATABASE_PATH = os.path.join(BASE_DIR, "database", "school.db")

# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection(database_path=None):

    if database_path is None:
        try:
            from flask import session
            selected_database = session.get("school_db")

            if selected_database:
                database_path = selected_database

        except Exception:
            pass

    if not database_path:
        database_path = DATABASE_PATH

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row

    return connection


# ============================================================
# CREATE / UPDATE TABLES
# ============================================================

def create_tables():

    connection = get_connection()
    cursor = connection.cursor()

    # --------------------------------------------------------
    # USERS
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'Administrator',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # ACADEMIC YEARS
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS academic_years (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year_name TEXT UNIQUE NOT NULL,
            is_active INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # CLASSES
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            amount REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # SCHOOL SETTINGS
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS school_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_name TEXT,
            address TEXT,
            phone TEXT,
            email TEXT,
            logo TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # STUDENTS
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT UNIQUE,
            first_name TEXT,
            middle_name TEXT,
            last_name TEXT,
            gender TEXT,
            date_of_birth TEXT,
            phone TEXT,
            address TEXT,
            parent_name TEXT,
            parent_phone TEXT,
            photo TEXT,
            class_id INTEGER,
            academic_year_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # TEACHERS
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id TEXT UNIQUE,
            first_name TEXT,
            middle_name TEXT,
            last_name TEXT,
            gender TEXT,
            phone TEXT,
            address TEXT,
            email TEXT,
            subject TEXT,
            salary_rate REAL DEFAULT 0,
            photo TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # TEACHER ATTENDANCE
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS teacher_attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER,
            attendance_date TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # TEACHER SALARIES
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS teacher_salaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER,
            month TEXT,
            days_present INTEGER DEFAULT 0,
            leave_days INTEGER DEFAULT 0,
            salary_amount REAL DEFAULT 0,
            paid_amount REAL DEFAULT 0,
            balance REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # STUDENT FEE SETTINGS
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS student_fee_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER,
            description TEXT,
            amount REAL DEFAULT 0,
            academic_year_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # STUDENT PAYMENTS
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS student_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            fee_setting_id INTEGER,
            amount_paid REAL DEFAULT 0,
            payment_date TEXT,
            receipt_number TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # STUDENT FEE ITEMS
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS student_fee_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER,
            description TEXT,
            amount REAL DEFAULT 0,
            academic_year_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # STUDENT FEE PAYMENTS
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS student_fee_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            fee_item_id INTEGER,
            amount_paid REAL DEFAULT 0,
            payment_date TEXT,
            receipt_number TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # STUDENT ATTENDANCE
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS student_attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            attendance_date TEXT,
            status TEXT,
            remarks TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # SUBJECTS
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            class_id INTEGER,
            academic_year_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # INDEXES
    # --------------------------------------------------------

    try:
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_students_class
            ON students(class_id)
            """
        )
    except Exception:
        pass

    try:
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_students_year
            ON students(academic_year_id)
            """
        )
    except Exception:
        pass

    try:
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_teacher_attendance_teacher
            ON teacher_attendance(teacher_id)
            """
        )
    except Exception:
        pass

    try:
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_student_attendance_student
            ON student_attendance(student_id)
            """
        )
    except Exception:
        pass

    # --------------------------------------------------------
    # DEFAULT ADMIN
    # --------------------------------------------------------

    user = cursor.execute(
        "SELECT id FROM users WHERE username = ?",
        ("admin",)
    ).fetchone()

    if not user:

        cursor.execute(
            """
            INSERT INTO users
            (username, password, role)
            VALUES (?, ?, ?)
            """,
            (
                "admin",
                generate_password_hash("admin123"),
                "Administrator"
            )
        )

    # --------------------------------------------------------
    # ACTIVE ACADEMIC YEAR
    # --------------------------------------------------------

    year_count = cursor.execute(
        "SELECT COUNT(*) FROM academic_years"
    ).fetchone()[0]

    if year_count > 0:

        active_count = cursor.execute(
            """
            SELECT COUNT(*)
            FROM academic_years
            WHERE is_active = 1
            """
        ).fetchone()[0]

        if active_count == 0:

            latest_year = cursor.execute(
                """
                SELECT id
                FROM academic_years
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

            if latest_year:

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
                    (latest_year["id"],)
                )

    connection.commit()
    connection.close()


# ============================================================
# CREATE ADMIN
# ============================================================

def create_admin():

    connection = get_connection()
    cursor = connection.cursor()

    user = cursor.execute(
        "SELECT id FROM users WHERE username = ?",
        ("admin",)
    ).fetchone()

    if not user:

        cursor.execute(
            """
            INSERT INTO users
            (username, password, role)
            VALUES (?, ?, ?)
            """,
            (
                "admin",
                generate_password_hash("admin123"),
                "Administrator"
            )
        )

        connection.commit()

    connection.close()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print("School Management System database")
    print("----------------------------------")
    print("Database location:")
    print(DATABASE_PATH)

    create_tables()
    create_admin()

    print("")
    print("Database setup completed.")