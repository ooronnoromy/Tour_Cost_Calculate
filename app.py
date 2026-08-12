from functools import wraps
from contextlib import contextmanager
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, g, Response
import sqlite3
import csv
import io
import math
import os
import secrets
import hmac
from pathlib import Path
from datetime import datetime, date
from urllib.parse import urlsplit
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "tour_costs.db"

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.config.update(
    SECRET_KEY=os.environ.get("TOURCOST_SECRET_KEY", "dev-only-change-this-secret-key"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

DEFAULT_SUPER_ADMIN_USERNAME = "superadmin"
DEFAULT_SUPER_ADMIN_PASSWORD = "SuperAdmin@123"
ALLOWED_ROLES = ("super_admin", "admin", "user")
CREATABLE_ROLES = ("admin", "user")
EXPENSE_CATEGORIES = (
    "transport",
    "hotel",
    "food",
    "activities",
    "visa",
    "shopping",
    "other",
)
PERSONAL_EXPENSE_CATEGORIES = (
    "Food & dining",
    "Transport",
    "Shopping",
    "Accommodation",
    "Activities",
    "Health",
    "Communication",
    "Gifts",
    "Other",
)
PAYMENT_METHODS = ("Cash", "Card", "Mobile banking", "Bank transfer", "Other")
CATEGORY_DETAILS = {
    "transport": {"label": "Transport", "description": "Tickets and local travel", "icon": "↗"},
    "hotel": {"label": "Hotel", "description": "Accommodation", "icon": "⌂"},
    "food": {"label": "Food", "description": "Meals and refreshments", "icon": "◌"},
    "activities": {"label": "Activities", "description": "Tours and experiences", "icon": "☆"},
    "visa": {"label": "Visa", "description": "Permits and processing", "icon": "✓"},
    "shopping": {"label": "Shopping", "description": "Personal purchases", "icon": "◇"},
    "other": {"label": "Other", "description": "Additional costs", "icon": "•••"},
}


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tours (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                destination TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                travelers INTEGER NOT NULL,
                budget_limit REAL NOT NULL DEFAULT 0,
                notes TEXT DEFAULT '',
                total_cost REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        tour_columns = {row[1] for row in conn.execute("PRAGMA table_info(tours)").fetchall()}
        if "total_cost" not in tour_columns:
            conn.execute("ALTER TABLE tours ADD COLUMN total_cost REAL NOT NULL DEFAULT 0")
        if "transport_cost" not in tour_columns:
            conn.execute("ALTER TABLE tours ADD COLUMN transport_cost REAL NOT NULL DEFAULT 0")
        if "hotel_cost_per_night" not in tour_columns:
            conn.execute("ALTER TABLE tours ADD COLUMN hotel_cost_per_night REAL NOT NULL DEFAULT 0")
        if "hotel_nights" not in tour_columns:
            conn.execute("ALTER TABLE tours ADD COLUMN hotel_nights INTEGER NOT NULL DEFAULT 0")
        if "food_cost_per_person_per_day" not in tour_columns:
            conn.execute("ALTER TABLE tours ADD COLUMN food_cost_per_person_per_day REAL NOT NULL DEFAULT 0")
        if "food_days" not in tour_columns:
            conn.execute("ALTER TABLE tours ADD COLUMN food_days INTEGER NOT NULL DEFAULT 0")
        if "activities_cost" not in tour_columns:
            conn.execute("ALTER TABLE tours ADD COLUMN activities_cost REAL NOT NULL DEFAULT 0")
        if "visa_cost" not in tour_columns:
            conn.execute("ALTER TABLE tours ADD COLUMN visa_cost REAL NOT NULL DEFAULT 0")
        if "shopping_cost" not in tour_columns:
            conn.execute("ALTER TABLE tours ADD COLUMN shopping_cost REAL NOT NULL DEFAULT 0")
        if "other_cost" not in tour_columns:
            conn.execute("ALTER TABLE tours ADD COLUMN other_cost REAL NOT NULL DEFAULT 0")
        if "tax_percent" not in tour_columns:
            conn.execute("ALTER TABLE tours ADD COLUMN tax_percent REAL NOT NULL DEFAULT 0")
        if "contingency_percent" not in tour_columns:
            conn.execute("ALTER TABLE tours ADD COLUMN contingency_percent REAL NOT NULL DEFAULT 0")
        if "budget_limit" not in tour_columns:
            conn.execute("ALTER TABLE tours ADD COLUMN budget_limit REAL NOT NULL DEFAULT 0")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK(role IN ('super_admin', 'admin', 'user'))
            )
            """
        )
        expense_table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'expenses'"
        ).fetchone()
        if expense_table_sql and "CHECK(category" in (expense_table_sql[0] or ""):
            conn.execute("ALTER TABLE expenses RENAME TO expenses_legacy")
            conn.execute(
                """
                CREATE TABLE expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tour_id INTEGER NOT NULL,
                    created_by_id INTEGER,
                    category TEXT NOT NULL,
                    amount REAL NOT NULL,
                    expense_date TEXT NOT NULL,
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(tour_id) REFERENCES tours(id) ON DELETE CASCADE,
                    FOREIGN KEY(created_by_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO expenses (id, tour_id, category, amount, expense_date, notes, created_at, updated_at)
                SELECT id, tour_id, category, amount, expense_date, notes, created_at, updated_at
                FROM expenses_legacy
                """
            )
            conn.execute("DROP TABLE expenses_legacy")
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tour_id INTEGER NOT NULL,
                    created_by_id INTEGER,
                    category TEXT NOT NULL,
                    amount REAL NOT NULL,
                    expense_date TEXT NOT NULL,
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(tour_id) REFERENCES tours(id) ON DELETE CASCADE,
                    FOREIGN KEY(created_by_id) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
        expense_columns = {row[1] for row in conn.execute("PRAGMA table_info(expenses)").fetchall()}
        if "created_by_id" not in expense_columns:
            conn.execute("ALTER TABLE expenses ADD COLUMN created_by_id INTEGER")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS personal_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tour_id INTEGER,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                expense_date TEXT NOT NULL,
                payment_method TEXT NOT NULL DEFAULT 'Cash',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(tour_id) REFERENCES tours(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_personal_expenses_user_date ON personal_expenses(user_id, expense_date DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_expenses_tour_date ON expenses(tour_id, expense_date DESC)"
        )
        now = datetime.now().isoformat(timespec="seconds")
        default_super_admin = conn.execute(
            "SELECT id FROM users WHERE username = ? LIMIT 1",
            (DEFAULT_SUPER_ADMIN_USERNAME,),
        ).fetchone()
        if not default_super_admin:
            admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'super_admin'").fetchone()[0]
            if admin_count == 0:
                conn.execute(
                    """
                    INSERT INTO users (username, full_name, password_hash, role, created_at, updated_at)
                    VALUES (?, ?, ?, 'super_admin', ?, ?)
                    """,
                    (
                        DEFAULT_SUPER_ADMIN_USERNAME,
                        "Super Admin",
                        generate_password_hash(DEFAULT_SUPER_ADMIN_PASSWORD),
                        now,
                        now,
                    ),
                )


def safe_float(value, default=0.0):
    try:
        number = float(value or default)
        return max(number, 0.0) if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return max(int(value or default), 0)
    except (TypeError, ValueError):
        return default


def valid_iso_date(value):
    try:
        return bool(value) and date.fromisoformat(value) is not None
    except (TypeError, ValueError):
        return False


def form_to_data(form):
    return {
        "title": form.get("title", "").strip(),
        "destination": form.get("destination", "").strip(),
        "start_date": form.get("start_date", "").strip(),
        "end_date": form.get("end_date", "").strip(),
        "travelers": max(safe_int(form.get("travelers"), 1), 1),
        "budget_limit": safe_float(form.get("budget_limit")),
        "notes": form.get("notes", "").strip(),
    }


def expense_form_to_data(form):
    return {
        # Keep the user's capitalization while removing accidental extra spaces.
        "category": " ".join(form.get("category", "").strip().split()),
        "amount": safe_float(form.get("amount")),
        "expense_date": form.get("expense_date", "").strip(),
        "notes": form.get("notes", "").strip(),
    }


def personal_expense_form_to_data(form):
    tour_id = safe_int(form.get("tour_id")) or None
    return {
        "tour_id": tour_id,
        "category": " ".join(form.get("category", "").strip().split()),
        "amount": safe_float(form.get("amount")),
        "expense_date": form.get("expense_date", "").strip(),
        "payment_method": form.get("payment_method", "Cash").strip(),
        "notes": form.get("notes", "").strip(),
    }


def validate_tour(data):
    errors = []
    if not data["title"]:
        errors.append("Tour title is required.")
    elif len(data["title"]) > 120:
        errors.append("Tour title must be 120 characters or fewer.")
    if not data["destination"]:
        errors.append("Destination is required.")
    elif len(data["destination"]) > 120:
        errors.append("Destination must be 120 characters or fewer.")
    if not valid_iso_date(data["start_date"]) or not valid_iso_date(data["end_date"]):
        errors.append("Start date and end date are required.")
    elif data["end_date"] < data["start_date"]:
        errors.append("End date cannot be before start date.")
    if data["travelers"] < 1:
        errors.append("At least one traveler is required.")
    elif data["travelers"] > 10000:
        errors.append("Travelers cannot exceed 10,000.")
    if data["budget_limit"] > 1_000_000_000:
        errors.append("Budget target cannot exceed 1,000,000,000.")
    if len(data["notes"]) > 1000:
        errors.append("Tour notes must be 1,000 characters or fewer.")
    return errors


def validate_expense(data):
    errors = []
    if not data["category"]:
        errors.append("Expense category is required.")
    elif len(data["category"]) > 60:
        errors.append("Expense category must be 60 characters or fewer.")
    if data["amount"] <= 0 or data["amount"] > 1_000_000_000:
        errors.append("Expense amount must be between 0.01 and 1,000,000,000.")
    if not valid_iso_date(data["expense_date"]):
        errors.append("Expense date is required.")
    if len(data["notes"]) > 500:
        errors.append("Expense notes must be 500 characters or fewer.")
    return errors


def validate_personal_expense(data, conn):
    errors = validate_expense(data)
    if data["payment_method"] not in PAYMENT_METHODS:
        errors.append("Choose a valid payment method.")
    if data["tour_id"]:
        tour = conn.execute("SELECT id FROM tours WHERE id = ?", (data["tour_id"],)).fetchone()
        if not tour:
            errors.append("The selected tour no longer exists.")
    return errors


def legacy_cost_total(tour):
    travelers = max(int(tour["travelers"]), 1)
    transport_total = float(tour["transport_cost"] or 0)
    hotel_total = float(tour["hotel_cost_per_night"] or 0) * int(tour["hotel_nights"] or 0)
    food_total = float(tour["food_cost_per_person_per_day"] or 0) * int(tour["food_days"] or 0) * travelers
    extras_total = (
        float(tour["activities_cost"] or 0)
        + float(tour["visa_cost"] or 0)
        + float(tour["shopping_cost"] or 0)
        + float(tour["other_cost"] or 0)
    )
    base_total = transport_total + hotel_total + food_total + extras_total
    if base_total <= 0:
        return 0.0
    tax_amount = base_total * float(tour["tax_percent"] or 0) / 100
    contingency_amount = (base_total + tax_amount) * float(tour["contingency_percent"] or 0) / 100
    return base_total + tax_amount + contingency_amount


def sync_tour_total(conn, tour_id):
    total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE tour_id = ?",
        (tour_id,),
    ).fetchone()[0]
    conn.execute("UPDATE tours SET total_cost = ?, updated_at = ? WHERE id = ?", (float(total or 0), datetime.now().isoformat(timespec="seconds"), tour_id))


def get_tour_expenses(tour_id):
    with get_db() as conn:
        return conn.execute(
            """
            SELECT expenses.*, users.full_name AS added_by_name, users.username AS added_by_username
            FROM expenses
            LEFT JOIN users ON users.id = expenses.created_by_id
            WHERE expenses.tour_id = ?
            ORDER BY expenses.expense_date DESC, expenses.id DESC
            """,
            (tour_id,),
        ).fetchall()


def get_user_by_id(user_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_username(username):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def can_manage_tour_expense(expense):
    if not g.current_user:
        return False
    return (
        g.current_user["role"] in ("super_admin", "admin")
        or expense["created_by_id"] == g.current_user["id"]
    )


def personal_expense_query(user_id):
    month = request.args.get("month", "").strip()
    category = request.args.get("category", "").strip()
    tour_id = safe_int(request.args.get("tour_id")) or None
    q = request.args.get("q", "").strip()
    clauses = ["personal_expenses.user_id = ?"]
    params = [user_id]
    if month and len(month) == 7 and valid_iso_date(f"{month}-01"):
        clauses.append("substr(personal_expenses.expense_date, 1, 7) = ?")
        params.append(month)
    else:
        month = ""
    if category:
        clauses.append("personal_expenses.category = ?")
        params.append(category)
    if tour_id:
        clauses.append("personal_expenses.tour_id = ?")
        params.append(tour_id)
    if q:
        clauses.append("(personal_expenses.notes LIKE ? OR personal_expenses.category LIKE ?)")
        params.extend((f"%{q}%", f"%{q}%"))
    return " AND ".join(clauses), params, {
        "month": month,
        "category": category,
        "tour_id": tour_id,
        "q": q,
    }


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.current_user:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def super_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.current_user or g.current_user["role"] != "super_admin":
            flash("Super admin access is required.", "danger")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)

    return wrapped


def user_manager_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.current_user or g.current_user["role"] not in ("super_admin", "admin"):
            flash("Administrator access is required.", "danger")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)

    return wrapped


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.before_request
def protect_from_csrf():
    if request.method == "POST":
        expected = session.get("csrf_token", "")
        supplied = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            session.pop("csrf_token", None)
            get_csrf_token()
            flash("That form expired. Please try again; your page has been refreshed safely.", "warning")
            referrer = urlsplit(request.referrer or "")
            if referrer.netloc == request.host:
                location = referrer.path or "/"
                if referrer.query:
                    location = f"{location}?{referrer.query}"
                return redirect(location)
            return redirect(url_for("dashboard" if session.get("user_id") else "login"))


@app.before_request
def load_current_user():
    g.current_user = None
    user_id = session.get("user_id")
    if user_id:
        g.current_user = get_user_by_id(user_id)
        if g.current_user is None:
            session.clear()


@app.context_processor
def inject_user_state():
    current_user = g.current_user
    return {
        "current_user": current_user,
        "is_logged_in": current_user is not None,
        "is_super_admin": bool(current_user and current_user["role"] == "super_admin"),
        "can_manage_users": bool(current_user and current_user["role"] in ("super_admin", "admin")),
        "csrf_token": get_csrf_token,
    }


def calculate_costs(tour):
    travelers = max(int(tour["travelers"]), 1)
    category_totals = {category: 0.0 for category in EXPENSE_CATEGORIES}
    recorded_category_totals = {}
    expense_count = 0
    if "id" in tour.keys():
        with get_db() as conn:
            expense_rows = conn.execute(
                "SELECT category, amount FROM expenses WHERE tour_id = ? ORDER BY id",
                (tour["id"],),
            ).fetchall()
        expense_count = len(expense_rows)
        for row in expense_rows:
            category_label = " ".join((row["category"] or "").strip().split())
            category = category_label.casefold()
            amount = float(row["amount"] or 0)
            if not category:
                continue
            if category in category_totals:
                category_totals[category] += amount
            category_details = CATEGORY_DETAILS.get(category)
            recorded_category = recorded_category_totals.setdefault(
                category,
                {
                    "label": category_label,
                    "description": category_details["description"] if category_details else "Custom category",
                    "icon": category_details["icon"] if category_details else "+",
                    "style": category if category_details else "custom",
                    "amount": 0.0,
                },
            )
            recorded_category["amount"] += amount

    if expense_count:
        category_breakdown = list(recorded_category_totals.values())
        expense_total = sum(category["amount"] for category in category_breakdown)
    else:
        category_totals = {
            "transport": float(tour["transport_cost"] or 0),
            "hotel": float(tour["hotel_cost_per_night"] or 0) * int(tour["hotel_nights"] or 0),
            "food": float(tour["food_cost_per_person_per_day"] or 0) * int(tour["food_days"] or 0) * travelers,
            "activities": float(tour["activities_cost"] or 0),
            "visa": float(tour["visa_cost"] or 0),
            "shopping": float(tour["shopping_cost"] or 0),
            "other": float(tour["other_cost"] or 0),
        }
        expense_total = sum(category_totals.values())
        category_breakdown = [
            {
                "label": CATEGORY_DETAILS[category]["label"],
                "description": CATEGORY_DETAILS[category]["description"],
                "icon": CATEGORY_DETAILS[category]["icon"],
                "style": category,
                "amount": amount,
            }
            for category, amount in category_totals.items()
            if amount > 0
        ]

        # Older tours may only have a saved total without individual category values.
        stored_total = float(tour["total_cost"] or 0)
        if not category_breakdown and stored_total > 0:
            category_breakdown = [{
                "label": "Other",
                "description": "Saved tour cost",
                "icon": CATEGORY_DETAILS["other"]["icon"],
                "style": "other",
                "amount": stored_total,
            }]

    if expense_total > 0:
        base_total = expense_total
        grand_total = expense_total
        tax_amount = 0.0
        contingency_amount = 0.0
    else:
        stored_total = float(tour["total_cost"] or 0)
        base_total = stored_total
        grand_total = stored_total if stored_total > 0 else legacy_cost_total(tour)
        tax_amount = 0.0
        contingency_amount = 0.0
    return {
        "transport_total": category_totals["transport"],
        "hotel_total": category_totals["hotel"],
        "food_total": category_totals["food"],
        "activities_total": category_totals["activities"],
        "visa_total": category_totals["visa"],
        "shopping_total": category_totals["shopping"],
        "other_total": category_totals["other"],
        "category_breakdown": category_breakdown,
        "base_total": base_total,
        "tax_amount": tax_amount,
        "contingency_amount": contingency_amount,
        "grand_total": grand_total,
        "per_person": grand_total / travelers,
        "expense_count": expense_count,
    }


def get_tour_insights(tour, grand_total=None):
    """Return presentation-friendly schedule and budget health information."""
    today = date.today()
    start = date.fromisoformat(tour["start_date"])
    end = date.fromisoformat(tour["end_date"])
    if today < start:
        status, status_label = "upcoming", "Upcoming"
        timing_label = f"Starts in {(start - today).days} day{'s' if (start - today).days != 1 else ''}"
    elif today > end:
        status, status_label = "completed", "Completed"
        timing_label = f"Ended {(today - end).days} day{'s' if (today - end).days != 1 else ''} ago"
    else:
        status, status_label = "active", "In progress"
        timing_label = "Happening now"

    total = float(grand_total if grand_total is not None else calculate_costs(tour)["grand_total"])
    budget_limit = float(tour["budget_limit"] or 0) if "budget_limit" in tour.keys() else 0.0
    budget_remaining = budget_limit - total if budget_limit > 0 else None
    budget_percent = (total / budget_limit * 100) if budget_limit > 0 else 0.0
    return {
        "status": status,
        "status_label": status_label,
        "timing_label": timing_label,
        "duration_days": (end - start).days + 1,
        "budget_limit": budget_limit,
        "budget_remaining": budget_remaining,
        "budget_percent": budget_percent,
        "budget_progress": min(budget_percent, 100),
        "over_budget": bool(budget_remaining is not None and budget_remaining < 0),
    }


@app.context_processor
def inject_helpers():
    return {
        "calculate_costs": calculate_costs,
        "get_tour_insights": get_tour_insights,
        "can_manage_tour_expense": can_manage_tour_expense,
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.current_user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_user_by_username(username)
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            flash(f"Welcome back, {user['full_name']}.", "success")
            next_url = request.args.get("next")
            if next_url and next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("login"))


@app.route("/users", methods=["GET", "POST"])
@login_required
@user_manager_required
def manage_users():
    allowed_roles = CREATABLE_ROLES if g.current_user["role"] == "super_admin" else ("user",)
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "user")

        errors = []
        if not username:
            errors.append("Username is required.")
        if not full_name:
            errors.append("Full name is required.")
        if not password or len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if role not in allowed_roles:
            errors.append("You do not have permission to create that account type.")
        if username and get_user_by_username(username):
            errors.append("Username already exists.")

        if errors:
            for error in errors:
                flash(error, "danger")
        else:
            now = datetime.now().isoformat(timespec="seconds")
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO users (username, full_name, password_hash, role, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (username, full_name, generate_password_hash(password), role, now, now),
                )
            flash(f"{role.replace('_', ' ').title()} account created successfully.", "success")
            return redirect(url_for("manage_users"))

    with get_db() as conn:
        users = conn.execute(
            "SELECT * FROM users ORDER BY CASE role WHEN 'super_admin' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, id ASC"
        ).fetchall()
    editable_user_ids = {
        user["id"] for user in users
        if g.current_user["role"] == "super_admin" or user["role"] == "user"
    }
    return render_template(
        "users.html",
        users=users,
        creatable_roles=allowed_roles,
        editable_user_ids=editable_user_ids,
    )


@app.route("/users/<int:user_id>/edit", methods=["POST"])
@login_required
@user_manager_required
def edit_user_account(user_id):
    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            flash("User account not found.", "danger")
            return redirect(url_for("manage_users"))
        if g.current_user["role"] == "admin" and user["role"] != "user":
            flash("Administrators can only update standard user accounts.", "danger")
            return redirect(url_for("manage_users"))

        errors = []
        if not username:
            errors.append("Username is required.")
        elif len(username) > 60:
            errors.append("Username must be 60 characters or fewer.")
        duplicate = conn.execute(
            "SELECT id FROM users WHERE LOWER(username) = ? AND id != ? LIMIT 1",
            (username, user_id),
        ).fetchone()
        if duplicate:
            errors.append("Username already exists.")
        if password and len(password) < 8:
            errors.append("New password must be at least 8 characters.")

        if errors:
            for error in errors:
                flash(error, "danger")
            return redirect(url_for("manage_users"))

        now = datetime.now().isoformat(timespec="seconds")
        if password:
            conn.execute(
                "UPDATE users SET username = ?, password_hash = ?, updated_at = ? WHERE id = ?",
                (username, generate_password_hash(password), now, user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET username = ?, updated_at = ? WHERE id = ?",
                (username, now, user_id),
            )

    flash(f"Login details updated for {user['full_name']}.", "success")
    return redirect(url_for("manage_users"))


@app.route("/")
@login_required
def dashboard():
    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "all").strip().lower()
    sort = request.args.get("sort", "newest").strip().lower()
    if status_filter not in ("all", "upcoming", "active", "completed"):
        status_filter = "all"
    if sort not in ("newest", "soonest", "cost_high", "cost_low", "title"):
        sort = "newest"
    with get_db() as conn:
        if q:
            tours = conn.execute(
                "SELECT * FROM tours WHERE title LIKE ? OR destination LIKE ? ORDER BY id DESC",
                (f"%{q}%", f"%{q}%"),
            ).fetchall()
        else:
            tours = conn.execute("SELECT * FROM tours ORDER BY id DESC").fetchall()

    tour_items = []
    for tour in tours:
        costs = calculate_costs(tour)
        insights = get_tour_insights(tour, costs["grand_total"])
        if status_filter != "all" and insights["status"] != status_filter:
            continue
        tour_items.append({"tour": tour, "costs": costs, "insights": insights})

    if sort == "soonest":
        tour_items.sort(key=lambda item: (item["tour"]["start_date"], item["tour"]["id"]))
    elif sort == "cost_high":
        tour_items.sort(key=lambda item: item["costs"]["grand_total"], reverse=True)
    elif sort == "cost_low":
        tour_items.sort(key=lambda item: item["costs"]["grand_total"])
    elif sort == "title":
        tour_items.sort(key=lambda item: item["tour"]["title"].casefold())
    else:
        tour_items.sort(key=lambda item: item["tour"]["id"], reverse=True)

    visible_tours = [item["tour"] for item in tour_items]
    total_budget = sum(item["costs"]["grand_total"] for item in tour_items)
    total_travelers = sum(int(item["tour"]["travelers"]) for item in tour_items)
    with get_db() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        current_month = date.today().strftime("%Y-%m")
        personal_month_total = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM personal_expenses WHERE user_id = ? AND substr(expense_date, 1, 7) = ?",
            (g.current_user["id"], current_month),
        ).fetchone()[0]
    return render_template(
        "dashboard.html",
        tours=visible_tours,
        tour_items=tour_items,
        q=q,
        status_filter=status_filter,
        sort=sort,
        total_budget=total_budget,
        total_travelers=total_travelers,
        user_count=user_count,
        personal_month_total=float(personal_month_total or 0),
    )


@app.route("/personal-expenses", methods=["GET", "POST"])
@login_required
def personal_expenses():
    with get_db() as conn:
        if request.method == "POST":
            data = personal_expense_form_to_data(request.form)
            errors = validate_personal_expense(data, conn)
            if errors:
                for error in errors:
                    flash(error, "danger")
            else:
                now = datetime.now().isoformat(timespec="seconds")
                conn.execute(
                    """
                    INSERT INTO personal_expenses (
                        user_id, tour_id, category, amount, expense_date,
                        payment_method, notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        g.current_user["id"], data["tour_id"], data["category"],
                        data["amount"], data["expense_date"], data["payment_method"],
                        data["notes"], now, now,
                    ),
                )
                flash("Personal expense added to your private ledger.", "success")
                return redirect(url_for("personal_expenses"))

        where_sql, params, filters = personal_expense_query(g.current_user["id"])
        expenses = conn.execute(
            f"""
            SELECT personal_expenses.*, tours.title AS tour_title
            FROM personal_expenses
            LEFT JOIN tours ON tours.id = personal_expenses.tour_id
            WHERE {where_sql}
            ORDER BY personal_expenses.expense_date DESC, personal_expenses.id DESC
            """,
            params,
        ).fetchall()
        tours = conn.execute("SELECT id, title FROM tours ORDER BY title COLLATE NOCASE").fetchall()
        all_time_total = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM personal_expenses WHERE user_id = ?",
            (g.current_user["id"],),
        ).fetchone()[0]
        current_month = date.today().strftime("%Y-%m")
        month_total = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM personal_expenses WHERE user_id = ? AND substr(expense_date, 1, 7) = ?",
            (g.current_user["id"], current_month),
        ).fetchone()[0]

    filtered_total = sum(float(item["amount"]) for item in expenses)
    category_totals = {}
    for item in expenses:
        category_totals[item["category"]] = category_totals.get(item["category"], 0) + float(item["amount"])
    category_breakdown = sorted(category_totals.items(), key=lambda item: item[1], reverse=True)
    return render_template(
        "personal_expenses.html",
        expenses=expenses,
        tours=tours,
        filters=filters,
        categories=PERSONAL_EXPENSE_CATEGORIES,
        payment_methods=PAYMENT_METHODS,
        filtered_total=filtered_total,
        all_time_total=float(all_time_total or 0),
        month_total=float(month_total or 0),
        current_month=current_month,
        category_breakdown=category_breakdown,
        today=date.today().isoformat(),
    )


