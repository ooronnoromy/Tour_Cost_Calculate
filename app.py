from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, g
import sqlite3
from pathlib import Path
from datetime import datetime
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "tour_costs.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-secret-key"

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


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
                    category TEXT NOT NULL,
                    amount REAL NOT NULL,
                    expense_date TEXT NOT NULL,
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(tour_id) REFERENCES tours(id) ON DELETE CASCADE
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
                    category TEXT NOT NULL,
                    amount REAL NOT NULL,
                    expense_date TEXT NOT NULL,
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(tour_id) REFERENCES tours(id) ON DELETE CASCADE
                )
                """
            )
        now = datetime.now().isoformat(timespec="seconds")
        default_super_admin = conn.execute(
            "SELECT id FROM users WHERE username = ? LIMIT 1",
            (DEFAULT_SUPER_ADMIN_USERNAME,),
        ).fetchone()
        if default_super_admin:
            conn.execute(
                """
                UPDATE users
                SET full_name = 'Super Admin', password_hash = ?, role = 'super_admin', updated_at = ?
                WHERE id = ?
                """,
                (generate_password_hash(DEFAULT_SUPER_ADMIN_PASSWORD), now, default_super_admin["id"]),
            )

        else:
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
        return max(float(value or default), 0.0)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return max(int(value or default), 0)
    except (TypeError, ValueError):
        return default


def form_to_data(form):
    return {
        "title": form.get("title", "").strip(),
        "destination": form.get("destination", "").strip(),
        "start_date": form.get("start_date", "").strip(),
        "end_date": form.get("end_date", "").strip(),
        "travelers": max(safe_int(form.get("travelers"), 1), 1),
        "notes": form.get("notes", "").strip(),
    }


def expense_form_to_data(form):
    return {
        "category": form.get("category", "transport").strip(),
        "amount": safe_float(form.get("amount")),
        "expense_date": form.get("expense_date", "").strip(),
        "notes": form.get("notes", "").strip(),
    }


def validate_tour(data):
    errors = []
    if not data["title"]:
        errors.append("Tour title is required.")
    if not data["destination"]:
        errors.append("Destination is required.")
    if not data["start_date"] or not data["end_date"]:
        errors.append("Start date and end date are required.")
    elif data["end_date"] < data["start_date"]:
        errors.append("End date cannot be before start date.")
    if data["travelers"] < 1:
        errors.append("At least one traveler is required.")
    return errors


def validate_expense(data):
    errors = []
    if not data["category"]:
        errors.append("Expense category is required.")
    if data["amount"] <= 0:
        errors.append("Expense amount must be greater than zero.")
    if not data["expense_date"]:
        errors.append("Expense date is required.")
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
            "SELECT * FROM expenses WHERE tour_id = ? ORDER BY expense_date DESC, id DESC",
            (tour_id,),
        ).fetchall()


def get_user_by_id(user_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_username(username):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


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
        "can_manage_users": bool(current_user and current_user["role"] == "super_admin"),
    }


def calculate_costs(tour):
    travelers = max(int(tour["travelers"]), 1)
    category_totals = {category: 0.0 for category in EXPENSE_CATEGORIES}
    expense_count = 0
    if "id" in tour.keys():
        with get_db() as conn:
            expense_rows = conn.execute(
                "SELECT category, SUM(amount) AS amount FROM expenses WHERE tour_id = ? GROUP BY category",
                (tour["id"],),
            ).fetchall()
        expense_count = len(expense_rows)
        for row in expense_rows:
            category_totals[row["category"]] = float(row["amount"] or 0)

    expense_total = sum(category_totals.values())
    if expense_total <= 0:
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
        "base_total": base_total,
        "tax_amount": tax_amount,
        "contingency_amount": contingency_amount,
        "grand_total": grand_total,
        "per_person": grand_total / travelers,
        "expense_count": expense_count,
    }


@app.context_processor
def inject_helpers():
    return {"calculate_costs": calculate_costs}


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
            if next_url and next_url.startswith("/"):
                return redirect(next_url)
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "danger")

    return render_template(
        "login.html",
        default_username=DEFAULT_SUPER_ADMIN_USERNAME,
        default_password=DEFAULT_SUPER_ADMIN_PASSWORD,
    )


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("login"))


@app.route("/users", methods=["GET", "POST"])
@login_required
@super_admin_required
def manage_users():
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
        if role not in CREATABLE_ROLES:
            errors.append("Only admin and user roles can be created here.")
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
    return render_template("users.html", users=users)


@app.route("/")
@login_required
def dashboard():
    q = request.args.get("q", "").strip()
    with get_db() as conn:
        if q:
            tours = conn.execute(
                "SELECT * FROM tours WHERE title LIKE ? OR destination LIKE ? ORDER BY id DESC",
                (f"%{q}%", f"%{q}%"),
            ).fetchall()
        else:
            tours = conn.execute("SELECT * FROM tours ORDER BY id DESC").fetchall()

    total_budget = sum(calculate_costs(t)["grand_total"] for t in tours)
    total_travelers = sum(int(t["travelers"]) for t in tours)
    with get_db() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return render_template(
        "dashboard.html",
        tours=tours,
        q=q,
        total_budget=total_budget,
        total_travelers=total_travelers,
        user_count=user_count,
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
                    title, destination, start_date, end_date, travelers, notes, total_cost, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["title"], data["destination"], data["start_date"], data["end_date"], data["travelers"], data["notes"], 0, now, now,
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
    expenses = get_tour_expenses(tour_id)
    return render_template(
        "tour_detail.html",
        tour=tour,
        costs=costs,
        expenses=expenses,
        expense_categories=EXPENSE_CATEGORIES,
        today=datetime.now().date().isoformat(),
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
                    title=?, destination=?, start_date=?, end_date=?, travelers=?, notes=?, updated_at=?
                WHERE id=?
                """,
                (
                    data["title"], data["destination"], data["start_date"], data["end_date"], data["travelers"], data["notes"], now, tour_id,
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
            INSERT INTO expenses (tour_id, category, amount, expense_date, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (tour_id, data["category"], data["amount"], data["expense_date"], data["notes"], now, now),
        )
        sync_tour_total(conn, tour_id)

    flash("Expense added successfully.", "success")
    return redirect(url_for("view_tour", tour_id=tour_id))


@app.route("/tour/<int:tour_id>/expenses/<int:expense_id>/delete", methods=["POST"])
@login_required
@super_admin_required
def delete_expense(tour_id, expense_id):
    with get_db() as conn:
        conn.execute("DELETE FROM expenses WHERE id = ? AND tour_id = ?", (expense_id, tour_id))
        sync_tour_total(conn, tour_id)
    flash("Expense deleted.", "success")
    return redirect(url_for("view_tour", tour_id=tour_id))


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
    app.run(debug=True)
