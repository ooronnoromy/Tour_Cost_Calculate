import tempfile
import unittest
from pathlib import Path

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
                "title": "Summer break", "destination": "Sylhet",
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
            expense_id = conn.execute("SELECT id FROM personal_expenses").fetchone()[0]

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

    def test_expired_form_recovers_safely_without_changing_data(self):
        response = self.client.post(
            "/tour/new",
            data={
                "title": "Blocked", "destination": "Nowhere",
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


if __name__ == "__main__":
    unittest.main()