@app.route("/personal-expenses/<int:expense_id>/edit", methods=["POST"])
@login_required
def edit_personal_expense(expense_id):
    with get_db() as conn:
        expense = conn.execute(
            "SELECT id FROM personal_expenses WHERE id = ? AND user_id = ?",
            (expense_id, g.current_user["id"]),
        ).fetchone()
        if not expense:
            flash("Personal expense not found.", "danger")
            return redirect(url_for("personal_expenses"))
        data = personal_expense_form_to_data(request.form)
        errors = validate_personal_expense(data, conn)
        if errors:
            for error in errors:
                flash(error, "danger")
        else:
            conn.execute(
                """
                UPDATE personal_expenses SET tour_id = ?, category = ?, amount = ?,
                    expense_date = ?, payment_method = ?, notes = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    data["tour_id"], data["category"], data["amount"], data["expense_date"],
                    data["payment_method"], data["notes"], datetime.now().isoformat(timespec="seconds"),
                    expense_id, g.current_user["id"],
                ),
            )
            flash("Personal expense updated.", "success")
    return redirect(url_for("personal_expenses"))


@app.route("/personal-expenses/<int:expense_id>/delete", methods=["POST"])
@login_required
def delete_personal_expense(expense_id):
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM personal_expenses WHERE id = ? AND user_id = ?",
            (expense_id, g.current_user["id"]),
        )
    flash("Personal expense deleted." if cursor.rowcount else "Personal expense not found.", "success" if cursor.rowcount else "danger")
    return redirect(url_for("personal_expenses"))


@app.route("/personal-expenses/export.csv")
@login_required
def export_personal_expenses():
    where_sql, params, _ = personal_expense_query(g.current_user["id"])
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT personal_expenses.expense_date, personal_expenses.category,
                   personal_expenses.amount, personal_expenses.payment_method,
                   COALESCE(tours.title, '') AS tour_title, personal_expenses.notes
            FROM personal_expenses LEFT JOIN tours ON tours.id = personal_expenses.tour_id
            WHERE {where_sql}
            ORDER BY personal_expenses.expense_date DESC, personal_expenses.id DESC
            """,
            params,
        ).fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("Date", "Category", "Amount (BDT)", "Payment method", "Linked tour", "Notes"))
    writer.writerows(tuple(row) for row in rows)
    filename = f"personal-expenses-{date.today().isoformat()}.csv"
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/tour/new", methods=["GET", "POST"])
@login_required
def new_tour():
    data = None
    if request.method == "POST":
        data = form_to_data(request.form)
        errors = validate_tour(data)
        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("tour_form.html", tour=data, mode="Create")

        now = datetime.now().isoformat(timespec="seconds")
        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tours (
                    title, destination, start_date, end_date, travelers, budget_limit, notes, total_cost, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["title"], data["destination"], data["start_date"], data["end_date"], data["travelers"], data["budget_limit"], data["notes"], 0, now, now,
                ),
            )
            tour_id = cursor.lastrowid
        flash("Tour created successfully. Add expenses next.", "success")
        return redirect(url_for("view_tour", tour_id=tour_id))

    return render_template("tour_form.html", tour=data, mode="Create")


