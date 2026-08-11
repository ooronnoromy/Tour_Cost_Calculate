from app import init_db, get_db, sync_tour_total
from datetime import datetime

init_db()
now = datetime.now().isoformat(timespec="seconds")
with get_db() as conn:
    super_admin = conn.execute("SELECT username FROM users WHERE role = 'super_admin'").fetchone()
    if super_admin:
        print(f"Super admin ready: {super_admin['username']} / SuperAdmin@123")
    count = conn.execute("SELECT COUNT(*) FROM tours").fetchone()[0]
    if count == 0:
        cursor = conn.execute(
            """INSERT INTO tours (
                title, destination, start_date, end_date, travelers, notes, total_cost, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "Cox's Bazar Group Tour",
                "Cox's Bazar, Bangladesh",
                "2026-09-10",
                "2026-09-13",
                4,
                "Sample data. Add, edit, or delete expenses from the tour page.",
                0,
                now,
                now,
            ),
        )
        tour_id = cursor.lastrowid
        expenses = [
            ("transport", 12000, "2026-09-10", "Bus tickets"),
            ("hotel", 13500, "2026-09-10", "Hotel deposit"),
            ("food", 4800, "2026-09-11", "Lunch and dinner"),
            ("activities", 3000, "2026-09-12", "Beach activities"),
            ("shopping", 5000, "2026-09-12", "Local shopping"),
            ("other", 2000, "2026-09-13", "Miscellaneous"),
        ]
        for category, amount, expense_date, note in expenses:
            conn.execute(
                """INSERT INTO expenses (tour_id, category, amount, expense_date, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (tour_id, category, amount, expense_date, note, now, now),
            )
        sync_tour_total(conn, tour_id)
        print("Sample tour and expenses added.")
    else:
        print("Database already has tours. No sample added.")
