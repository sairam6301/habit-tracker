# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, send_from_directory, redirect, session, url_for
import sqlite3
import os
import csv
import io
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import calendar

# ── APScheduler ───────────────────────────────────────────────
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False

# ── Authlib (Google OAuth) ────────────────────────────────────
try:
    from authlib.integrations.flask_client import OAuth
    OAUTH_AVAILABLE = True
except ImportError:
    OAUTH_AVAILABLE = False

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR  = os.path.join(BASE_DIR, 'static')
DB_PATH     = os.path.join(BASE_DIR, 'habits.db')

app = Flask(__name__, static_folder=STATIC_DIR)
app.secret_key = os.environ.get('HABITFLOW_SECRET_KEY', 'habittracker-dev-fallback-key-change-in-production')

# ── Google OAuth setup ────────────────────────────────────────
oauth = None
google = None
if OAUTH_AVAILABLE:
    oauth = OAuth(app)
    google = oauth.register(
        name='google',
        client_id=os.environ.get('GOOGLE_CLIENT_ID'),
        client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

# ── CORS ──────────────────────────────────────────────────────
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
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password      TEXT,
            google_id     TEXT,
            avatar_url    TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS habits (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            name       TEXT NOT NULL,
            emoji      TEXT DEFAULT '✅',
            goal       INTEGER DEFAULT 30,
            color      TEXT DEFAULT '#4CAF50',
            category   TEXT DEFAULT 'General',
            difficulty TEXT DEFAULT 'Medium',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS completions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id       INTEGER NOT NULL,
            user_id        INTEGER NOT NULL,
            completed_date TEXT NOT NULL,
            UNIQUE(habit_id, completed_date),
            FOREIGN KEY (habit_id) REFERENCES habits(id)
        );
    ''')
    # Safe column migrations for upgrades
    for col, definition in [
        ('category',   "TEXT DEFAULT 'General'"),
        ('difficulty', "TEXT DEFAULT 'Medium'"),
        ('google_id',  'TEXT'),
        ('avatar_url', 'TEXT'),
    ]:
        try:
            conn.execute(f'ALTER TABLE habits ADD COLUMN {col} {definition}')
            conn.commit()
        except: pass
        try:
            conn.execute(f'ALTER TABLE users ADD COLUMN {col} {definition}')
            conn.commit()
        except: pass
    # password can now be NULL for Google-only users
    conn.close()

# ── Streak helpers ────────────────────────────────────────────
def calc_streak(habit_id, conn):
    rows = conn.execute(
        "SELECT completed_date FROM completions WHERE habit_id=? ORDER BY completed_date DESC",
        (habit_id,)
    ).fetchall()
    if not rows:
        return 0
    dates = set(r['completed_date'] for r in rows)
    streak = 0
    check = date.today()
    while check.strftime('%Y-%m-%d') in dates:
        streak += 1
        check -= timedelta(days=1)
    return streak

def calc_best_streak(habit_id, conn):
    rows = conn.execute(
        "SELECT completed_date FROM completions WHERE habit_id=? ORDER BY completed_date ASC",
        (habit_id,)
    ).fetchall()
    if not rows:
        return 0
    dates = sorted([datetime.strptime(r['completed_date'], '%Y-%m-%d').date() for r in rows])
    best = current = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i-1]).days == 1:
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best

def user_dict(user):
    return {'id': user['id'], 'username': user['username'],
            'email': user['email'], 'avatar_url': user['avatar_url'] or ''}

# ══════════════════════════════════════════════════════════════
#  FEATURE 1 — Google OAuth Routes
# ══════════════════════════════════════════════════════════════
@app.route('/auth/google')
def google_login():
    import sys
    print(f'[DEBUG] Python: {sys.executable}')
    print(f'[DEBUG] OAUTH_AVAILABLE: {OAUTH_AVAILABLE}')
    print(f'[DEBUG] google object: {google}')
    print(f'[DEBUG] GOOGLE_CLIENT_ID: {os.environ.get("GOOGLE_CLIENT_ID", "NOT SET")}')
    if not OAUTH_AVAILABLE or not google:
        return jsonify({'error': 'Google OAuth not configured. Install authlib and set GOOGLE_CLIENT_ID/SECRET.'}), 501
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/google/callback')
def google_callback():
    if not OAUTH_AVAILABLE or not google:
        return redirect('/?error=oauth_unavailable')
    try:
        token = google.authorize_access_token()
        userinfo = token.get('userinfo') or google.userinfo()
        google_id  = userinfo.get('sub')
        email      = userinfo.get('email', '').lower().strip()
        name       = userinfo.get('name', email.split('@')[0])
        avatar_url = userinfo.get('picture', '')

        if not email:
            return redirect('/?error=no_email')

        conn = get_db()
        # Check if user exists by google_id or email
        user = conn.execute('SELECT * FROM users WHERE google_id=? OR email=?',
                            (google_id, email)).fetchone()
        if user:
            # Update google_id and avatar if missing
            conn.execute('UPDATE users SET google_id=?, avatar_url=? WHERE id=?',
                         (google_id, avatar_url, user['id']))
            conn.commit()
        else:
            # Create new user (no password for Google users)
            username = name.replace(' ', '_').lower()
            # Ensure unique username
            base = username
            i = 1
            while conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone():
                username = f'{base}{i}'; i += 1
            conn.execute('INSERT INTO users (username, email, password, google_id, avatar_url) VALUES (?,?,NULL,?,?)',
                         (username, email, google_id, avatar_url))
            conn.commit()
            user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()

        conn.close()
        # Store user in session then redirect frontend with user data
        session['user_id'] = user['id']
        u = dict(user)
        import json, base64
        payload = base64.urlsafe_b64encode(
            json.dumps({'id': u['id'], 'username': u['username'],
                        'email': u['email'], 'avatar_url': avatar_url}).encode()
        ).decode()
        return redirect(f'/?google_auth={payload}')
    except Exception as e:
        print(f'Google OAuth error: {e}')
        return redirect(f'/?error=oauth_failed')

# ══════════════════════════════════════════════════════════════
#  EXISTING Auth Routes (unchanged)
# ══════════════════════════════════════════════════════════════
@app.route('/api/signup', methods=['POST'])
def signup():
    try:
        d = request.get_json(force=True, silent=True) or {}
        username = d.get('username', '').strip()
        email    = d.get('email', '').strip().lower()
        password = d.get('password', '')
        if not all([username, email, password]):
            return jsonify({'error': 'All fields are required'}), 400
        conn = get_db()
        try:
            conn.execute('INSERT INTO users (username,email,password) VALUES (?,?,?)',
                         (username, email, generate_password_hash(password)))
            conn.commit()
            user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
            return jsonify({'success': True, 'user': user_dict(user)})
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
        email    = d.get('email', '').strip().lower()
        password = d.get('password', '')
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        conn.close()
        if user and user['password'] and check_password_hash(user['password'], password):
            return jsonify({'success': True, 'user': user_dict(user)})
        if user and not user['password']:
            return jsonify({'error': 'This account uses Google Sign-In. Please use the Google button.'}), 401
        return jsonify({'error': 'Invalid email or password'}), 401
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  EXISTING Habits Routes (unchanged)
# ══════════════════════════════════════════════════════════════
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
            hd['streak']      = calc_streak(h['id'], conn)
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
        hd = dict(habit); hd['streak'] = 0; hd['best_streak'] = 0
        conn.close()
        return jsonify(hd)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/habits/<int:habit_id>', methods=['PUT'])
def update_habit(habit_id):
    try:
        d = request.get_json(force=True, silent=True) or {}
        conn = get_db()
        conn.execute('UPDATE habits SET name=?,emoji=?,color=?,category=?,difficulty=? WHERE id=?',
                     (d.get('name'), d.get('emoji','✅'), d.get('color','#4CAF50'),
                      d.get('category','General'), d.get('difficulty','Medium'), habit_id))
        conn.commit()
        habit = conn.execute('SELECT * FROM habits WHERE id=?', (habit_id,)).fetchone()
        hd = dict(habit)
        hd['streak']      = calc_streak(habit_id, conn)
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
        conn.commit(); conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ══════════════════════════════════════════════════════════════
#  EXISTING Completions Routes (unchanged)
# ══════════════════════════════════════════════════════════════
@app.route('/api/completions', methods=['GET'])
def get_completions():
    try:
        user_id = request.args.get('user_id')
        year    = request.args.get('year',  str(datetime.now().year))
        month   = request.args.get('month', str(datetime.now().month))
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

# ══════════════════════════════════════════════════════════════
#  EXISTING Analytics Route (unchanged)
# ══════════════════════════════════════════════════════════════
@app.route('/api/analytics', methods=['GET'])
def get_analytics():
    try:
        user_id = request.args.get('user_id')
        year    = int(request.args.get('year',  datetime.now().year))
        month   = int(request.args.get('month', datetime.now().month))
        conn = get_db()
        habits       = conn.execute('SELECT * FROM habits WHERE user_id=?', (user_id,)).fetchall()
        num_habits   = len(habits)
        days_in_month = calendar.monthrange(year, month)[1]
        today        = date.today()
        days_passed  = min(today.day, days_in_month) if (today.year==year and today.month==month) else days_in_month

        completions = conn.execute(
            'SELECT * FROM completions WHERE user_id=? AND strftime("%Y-%m", completed_date)=?',
            (user_id, f'{year}-{month:02d}')
        ).fetchall()

        total_possible = num_habits * days_passed
        total_completed = len(completions)
        monthly_pct = round((total_completed/total_possible*100) if total_possible>0 else 0, 1)

        daily_data = {}
        for c in completions:
            k = c['completed_date']
            daily_data[k] = daily_data.get(k,0) + 1

        daily_labels, daily_values = [], []
        for d in range(1, days_in_month+1):
            ds = f'{year}-{month:02d}-{d:02d}'
            daily_labels.append(str(d))
            daily_values.append(round((daily_data.get(ds,0)/num_habits*100) if num_habits>0 else 0, 1))

        weeks, week_labels = [], []
        for wn, ws in enumerate(range(1, days_in_month+1, 7), 1):
            we = min(ws+6, days_in_month)
            wt=wp=0
            for d in range(ws, we+1):
                ds = f'{year}-{month:02d}-{d:02d}'
                if ds <= str(today):
                    wt += daily_data.get(ds,0); wp += num_habits
            weeks.append(round((wt/wp*100) if wp>0 else 0, 1))
            week_labels.append(f'Week {wn}')

        habit_progress = []
        for h in habits:
            cnt    = sum(1 for c in completions if c['habit_id']==h['id'])
            streak = calc_streak(h['id'], conn)
            habit_progress.append({
                'id':h['id'], 'name':h['name'], 'emoji':h['emoji'],
                'category':h['category'] or 'General',
                'difficulty':h['difficulty'] or 'Medium',
                'completed':cnt, 'goal':days_passed,
                'pct':round((cnt/days_passed*100) if days_passed>0 else 0, 1),
                'streak':streak, 'best_streak':calc_best_streak(h['id'],conn)
            })
        conn.close()
        return jsonify({
            'monthly_pct':monthly_pct, 'total_completed':total_completed,
            'total_possible':total_possible, 'daily_labels':daily_labels,
            'daily_values':daily_values, 'week_labels':week_labels,
            'weekly_values':weeks, 'habit_progress':habit_progress,
            'days_passed':days_passed, 'num_habits':num_habits
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ══════════════════════════════════════════════════════════════
#  EXISTING Export CSV Route (unchanged)
# ══════════════════════════════════════════════════════════════
@app.route('/api/export/csv', methods=['GET'])
def export_csv():
    try:
        user_id = request.args.get('user_id')
        conn = get_db()
        habits      = conn.execute('SELECT * FROM habits WHERE user_id=?', (user_id,)).fetchall()
        completions = conn.execute('SELECT * FROM completions WHERE user_id=? ORDER BY completed_date', (user_id,)).fetchall()
        conn.close()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Habit','Category','Difficulty','Date','Completed'])
        comp_set = set((c['habit_id'], c['completed_date']) for c in completions)
        today = date.today()
        start = date(today.year, today.month, 1)
        for h in habits:
            for i in range((today-start).days+1):
                ds = (start+timedelta(days=i)).strftime('%Y-%m-%d')
                writer.writerow([h['name'], h['category'] or 'General',
                                  h['difficulty'] or 'Medium', ds,
                                  1 if (h['id'],ds) in comp_set else 0])
        from flask import Response
        return Response(output.getvalue(), mimetype='text/csv',
                        headers={'Content-Disposition':'attachment; filename=habits_export.csv'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ══════════════════════════════════════════════════════════════
#  FEATURE 2 — Daily Email Progress (APScheduler + SMTP)
# ══════════════════════════════════════════════════════════════
def build_email_html(username, habits_done, total_habits, pct, best_streak, habit_rows):
    rows_html = ''.join(f'''
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb">{r["emoji"]} {r["name"]}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;text-align:center">
            {"✅" if r["done"] else "⬜"}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;text-align:center;color:{"#16a34a" if r["streak"]>0 else "#9ca3af"}">
            {"🔥"+str(r["streak"]) if r["streak"]>0 else "—"}</td>
        </tr>''' for r in habit_rows)

    color = '#16a34a' if pct >= 80 else '#d97706' if pct >= 50 else '#dc2626'
    return f'''
    <!DOCTYPE html><html><head><meta charset="UTF-8"></head>
    <body style="font-family:'Segoe UI',sans-serif;background:#f0fdf4;margin:0;padding:20px">
      <div style="max-width:560px;margin:0 auto;background:white;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.1)">
        <div style="background:linear-gradient(135deg,#166534,#22c55e);padding:28px 32px">
          <h1 style="color:white;margin:0;font-size:22px">🌿 HabitFlow Daily Report</h1>
          <p style="color:rgba(255,255,255,.8);margin:6px 0 0;font-size:14px">
            {datetime.now().strftime("%A, %B %d %Y")}</p>
        </div>
        <div style="padding:28px 32px">
          <p style="font-size:16px;color:#374151">Hey <strong>{username}</strong> 👋</p>
          <div style="display:flex;gap:16px;margin:20px 0;flex-wrap:wrap">
            <div style="flex:1;background:#f0fdf4;border:1px solid #86efac;border-radius:10px;padding:16px;min-width:120px;text-align:center">
              <div style="font-size:28px;font-weight:700;color:{color}">{pct}%</div>
              <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em">Completion</div>
            </div>
            <div style="flex:1;background:#f0fdf4;border:1px solid #86efac;border-radius:10px;padding:16px;min-width:120px;text-align:center">
              <div style="font-size:28px;font-weight:700;color:#166534">{habits_done}/{total_habits}</div>
              <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em">Done Today</div>
            </div>
            <div style="flex:1;background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:16px;min-width:120px;text-align:center">
              <div style="font-size:28px;font-weight:700;color:#d97706">🔥{best_streak}</div>
              <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em">Best Streak</div>
            </div>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>
              <tr style="background:#f9fafb">
                <th style="padding:8px 12px;text-align:left;color:#6b7280;font-weight:600">HABIT</th>
                <th style="padding:8px 12px;text-align:center;color:#6b7280;font-weight:600">TODAY</th>
                <th style="padding:8px 12px;text-align:center;color:#6b7280;font-weight:600">STREAK</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
          <p style="margin:24px 0 0;font-size:14px;color:#6b7280;text-align:center">
            {"🎉 Amazing work today! Keep the streak going!" if pct==100
             else "💪 Keep going — every habit counts!" if pct>=50
             else "🌱 Small steps lead to big results. You've got this!"}
          </p>
        </div>
        <div style="background:#f9fafb;padding:16px 32px;text-align:center">
          <p style="font-size:11px;color:#9ca3af;margin:0">HabitFlow · Daily Progress Report</p>
        </div>
      </div>
    </body></html>'''

def send_progress_email(to_email, username, habits_done, total_habits, pct, best_streak, habit_rows):
    smtp_host   = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
    smtp_port   = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user   = os.environ.get('SMTP_USER', '')
    smtp_pass   = os.environ.get('SMTP_PASS', '')
    from_email  = os.environ.get('FROM_EMAIL', smtp_user)

    if not smtp_user or not smtp_pass:
        print(f'[Email] Skipping {to_email} — SMTP_USER/SMTP_PASS not set')
        return

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'🌿 Your HabitFlow Daily Progress — {pct}% today'
    msg['From']    = f'HabitFlow <{from_email}>'
    msg['To']      = to_email

    # Plain text fallback
    plain = (f'Hey {username}!\n\n'
             f'Habits completed today: {habits_done} / {total_habits}\n'
             f'Completion rate: {pct}%\n'
             f'Best streak: {best_streak} days\n\n'
             f'Keep going! 🌿\n— HabitFlow')
    msg.attach(MIMEText(plain, 'plain'))
    msg.attach(MIMEText(build_email_html(username, habits_done, total_habits,
                                         pct, best_streak, habit_rows), 'html'))
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(from_email, to_email, msg.as_string())
        print(f'[Email] ✅ Sent to {to_email}')
    except Exception as e:
        print(f'[Email] ❌ Failed for {to_email}: {e}')

def daily_email_job():
    """Runs every day at 9 PM — sends progress email to all users."""
    print(f'[Scheduler] Running daily email job at {datetime.now()}')
    try:
        conn = get_db()
        users = conn.execute('SELECT id, username, email FROM users').fetchall()
        today_str = date.today().strftime('%Y-%m-%d')

        for user in users:
            uid = user['id']
            habits = conn.execute('SELECT * FROM habits WHERE user_id=?', (uid,)).fetchall()
            if not habits:
                continue

            completions_today = set(
                c['habit_id'] for c in conn.execute(
                    'SELECT habit_id FROM completions WHERE user_id=? AND completed_date=?',
                    (uid, today_str)
                ).fetchall()
            )
            habit_rows = []
            best_streak = 0
            for h in habits:
                s = calc_streak(h['id'], conn)
                best_streak = max(best_streak, s)
                habit_rows.append({
                    'emoji': h['emoji'], 'name': h['name'],
                    'done':  h['id'] in completions_today,
                    'streak': s
                })
            habits_done = len(completions_today)
            total       = len(habits)
            pct         = round(habits_done / total * 100) if total > 0 else 0

            send_progress_email(
                user['email'], user['username'],
                habits_done, total, pct, best_streak, habit_rows
            )
        conn.close()
    except Exception as e:
        print(f'[Scheduler] Error in daily_email_job: {e}')

# ── Test Email Route ──────────────────────────────────────────
@app.route('/api/test-email')
def test_email():
    try:
        daily_email_job()
        return jsonify({'success': True, 'message': 'Test email sent to all users!'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ══════════════════════════════════════════════════════════════
#  FEATURE: Delete Account
# ══════════════════════════════════════════════════════════════
@app.route('/api/delete-account', methods=['DELETE'])
def delete_account():
    try:
        d = request.get_json(force=True, silent=True) or {}
        user_id = d.get('user_id')
        if not user_id:
            return jsonify({'error': 'user_id required'}), 400
        conn = get_db()
        # Delete all completions and habits first, then user
        habit_ids = [h['id'] for h in conn.execute('SELECT id FROM habits WHERE user_id=?', (user_id,)).fetchall()]
        for hid in habit_ids:
            conn.execute('DELETE FROM completions WHERE habit_id=?', (hid,))
        conn.execute('DELETE FROM habits WHERE user_id=?', (user_id,))
        conn.execute('DELETE FROM users WHERE id=?', (user_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ══════════════════════════════════════════════════════════════
#  FEATURE: Calendar Data API
# ══════════════════════════════════════════════════════════════
@app.route('/api/calendar', methods=['GET'])
def get_calendar():
    try:
        user_id  = request.args.get('user_id')
        year     = int(request.args.get('year',  datetime.now().year))
        month    = int(request.args.get('month', datetime.now().month))
        habit_id = request.args.get('habit_id')  # optional — filter by habit

        conn = get_db()
        habits = conn.execute('SELECT * FROM habits WHERE user_id=?', (user_id,)).fetchall()

        # Get ALL completions for this month
        query = 'SELECT * FROM completions WHERE user_id=? AND strftime("%Y-%m", completed_date)=?'
        params = [user_id, f'{year}-{month:02d}']
        if habit_id:
            query += ' AND habit_id=?'
            params.append(habit_id)
        completions = conn.execute(query, params).fetchall()

        days_in_month = calendar.monthrange(year, month)[1]
        today = date.today()
        num_habits = len(habits)

        # Build day-by-day data
        calendar_days = []
        for d in range(1, days_in_month + 1):
            ds = f'{year}-{month:02d}-{d:02d}'
            day_date = date(year, month, d)
            done = sum(1 for c in completions if c['completed_date'] == ds)
            is_future = day_date > today
            is_today  = day_date == today
            pct = round(done / num_habits * 100) if num_habits > 0 and not is_future else 0
            calendar_days.append({
                'date': ds, 'day': d,
                'done': done, 'total': num_habits,
                'pct': pct,
                'is_future': is_future,
                'is_today': is_today,
                'weekday': day_date.weekday(),  # 0=Mon, 6=Sun
            })

        # Per-habit completion map for selected month
        habit_completion = {}
        for h in habits:
            done_dates = set(c['completed_date'] for c in completions if c['habit_id'] == h['id'])
            habit_completion[h['id']] = {
                'name': h['name'], 'emoji': h['emoji'],
                'color': h['color'],
                'done_dates': list(done_dates),
                'current_streak': calc_streak(h['id'], conn),
                'best_streak': calc_best_streak(h['id'], conn),
            }

        conn.close()
        return jsonify({
            'calendar_days': calendar_days,
            'habit_completion': habit_completion,
            'year': year, 'month': month,
            'days_in_month': days_in_month,
            'num_habits': num_habits,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Static ────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')

@app.route('/<path:filename>')
def static_files(filename):
    if filename.startswith('api/') or filename.startswith('auth/'):
        return jsonify({'error': 'Not found'}), 404
    try:
        return send_from_directory(STATIC_DIR, filename)
    except Exception:
        return send_from_directory(STATIC_DIR, 'index.html')

# ── Always init DB (works with both gunicorn and direct run) ──
init_db()

# ── Start scheduler (works with both gunicorn and direct run) ──
if SCHEDULER_AVAILABLE:
    scheduler = BackgroundScheduler(timezone='Asia/Kolkata')
    scheduler.add_job(daily_email_job, 'cron', hour=21, minute=0)
    scheduler.start()
    import atexit
    atexit.register(lambda: scheduler.shutdown())

# ── Startup ───────────────────────────────────────────────────
if __name__ == '__main__':
    print("\n" + "="*55)
    print("  HabitFlow — Habit Tracker")
    print("="*55)
    print(f"  Folder     : {BASE_DIR}")
    print(f"  Static     : {STATIC_DIR}")
    print(f"  DB         : {DB_PATH}")
    print(f"  OAuth      : {'✅ Authlib ready' if OAUTH_AVAILABLE else '⚠ Install authlib'}")
    print(f"  Scheduler  : {'✅ APScheduler ready' if SCHEDULER_AVAILABLE else '⚠ Install apscheduler'}")
    print(f"  Google ID  : {'✅ Set' if os.environ.get('GOOGLE_CLIENT_ID') else '⚠ Not set'}")
    print(f"  SMTP       : {'✅ Set' if os.environ.get('SMTP_USER') else '⚠ Not set (emails disabled)'}")

    init_db()
    print("  ✅ Database ready.")
    print("  ✅ Scheduler started — emails at 9 PM IST daily.")
    print("\n  Open browser: http://localhost:5000")
    print("="*55 + "\n")
    app.run(debug=True, port=5000, host='0.0.0.0', use_reloader=False)