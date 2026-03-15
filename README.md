# 🌿 HabitFlow — Habit Tracker Web App

A beautiful, full-featured habit tracker built with **Python Flask + SQLite + Chart.js**.

## Features
- ✅ User Authentication (Sign Up / Login)
- 📋 Monthly habit grid with daily checkboxes
- 📊 Line chart (daily performance), Bar chart (weekly), Pie/Donut (monthly overview)
- 📈 KPI cards: Monthly %, Total Habits, Days Tracked, Best Habit
- 🏆 Habit progress bars + Top 10 leaderboard
- ➕ Add/Delete habits with emoji picker
- 🗓 Month navigation (browse past months)
- 💾 SQLite database (zero configuration)

## Setup & Run

### 1. Install Python dependencies
```bash
pip install flask flask-cors
# or
pip install -r requirements.txt
```

### 2. Start the server
```bash
python app.py
```

### 3. Open your browser
```
https://habit-flow-gn7b.onrender.com/
```

That's it! The database (`habits.db`) is created automatically on first run.

## Project Structure
```
habit-tracker/
├── app.py              ← Flask backend (API + routes)
├── requirements.txt    ← Python dependencies
├── habits.db           ← SQLite database (auto-created)
└── static/
    └── index.html      ← Full frontend (HTML + CSS + JS)
```

## API Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /api/signup | Create new account |
| POST | /api/login | Login |
| GET | /api/habits | List habits |
| POST | /api/habits | Create habit |
| DELETE | /api/habits/:id | Delete habit |
| GET | /api/completions | Get completions for month |
| POST | /api/completions/toggle | Toggle habit completion |
| GET | /api/analytics | Get analytics & charts data |

## Tech Stack
- **Frontend**: HTML5, CSS3, JavaScript (Vanilla)
- **Charts**: Chart.js 4
- **Backend**: Python Flask
- **Database**: SQLite3
- **Fonts**: Syne + DM Sans (Google Fonts)
