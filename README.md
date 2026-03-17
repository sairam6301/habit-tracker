# 🌿 Habit Flow — Habit Tracker Web App

A beautiful, full-featured habit tracker built with **Python Flask + SQLite + Chart.js**.

## Project Structure
```
DAY TRACKER/
├── static/
│   ├── landing.html    ← Landing page (homepage)
│   └── index.html      ← Dashboard (after login)
├── venv/
├── app.py              ← Flask backend
├── habits.db           ← SQLite database (auto-created)
├── requirements.txt
└── README.md
```

## Features
- ✅ Landing page with Login/Signup modal popup
- 🌙 Dark / Light mode toggle
- 👥 Meet the Developers section
- 🔐 User Authentication (Email + Google OAuth)
- 📋 Monthly habit grid with daily checkboxes
- 📊 Charts: Line, Bar, Donut
- 📈 KPI cards, streak tracking, progress bars
- 🗓 Full year calendar heatmap
- 📧 Daily email reports (APScheduler)
- ⬇️ Export CSV + PDF
- 💾 SQLite database

## Setup & Run

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the server
```bash
python app.py
```

### 3. Open browser
```
http://localhost:5000
```

## Routes
| Route | File | Description |
|-------|------|-------------|
| `/` | landing.html | Homepage with login modal |
| `/dashboard` | index.html | Main habit tracker app |
| `/auth/google` | — | Google OAuth redirect |
| `/api/login` | — | Login endpoint |
| `/api/signup` | — | Signup endpoint |

## Tech Stack
- **Frontend**: HTML5, CSS3, Vanilla JS, Playfair Display + DM Sans
- **Charts**: Chart.js 4
- **Backend**: Python Flask
- **Database**: SQLite3
- **Auth**: Werkzeug password hashing + Google OAuth (Authlib)
- **Email**: APScheduler + SMTP
```

---

**Step 5 — Final folder should look like:**
```
DAY TRACKER/
├── static/
│   ├── landing.html   ✅ new - homepage
│   └── index.html     ✅ updated - dashboard
├── venv/
├── app.py             ✅ updated - added /dashboard route
├── habits.db
├── README.md          ✅ updated
└── requirements.txt   (no changes needed)