# TourCost Pro

An advanced Python web application for calculating and managing tour budgets.

## Features

- Role-based login with super admin, admin, and user access
- Super admin can create administrators and users; administrators can manage standard users
- Create, edit, view, search, and delete tour budgets
- Private personal-expense ledger for every account
- Filter personal spending by month, category, linked tour, or search text
- Export filtered personal expenses to CSV
- Track cash, card, mobile banking, bank transfer, and other payments
- Transport, hotel, food, activities, visa, shopping, and miscellaneous costs
- Tax/service charge percentage
- Emergency/contingency percentage
- Automatic grand total and per-person cost
- Live calculation while filling the form
- SQLite database persistence
- Dashboard summary
- Expense ownership controls for safe editing and deletion
- Printable budget page / browser "Save as PDF"
- Responsive Bootstrap interface
- Integration tests for privacy and expense permissions

## Run on Windows

```bash
cd tour_cost_calculator
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

For production, set a strong application secret before starting the app:

```powershell
$env:TOURCOST_SECRET_KEY = "replace-with-a-long-random-value"
python app.py
```

Run the automated checks with:

```bash
python -m unittest discover -s tests -v
```

## Default super admin

The app creates a super admin automatically on first launch.

- Username: `superadmin`
- Password: `SuperAdmin@123`

Use that account to create additional admin and user accounts from the Users page.

## Optional sample data

Run once before starting the app:

```bash
python seed.py
```

## Project Structure

```text
tour_cost_calculator/
├── app.py
├── seed.py
├── requirements.txt
├── README.md
├── static/
│   └── style.css
└── templates/
    ├── base.html
    ├── dashboard.html
    ├── login.html
    ├── users.html
    ├── tour_form.html
    └── tour_detail.html
```

## Production note

Before deploying publicly, set `TOURCOST_SECRET_KEY`, replace the default super admin password, leave debug mode disabled, back up `tour_costs.db`, and use a production WSGI server.
