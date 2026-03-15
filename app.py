from flask import Flask, request, jsonify, send_from_directory
import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash
import csv
import io
from datetime import datetime, date, timedelta
import calendar

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')
DB_PATH = os.path.join(BASE_DIR, 'habits.db')

app = Flask(__name__, static_folder=STATIC_DIR)
app.secret_key = os.environ.get('HABITFLOW_SECRET_KEY', 'habittracker-dev-fallback-key-change-in-production')

@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'
    return response

@app.route('/api/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    return jsonify({}), 200

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Endpoint not found'}), 404
    return send_from_directory(STATIC_DIR, 'index.html')

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': str(e)}), 500

# ── Database ──────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            emoji TEXT DEFAULT '✅',
            goal INTEGER DEFAULT 30,
            color TEXT DEFAULT '#4CAF50',
            category TEXT DEFAULT 'General',
            difficulty TEXT DEFAULT 'Medium',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            completed_date TEXT NOT NULL,
            UNIQUE(habit_id, completed_date),
            FOREIGN KEY (habit_id) REFERENCES habits(id)
        );
    ''')
    # Add new columns if upgrading from old DB
    try:
        conn.execute("ALTER TABLE habits ADD COLUMN category TEXT DEFAULT 'General'")
        conn.commit()
    except: pass
    try:
        conn.execute("ALTER TABLE habits ADD COLUMN difficulty TEXT DEFAULT 'Medium'")
        conn.commit()
    except: pass
    conn.close()

def calc_streak(habit_id, conn):
    """Calculate current streak for a habit."""
    rows = conn.execute(
        "SELECT completed_date FROM completions WHERE habit_id=? ORDER BY completed_date DESC",
        (habit_id,)
    ).fetchall()
    if not rows:
        return 0
    dates = [datetime.strptime(r['completed_date'], '%Y-%m-%d').date() for r in rows]
    streak = 0
    check = date.today()
    for d in dates:
        if d == check:
            streak += 1
            check -= timedelta(days=1)
        elif d == check + timedelta(days=1):
            # allow today not yet completed, start from yesterday
            check = d
            streak += 1
            check -= timedelta(days=1)
        else:
            break
    return streak

def calc_best_streak(habit_id, conn):
    """Calculate all-time best streak."""
    rows = conn.execute(
        "SELECT completed_date FROM completions WHERE habit_id=? ORDER BY completed_date ASC",
        (habit_id,)
    ).fetchall()
    if not rows:
        return 0
    dates = sorted([datetime.strptime(r['completed_date'], '%Y-%m-%d').date() for r in rows])
    best = 1
    current = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i-1]).days == 1:
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best

# ── Auth ──────────────────────────────────────────────────────
@app.route('/api/signup', methods=['POST'])
def signup():
    try:
        d = request.get_json(force=True, silent=True) or {}
        username = d.get('username', '').strip()
        email = d.get('email', '').strip()
        password = d.get('password', '')
        if not all([username, email, password]):
            return jsonify({'error': 'All fields are required'}), 400
        conn = get_db()
        try:
            conn.execute('INSERT INTO users (username,email,password) VALUES (?,?,?)',
                         (username, email, generate_password_hash(password)))
            conn.commit()
            user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
            return jsonify({'success': True, 'user': {'id': user['id'], 'username': user['username'], 'email': user['email']}})
        except sqlite3.IntegrityError:
            return jsonify({'error': 'Username or email already exists'}), 409
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        d = request.get_json(force=True, silent=True) or {}
        email = d.get('email', '').strip()
        password = d.get('password', '')
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            return jsonify({'success': True, 'user': {'id': user['id'], 'username': user['username'], 'email': user['email']}})
        return jsonify({'error': 'Invalid email or password'}), 401
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Habits ────────────────────────────────────────────────────
@app.route('/api/habits', methods=['GET'])
def get_habits():
    try:
        user_id = request.args.get('user_id')
        if not user_id:
            return jsonify({'error': 'user_id required'}), 400
        conn = get_db()
        habits = conn.execute('SELECT * FROM habits WHERE user_id=? ORDER BY id', (user_id,)).fetchall()
        result = []
        for h in habits:
            hd = dict(h)
            hd['streak'] = calc_streak(h['id'], conn)
            hd['best_streak'] = calc_best_streak(h['id'], conn)
            result.append(hd)
        conn.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/habits', methods=['POST'])
def create_habit():
    try:
        d = request.get_json(force=True, silent=True) or {}
        if not d.get('user_id') or not d.get('name'):
            return jsonify({'error': 'user_id and name required'}), 400
        conn = get_db()
        c = conn.cursor()
        c.execute('INSERT INTO habits (user_id,name,emoji,goal,color,category,difficulty) VALUES (?,?,?,?,?,?,?)',
                  (d['user_id'], d['name'].strip(), d.get('emoji','✅'),
                   d.get('goal',30), d.get('color','#4CAF50'),
                   d.get('category','General'), d.get('difficulty','Medium')))
        conn.commit()
        habit = conn.execute('SELECT * FROM habits WHERE id=?', (c.lastrowid,)).fetchone()
        hd = dict(habit)
        hd['streak'] = 0
        hd['best_streak'] = 0
        conn.close()
        return jsonify(hd)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/habits/<int:habit_id>', methods=['PUT'])
def update_habit(habit_id):
    try:
        d = request.get_json(force=True, silent=True) or {}
        conn = get_db()
        conn.execute('''UPDATE habits SET name=?, emoji=?, color=?, category=?, difficulty=?
                        WHERE id=?''',
                     (d.get('name'), d.get('emoji','✅'), d.get('color','#4CAF50'),
                      d.get('category','General'), d.get('difficulty','Medium'), habit_id))
        conn.commit()
        habit = conn.execute('SELECT * FROM habits WHERE id=?', (habit_id,)).fetchone()
        hd = dict(habit)
        hd['streak'] = calc_streak(habit_id, conn)
        hd['best_streak'] = calc_best_streak(habit_id, conn)
        conn.close()
        return jsonify(hd)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/habits/<int:habit_id>', methods=['DELETE'])
def delete_habit(habit_id):
    try:
        conn = get_db()
        conn.execute('DELETE FROM completions WHERE habit_id=?', (habit_id,))
        conn.execute('DELETE FROM habits WHERE id=?', (habit_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Completions ───────────────────────────────────────────────
@app.route('/api/completions', methods=['GET'])
def get_completions():
    try:
        user_id = request.args.get('user_id')
        year = request.args.get('year', str(datetime.now().year))
        month = request.args.get('month', str(datetime.now().month))
        conn = get_db()
        rows = conn.execute(
            'SELECT * FROM completions WHERE user_id=? AND strftime("%Y-%m", completed_date)=?',
            (user_id, f'{int(year)}-{int(month):02d}')
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/completions/toggle', methods=['POST'])
def toggle_completion():
    try:
        d = request.get_json(force=True, silent=True) or {}
        conn = get_db()
        existing = conn.execute('SELECT id FROM completions WHERE habit_id=? AND completed_date=?',
                                (d['habit_id'], d['date'])).fetchone()
        if existing:
            conn.execute('DELETE FROM completions WHERE id=?', (existing['id'],))
            status = False
        else:
            conn.execute('INSERT INTO completions (habit_id,user_id,completed_date) VALUES (?,?,?)',
                         (d['habit_id'], d['user_id'], d['date']))
            status = True
        conn.commit()
        streak = calc_streak(d['habit_id'], conn)
        conn.close()
        return jsonify({'completed': status, 'streak': streak})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Analytics ─────────────────────────────────────────────────
@app.route('/api/analytics', methods=['GET'])
def get_analytics():
    try:
        user_id = request.args.get('user_id')
        year = int(request.args.get('year', datetime.now().year))
        month = int(request.args.get('month', datetime.now().month))
        conn = get_db()
        habits = conn.execute('SELECT * FROM habits WHERE user_id=?', (user_id,)).fetchall()
        num_habits = len(habits)
        days_in_month = calendar.monthrange(year, month)[1]
        today = date.today()
        days_passed = min(today.day, days_in_month) if (today.year == year and today.month == month) else days_in_month

        completions = conn.execute(
            'SELECT * FROM completions WHERE user_id=? AND strftime("%Y-%m", completed_date)=?',
            (user_id, f'{year}-{month:02d}')
        ).fetchall()

        total_possible = num_habits * days_passed
        total_completed = len(completions)
        monthly_pct = round((total_completed / total_possible * 100) if total_possible > 0 else 0, 1)

        daily_data = {}
        for c in completions:
            k = c['completed_date']
            daily_data[k] = daily_data.get(k, 0) + 1

        daily_labels, daily_values = [], []
        for d in range(1, days_in_month + 1):
            date_str = f'{year}-{month:02d}-{d:02d}'
            cnt = daily_data.get(date_str, 0)
            daily_labels.append(str(d))
            daily_values.append(round((cnt / num_habits * 100) if num_habits > 0 else 0, 1))

        weeks, week_labels = [], []
        for wn, week_start in enumerate(range(1, days_in_month + 1, 7), 1):
            week_end = min(week_start + 6, days_in_month)
            wt, wp = 0, 0
            for d in range(week_start, week_end + 1):
                date_str = f'{year}-{month:02d}-{d:02d}'
                if date_str <= str(today):
                    wt += daily_data.get(date_str, 0)
                    wp += num_habits
            weeks.append(round((wt / wp * 100) if wp > 0 else 0, 1))
            week_labels.append(f'Week {wn}')

        habit_progress = []
        for h in habits:
            cnt = sum(1 for c in completions if c['habit_id'] == h['id'])
            streak = calc_streak(h['id'], conn)
            best_streak = calc_best_streak(h['id'], conn)
            habit_progress.append({
                'id': h['id'], 'name': h['name'], 'emoji': h['emoji'],
                'category': h['category'] if h['category'] else 'General',
                'difficulty': h['difficulty'] if h['difficulty'] else 'Medium',
                'completed': cnt, 'goal': days_passed,
                'pct': round((cnt / days_passed * 100) if days_passed > 0 else 0, 1),
                'streak': streak, 'best_streak': best_streak
            })

        conn.close()
        return jsonify({
            'monthly_pct': monthly_pct, 'total_completed': total_completed,
            'total_possible': total_possible, 'daily_labels': daily_labels,
            'daily_values': daily_values, 'week_labels': week_labels,
            'weekly_values': weeks, 'habit_progress': habit_progress,
            'days_passed': days_passed, 'num_habits': num_habits
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Export CSV ────────────────────────────────────────────────
@app.route('/api/export/csv', methods=['GET'])
def export_csv():
    try:
        user_id = request.args.get('user_id')
        conn = get_db()
        habits = conn.execute('SELECT * FROM habits WHERE user_id=?', (user_id,)).fetchall()
        completions = conn.execute(
            'SELECT * FROM completions WHERE user_id=? ORDER BY completed_date',
            (user_id,)
        ).fetchall()
        conn.close()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Habit', 'Category', 'Difficulty', 'Date', 'Completed'])

        comp_set = set((c['habit_id'], c['completed_date']) for c in completions)
        today = date.today()
        start = date(today.year, today.month, 1)

        for h in habits:
            for i in range((today - start).days + 1):
                d = start + timedelta(days=i)
                date_str = d.strftime('%Y-%m-%d')
                done = 1 if (h['id'], date_str) in comp_set else 0
                writer.writerow([h['name'], h['category'] or 'General',
                                  h['difficulty'] or 'Medium', date_str, done])

        from flask import Response
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=habits_export.csv'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Static ────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')

@app.route('/<path:filename>')
def static_files(filename):
    if filename.startswith('api/'):
        return jsonify({'error': 'Not found'}), 404
    try:
        return send_from_directory(STATIC_DIR, filename)
    except Exception:
        return send_from_directory(STATIC_DIR, 'index.html')

if __name__ == '__main__':
    print("\n" + "="*50)
    print("  HabitFlow - Habit Tracker")
    print("="*50)
    print(f"  Folder  : {BASE_DIR}")
    print(f"  Static  : {STATIC_DIR}")
    print(f"  DB      : {DB_PATH}")
    print(f"  HTML    : {os.path.exists(os.path.join(STATIC_DIR, 'index.html'))}")
    init_db()
    print("  ✅ Database ready.")
    print("\n  Open browser at: http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=True, port=5000, host='0.0.0.0')