@app.route("/tour/<int:tour_id>")
@login_required
def view_tour(tour_id):
    with get_db() as conn:
        tour = conn.execute("SELECT * FROM tours WHERE id = ?", (tour_id,)).fetchone()
    if not tour:
        flash("Tour not found.", "danger")
        return redirect(url_for("dashboard"))
    costs = calculate_costs(tour)
    insights = get_tour_insights(tour, costs["grand_total"])
    expenses = get_tour_expenses(tour_id)
    return render_template(
        "tour_detail.html",
        tour=tour,
        costs=costs,
        insights=insights,
        expenses=expenses,
        expense_categories=EXPENSE_CATEGORIES,
        today=datetime.now().date().isoformat(),
    )


@app.route("/tour/<int:tour_id>/print")
@login_required
def print_tour(tour_id):
    with get_db() as conn:
        tour = conn.execute("SELECT * FROM tours WHERE id = ?", (tour_id,)).fetchone()
    if not tour:
        flash("Tour not found.", "danger")
        return redirect(url_for("dashboard"))

    costs = calculate_costs(tour)
    insights = get_tour_insights(tour, costs["grand_total"])
    expenses = get_tour_expenses(tour_id)
    return render_template(
        "print_tour.html",
        tour=tour,
        costs=costs,
        insights=insights,
        expenses=expenses,
        generated_at=datetime.now(),
    )


