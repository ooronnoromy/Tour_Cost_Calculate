# TourCost Pro

An advanced Python web application for calculating and managing tour budgets.

## Features

- Role-based login with super admin, admin, and user access
- Super admin can create admin and user accounts
- Create, edit, view, search, and delete tour budgets
- Transport, hotel, food, activities, visa, shopping, and miscellaneous costs
- Tax/service charge percentage
- Emergency/contingency percentage
- Automatic grand total and per-person cost
- Live calculation while filling the form
- SQLite database persistence
- Dashboard summary
- Printable budget page / browser "Save as PDF"
- Responsive Bootstrap interface

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

Before deploying publicly, change `SECRET_KEY`, set a strong super admin password, disable Flask debug mode, and use a production WSGI server.
