import tempfile
import unittest
from pathlib import Path
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

import app as tourcost


class TourCostIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = tourcost.DB_PATH
        tourcost.DB_PATH = Path(self.temp_dir.name) / "test.db"
        tourcost.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        tourcost.init_db()
        self.client = tourcost.app.test_client()
        self.login("superadmin", "SuperAdmin@123")

    def tearDown(self):
        tourcost.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def login(self, username, password):
        self.client.get("/login")
        return self.post(
            "/login", data={"username": username, "password": password}, follow_redirects=True
        )

    def post(self, url, data=None, **kwargs):
        with self.client.session_transaction() as flask_session:
            token = flask_session["csrf_token"]
        form_data = dict(data or {})
        form_data["csrf_token"] = token
        return self.client.post(url, data=form_data, **kwargs)

    def create_tour(self):
        response = self.post(
            "/tour/new",
            data={
                "title": "Summer break", "traveller_names": ["Ayesha Rahman", "Nadia Islam"], "destination": "Sylhet",
                "start_date": "2026-09-01", "end_date": "2026-09-04",
                "travelers": "2", "notes": "",
            },
        )
        return int(response.headers["Location"].rstrip("/").split("/")[-1])

    def add_user(self, username, role="user"):
        with tourcost.get_db() as conn:
            now = "2026-08-13T12:00:00"
            conn.execute(
                """INSERT INTO users (username, full_name, password_hash, role, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (username, username.title(), generate_password_hash("password123"), role, now, now),
            )

    def test_tour_form_saves_and_displays_all_traveller_names(self):
        form = self.client.get("/tour/new")
        self.assertIn(b"travellerNameFields", form.data)
        self.assertIn(b"name = 'traveller_names'", form.data)

        tour_id = self.create_tour()
        with tourcost.get_db() as conn:
            saved_name = conn.execute(
                "SELECT traveller_name FROM tours WHERE id = ?", (tour_id,)
            ).fetchone()[0]
        self.assertEqual(saved_name, "Ayesha Rahman\nNadia Islam")
        detail = self.client.get(f"/tour/{tour_id}")
        self.assertIn(b"Ayesha Rahman", detail.data)
        self.assertIn(b"Nadia Islam", detail.data)

        invalid = self.post(
            "/tour/new",
            data={
                "title": "Incomplete group", "traveller_names": ["Only One", "Only Two"],
                "destination": "Dhaka", "start_date": "2026-10-01",
                "end_date": "2026-10-02", "travelers": "3", "notes": "",
            },
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertIn(b"Enter one traveller name for each traveler", invalid.data)
        with tourcost.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tours").fetchone()[0], 1)

    def test_personal_expense_is_private_editable_and_exportable(self):
        response = self.post(
            "/personal-expenses",
            data={
                "category": "Food & dining", "amount": "450.50",
                "expense_date": "2026-08-13", "payment_method": "Cash",
                "tour_id": "", "notes": "Lunch",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Personal expense added", response.data)
        with tourcost.get_db() as conn:
            expense = conn.execute("SELECT id, tour_id FROM personal_expenses").fetchone()
            expense_id = expense["id"]
            self.assertIsNone(expense["tour_id"])

        unlinked = self.client.get("/personal-expenses?tour_id=unlinked")
        self.assertIn(b"Not linked to any tour", unlinked.data)
        self.assertIn(b"Lunch", unlinked.data)
        self.assertIn(b"Do not link to a tour", unlinked.data)

        response = self.post(
            f"/personal-expenses/{expense_id}/edit",
            data={
                "category": "Food & dining", "amount": "500",
                "expense_date": "2026-08-13", "payment_method": "Card",
                "tour_id": "", "notes": "Lunch and tea",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Personal expense updated", response.data)
        export = self.client.get("/personal-expenses/export.csv")
        self.assertEqual(export.status_code, 200)
        self.assertIn(b"500.0", export.data)
        self.assertIn(b"Lunch and tea", export.data)

        self.add_user("traveler")
        self.post("/logout")
        self.login("traveler", "password123")
        self.assertNotIn(b"Lunch and tea", self.client.get("/personal-expenses").data)
        self.post(f"/personal-expenses/{expense_id}/delete")
        with tourcost.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM personal_expenses").fetchone()[0], 1)

    def test_tour_expense_creator_can_edit_but_other_user_cannot(self):
        tour_id = self.create_tour()
        self.post(
            f"/tour/{tour_id}/expenses/add",
            data={"category": "Transport", "amount": "100", "expense_date": "2026-09-01", "notes": "Bus"},
        )
        with tourcost.get_db() as conn:
            expense_id = conn.execute("SELECT id FROM expenses").fetchone()[0]
        self.post(
            f"/tour/{tour_id}/expenses/{expense_id}/edit",
            data={"category": "Transport", "amount": "125", "expense_date": "2026-09-01", "notes": "Bus fare"},
        )
        with tourcost.get_db() as conn:
            self.assertEqual(conn.execute("SELECT amount FROM expenses").fetchone()[0], 125)

        self.add_user("viewer")
        self.post("/logout")
        self.login("viewer", "password123")
        response = self.post(
            f"/tour/{tour_id}/expenses/{expense_id}/edit",
            data={"category": "Transport", "amount": "1", "expense_date": "2026-09-01", "notes": ""},
            follow_redirects=True,
        )
        self.assertIn(b"You can only manage expenses you added", response.data)
        with tourcost.get_db() as conn:
            self.assertEqual(conn.execute("SELECT amount FROM expenses").fetchone()[0], 125)

    def test_traveller_payment_popup_records_money_and_rejects_unknown_name(self):
        tour_id = self.create_tour()
        self.post(
            f"/tour/{tour_id}/expenses/add",
            data={"category": "Hotel", "amount": "2000", "expense_date": "2026-09-01", "notes": ""},
        )
        detail = self.client.get(f"/tour/{tour_id}")
        self.assertIn(b'data-bs-target="#recordPaymentModal"', detail.data)
        self.assertIn(b"Record payment", detail.data)
        self.assertIn(b"Ayesha Rahman", detail.data)
        self.assertIn(b"Nadia Islam", detail.data)

        response = self.post(
            f"/tour/{tour_id}/payments/add",
            data={
                "traveller_name": "Nadia Islam", "amount": "1250.50",
                "payment_date": "2026-08-13", "notes": "First installment",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Payment recorded for Nadia Islam", response.data)
        self.assertIn(b"1,250.50", response.data)
        self.assertIn(b"First installment", response.data)
        self.assertIn(b"Due amount", response.data)
        self.assertIn(b"Backable", response.data)
        self.assertIn(b'balance-due">&#2547;1,000.00', response.data)
        self.assertIn(b'balance-backable">&#2547;250.50', response.data)
        with tourcost.get_db() as conn:
            payment = conn.execute("SELECT * FROM tour_payments").fetchone()
            self.assertEqual(payment["traveller_name"], "Nadia Islam")
            self.assertEqual(payment["amount"], 1250.5)

        response = self.post(
            f"/tour/{tour_id}/payments/add",
            data={
                "traveller_name": "Unknown Person", "amount": "500",
                "payment_date": "2026-08-13", "notes": "",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Choose a valid traveller from this tour", response.data)
        with tourcost.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tour_payments").fetchone()[0], 1)

    def test_only_super_admin_can_edit_or_delete_payment_history(self):
        tour_id = self.create_tour()
        self.post(
            f"/tour/{tour_id}/payments/add",
            data={
                "traveller_name": "Ayesha Rahman", "amount": "1000",
                "payment_date": "2026-08-13", "notes": "Initial payment",
            },
            follow_redirects=True,
        )
        with tourcost.get_db() as conn:
            payment_id = conn.execute("SELECT id FROM tour_payments").fetchone()[0]

        response = self.post(
            f"/tour/{tour_id}/payments/{payment_id}/edit",
            data={
                "traveller_name": "Ayesha Rahman", "amount": "1500",
                "payment_date": "2026-08-14", "notes": "Updated amount",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Payment updated", response.data)
        with tourcost.get_db() as conn:
            payment = conn.execute("SELECT amount, notes FROM tour_payments WHERE id = ?", (payment_id,)).fetchone()
            self.assertEqual(payment["amount"], 1500)
            self.assertEqual(payment["notes"], "Updated amount")

        self.add_user("manager", role="admin")
        self.post("/logout")
        self.login("manager", "password123")
        detail = self.client.get(f"/tour/{tour_id}")
        self.assertNotIn(
            f"/tour/{tour_id}/payments/{payment_id}/edit".encode(),
            detail.data,
        )
        self.assertNotIn(
            f"/tour/{tour_id}/payments/{payment_id}/delete".encode(),
            detail.data,
        )

        response = self.post(
            f"/tour/{tour_id}/payments/{payment_id}/edit",
            data={
                "traveller_name": "Ayesha Rahman", "amount": "9999",
                "payment_date": "2026-08-15", "notes": "Tamper",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Super admin access is required", response.data)
        with tourcost.get_db() as conn:
            self.assertEqual(conn.execute("SELECT amount FROM tour_payments WHERE id = ?", (payment_id,)).fetchone()[0], 1500)

        response = self.post(
            f"/tour/{tour_id}/payments/{payment_id}/delete",
            follow_redirects=True,
        )
        self.assertIn(b"Super admin access is required", response.data)
        with tourcost.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tour_payments WHERE id = ?", (payment_id,)).fetchone()[0], 1)

        self.post("/logout")
        self.login("superadmin", "SuperAdmin@123")
        response = self.post(
            f"/tour/{tour_id}/payments/{payment_id}/delete",
            follow_redirects=True,
        )
        self.assertIn(b"Payment deleted", response.data)
        with tourcost.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tour_payments WHERE id = ?", (payment_id,)).fetchone()[0], 0)

    def test_expired_form_recovers_safely_without_changing_data(self):
        response = self.client.post(
            "/tour/new",
            data={
                "title": "Blocked", "traveller_names": ["Blocked User"], "destination": "Nowhere",
                "start_date": "2026-09-01", "end_date": "2026-09-02",
                "travelers": "1", "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        with tourcost.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tours").fetchone()[0], 0)

    def test_admin_can_open_users_and_create_standard_user_only(self):
        self.add_user("manager", role="admin")
        self.post("/logout")
        self.login("manager", "password123")
        response = self.client.get("/users")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Create an account", response.data)

        response = self.post(
            "/users",
            data={
                "username": "new_standard_user", "full_name": "New Standard User",
                "password": "password123", "role": "user",
            },
            follow_redirects=True,
        )
        self.assertIn(b"User account created successfully", response.data)
        response = self.post(
            "/users",
            data={
                "username": "forbidden_admin", "full_name": "Forbidden Admin",
                "password": "password123", "role": "admin",
            },
            follow_redirects=True,
        )
        self.assertIn(b"do not have permission", response.data)
        with tourcost.get_db() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM users WHERE username = 'forbidden_admin'").fetchone())

    def test_budget_target_dashboard_filter_and_tour_export(self):
        start_date = (date.today() + timedelta(days=1)).isoformat()
        end_date = (date.today() + timedelta(days=3)).isoformat()
        response = self.post(
            "/tour/new",
            data={
                "title": "Budgeted escape", "traveller_names": ["Karim Hasan", "Mina Hasan", "Rafi Hasan", "Sara Hasan"], "destination": "Bandarban",
                "start_date": start_date, "end_date": end_date,
                "travelers": "4", "budget_limit": "1000", "notes": "",
            },
        )
        tour_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
        self.post(
            f"/tour/{tour_id}/expenses/add",
            data={"category": "Transport", "amount": "750", "expense_date": start_date, "notes": "Bus"},
        )

        detail = self.client.get(f"/tour/{tour_id}")
        self.assertIn(b"Budget remaining", detail.data)
        self.assertIn(b"250.00", detail.data)
        dashboard = self.client.get("/?status=upcoming&sort=cost_high")
        self.assertIn(b"Budgeted escape", dashboard.data)
        self.assertIn(b"75%", dashboard.data)

        export = self.client.get(f"/tour/{tour_id}/expenses/export.csv")
        self.assertEqual(export.status_code, 200)
        self.assertIn(b"Transport", export.data)
        self.assertIn(b"750.0", export.data)

    def test_selected_tour_has_complete_a4_print_report(self):
        tour_id = self.create_tour()
        self.post(
            f"/tour/{tour_id}/expenses/add",
            data={
                "category": "Hotel", "amount": "2400",
                "expense_date": "2026-09-02", "notes": "Two rooms",
            },
        )

        detail = self.client.get(f"/tour/{tour_id}")
        self.assertIn(f'/tour/{tour_id}/print'.encode(), detail.data)
        report = self.client.get(f"/tour/{tour_id}/print")
        self.assertEqual(report.status_code, 200)
        self.assertIn(b"Complete tour record", report.data)
        self.assertIn(b"Summer break", report.data)
        self.assertIn(b"Ayesha Rahman", report.data)
        self.assertIn(b"Sylhet", report.data)
        self.assertIn(b"Two rooms", report.data)
        self.assertIn(b"2,400.00", report.data)
        print_css = self.client.get("/static/print.css")
        self.assertEqual(print_css.status_code, 200)
        self.assertIn(b"size: A4 portrait", print_css.data)
        print_css.close()


if __name__ == "__main__":
    unittest.main()