@app.route("/tour/<int:tour_id>/edit", methods=["GET", "POST"])
@login_required
def edit_tour(tour_id):
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM tours WHERE id = ?", (tour_id,)).fetchone()
    if not existing:
        flash("Tour not found.", "danger")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        data = form_to_data(request.form)
        errors = validate_tour(data)
        if errors:
            for error in errors:
                flash(error, "danger")
            data["id"] = tour_id
            return render_template("tour_form.html", tour=data, mode="Edit")

        now = datetime.now().isoformat(timespec="seconds")
        with get_db() as conn:
            conn.execute(
                """
                UPDATE tours SET
                    title=?, destination=?, start_date=?, end_date=?, travelers=?, budget_limit=?, notes=?, updated_at=?
                WHERE id=?
                """,
                (
                    data["title"], data["destination"], data["start_date"], data["end_date"], data["travelers"], data["budget_limit"], data["notes"], now, tour_id,
                ),
            )
        flash("Tour budget updated successfully.", "success")
        return redirect(url_for("view_tour", tour_id=tour_id))

    return render_template("tour_form.html", tour=existing, mode="Edit")


@app.route("/tour/<int:tour_id>/expenses/add", methods=["POST"])
@login_required
def add_expense(tour_id):
    with get_db() as conn:
        tour = conn.execute("SELECT id FROM tours WHERE id = ?", (tour_id,)).fetchone()
        if not tour:
            flash("Tour not found.", "danger")
            return redirect(url_for("dashboard"))

        data = expense_form_to_data(request.form)
        errors = validate_expense(data)
        if errors:
            for error in errors:
                flash(error, "danger")
            return redirect(url_for("view_tour", tour_id=tour_id))

        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """
            INSERT INTO expenses (tour_id, created_by_id, category, amount, expense_date, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tour_id, g.current_user["id"], data["category"], data["amount"], data["expense_date"], data["notes"], now, now),
        )
        sync_tour_total(conn, tour_id)

    flash("Expense added successfully.", "success")
    return redirect(url_for("view_tour", tour_id=tour_id))


@app.route("/tour/<int:tour_id>/expenses/<int:expense_id>/delete", methods=["POST"])
@login_required
def delete_expense(tour_id, expense_id):
    with get_db() as conn:
        expense = conn.execute(
            "SELECT * FROM expenses WHERE id = ? AND tour_id = ?", (expense_id, tour_id)
        ).fetchone()
        if not expense:
            flash("Expense not found.", "danger")
            return redirect(url_for("view_tour", tour_id=tour_id))
        if not can_manage_tour_expense(expense):
            flash("You can only manage expenses you added.", "danger")
            return redirect(url_for("view_tour", tour_id=tour_id))
        conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        sync_tour_total(conn, tour_id)
    flash("Expense deleted.", "success")
    return redirect(url_for("view_tour", tour_id=tour_id))


@app.route("/tour/<int:tour_id>/expenses/<int:expense_id>/edit", methods=["POST"])
@login_required
def edit_expense(tour_id, expense_id):
    with get_db() as conn:
        expense = conn.execute(
            "SELECT * FROM expenses WHERE id = ? AND tour_id = ?", (expense_id, tour_id)
        ).fetchone()
        if not expense:
            flash("Expense not found.", "danger")
            return redirect(url_for("view_tour", tour_id=tour_id))
        if not can_manage_tour_expense(expense):
            flash("You can only manage expenses you added.", "danger")
            return redirect(url_for("view_tour", tour_id=tour_id))
        data = expense_form_to_data(request.form)
        errors = validate_expense(data)
        if errors:
            for error in errors:
                flash(error, "danger")
        else:
            conn.execute(
                """
                UPDATE expenses SET category = ?, amount = ?, expense_date = ?, notes = ?, updated_at = ?
                WHERE id = ? AND tour_id = ?
                """,
                (
                    data["category"], data["amount"], data["expense_date"], data["notes"],
                    datetime.now().isoformat(timespec="seconds"), expense_id, tour_id,
                ),
            )
            sync_tour_total(conn, tour_id)
            flash("Expense updated.", "success")
    return redirect(url_for("view_tour", tour_id=tour_id))


@app.route("/tour/<int:tour_id>/expenses/export.csv")
@login_required
def export_tour_expenses(tour_id):
    with get_db() as conn:
        tour = conn.execute("SELECT title FROM tours WHERE id = ?", (tour_id,)).fetchone()
        if not tour:
            flash("Tour not found.", "danger")
            return redirect(url_for("dashboard"))
        rows = conn.execute(
            """
            SELECT expenses.expense_date, expenses.category, expenses.amount,
                   COALESCE(users.full_name, '') AS added_by, expenses.notes
            FROM expenses
            LEFT JOIN users ON users.id = expenses.created_by_id
            WHERE expenses.tour_id = ?
            ORDER BY expenses.expense_date DESC, expenses.id DESC
            """,
            (tour_id,),
        ).fetchall()

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("Date", "Category", "Amount (BDT)", "Added by", "Notes"))
    writer.writerows(tuple(row) for row in rows)
    safe_title = "".join(char if char.isalnum() else "-" for char in tour["title"]).strip("-").lower()
    filename = f"{safe_title or 'tour'}-expenses-{date.today().isoformat()}.csv"
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/tour/<int:tour_id>/delete", methods=["POST"])
@login_required
@super_admin_required
def delete_tour(tour_id):
    with get_db() as conn:
        conn.execute("DELETE FROM tours WHERE id = ?", (tour_id,))
    flash("Tour deleted.", "success")
    return redirect(url_for("dashboard"))


@app.route("/api/calculate", methods=["POST"])
@login_required
def api_calculate():
    payload = request.get_json(silent=True) or {}
    total_cost = safe_float(payload.get("total_cost"))
    travelers = max(safe_int(payload.get("travelers"), 1), 1)
    return jsonify({"grand_total": total_cost, "per_person": total_cost / travelers})


if __name__ == "__main__":
    init_db()
    # Local development reloads automatically. Set FLASK_DEBUG=0 in production.
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=safe_int(os.environ.get("PORT"), 5000) or 5000,
        debug=os.environ.get("FLASK_DEBUG", "1") == "1",
    )
