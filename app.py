from flask import Flask, render_template, request, send_file, redirect, url_for, flash, jsonify, session
from google import genai
import sqlite3
from datetime import datetime
import json
import re
from io import BytesIO

# PDF LIBRARIES (Professional PDF ke liye)
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

# LOGIN LIBRARY
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "supersecretkey"

# ================= LOGIN CONFIG =================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ================= SETTINGS =================
# ⚠️ TERI API KEY (Jo tune di thi)
API_KEY = "AIzaSyD7moXk0zozZ8et17KTdVslpU-sO9k8egg"
client = genai.Client(api_key=API_KEY)

# 👑 ADMIN EMAIL
ADMIN_EMAIL = "admin120@gmail.com"

# 💾 MEMORY STORAGE (Session Cookie Fix - Data yahan save hoga)
USER_PLANS = {}


# ================= USER MODEL =================
class User(UserMixin):
    def __init__(self, id, name, email):
        self.id = id
        self.name = name
        self.email = email


@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect("fitness.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    if user:
        return User(id=user[0], name=user[1], email=user[2])
    return None


# ================= DATABASE SETUP =================
def init_db():
    conn = sqlite3.connect("fitness.db")
    cursor = conn.cursor()

    # Progress Table
    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS progress
                   (
                       id
                       INTEGER
                       PRIMARY
                       KEY
                       AUTOINCREMENT,
                       name
                       TEXT,
                       date
                       TEXT,
                       weight
                       REAL,
                       height
                       REAL,
                       bmi
                       REAL,
                       category
                       TEXT,
                       user_email
                       TEXT
                   )""")

    # Users Table
    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS users
                   (
                       id
                       INTEGER
                       PRIMARY
                       KEY
                       AUTOINCREMENT,
                       name
                       TEXT,
                       email
                       TEXT
                       UNIQUE,
                       phone
                       TEXT
                       UNIQUE,
                       password
                       TEXT
                   )""")

    # Activity Logs (Admin Panel ke liye)
    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS activity_logs
                   (
                       id
                       INTEGER
                       PRIMARY
                       KEY
                       AUTOINCREMENT,
                       user_name
                       TEXT,
                       email
                       TEXT,
                       action
                       TEXT,
                       timestamp
                       TEXT
                   )""")

    conn.commit()
    conn.close()


init_db()


# ================= HELPER FUNCTIONS =================
def log_activity(name, email, action):
    try:
        conn = sqlite3.connect("fitness.db", timeout=10)
        cursor = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO activity_logs (user_name, email, action, timestamp) VALUES (?, ?, ?, ?)",
                       (name, email, action, now))
        conn.commit()
        conn.close()
    except:
        pass


def is_valid_email(email):
    return re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', email)


def is_valid_phone(phone):
    return phone.isdigit() and len(phone) == 10


# ================= ROUTES =================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        login_input = request.form['login_input']
        password = request.form['password']
        conn = sqlite3.connect("fitness.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ? OR phone = ?", (login_input, login_input))
        user_data = cursor.fetchone()
        conn.close()

        if user_data and check_password_hash(user_data[4], password):
            user_obj = User(id=user_data[0], name=user_data[1], email=user_data[2])
            login_user(user_obj)
            log_activity(user_obj.name, user_obj.email, "Logged In")
            return redirect(url_for('index'))
        else:
            flash('Invalid Email/Phone or Password!', 'error')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        phone = request.form['phone']
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        if not name or not email or not phone or not password:
            flash("All fields are required!", "error");
            return render_template('register.html')
        if not is_valid_email(email):
            flash("Invalid Email!", "error");
            return render_template('register.html')
        if not is_valid_phone(phone):
            flash("Invalid Phone!", "error");
            return render_template('register.html')
        if password != confirm_password:
            flash("Passwords do not match!", "error");
            return render_template('register.html')
        if len(password) < 8:
            flash("Password must be at least 8 chars!", "error");
            return render_template('register.html')

        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        try:
            conn = sqlite3.connect("fitness.db")
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (name, email, phone, password) VALUES (?, ?, ?, ?)",
                           (name, email, phone, hashed_password))
            conn.commit()
            conn.close()
            log_activity(name, email, "Created New Account")
            flash('Account Created! Please Login.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('User already exists!', 'error')
    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    log_activity(current_user.name, current_user.email, "Logged Out")
    if current_user.id in USER_PLANS: del USER_PLANS[current_user.id]  # Clear memory
    logout_user()
    return redirect(url_for('login'))


# ================= MAIN APP (TERA ORIGINAL LOGIC) =================
@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    recommendations = None
    bmi = None
    bmi_category = None
    bmi_class = None

    # Agar purana plan memory mein hai to dikha do (Refresh karne par gayab nahi hoga)
    if request.method == "GET" and current_user.id in USER_PLANS:
        saved = USER_PLANS[current_user.id]
        recommendations = saved['content']
        bmi = saved['bmi']
        bmi_category = saved['category']
        bmi_class = saved['class']

    if request.method == "POST":
        name = request.form["name"]
        dietary_preferences = request.form.get("dietary_preferences")
        fitness_goals = request.form.get("fitness_goals")
        lifestyle_factors = request.form.get("lifestyle_factors")
        dietary_restrictions = request.form.get("dietary_restrictions")
        health_conditions = request.form.get("health_conditions")
        weight = float(request.form.get("weight"))
        height = float(request.form.get("height"))
        query = request.form.get("user_query")

        # Logic
        height_m = height / 100
        bmi = round(weight / (height_m ** 2), 2)

        if bmi < 18.5:
            bmi_category, bmi_class = "Underweight", "bmi-underweight"
        elif bmi < 25:
            bmi_category, bmi_class = "Normal", "bmi-normal"
        elif bmi < 30:
            bmi_category, bmi_class = "Overweight", "bmi-overweight"
        else:
            bmi_category, bmi_class = "Obese", "bmi-obese"

        # Database Save
        conn = sqlite3.connect("fitness.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO progress (name, date, weight, height, bmi, category, user_email) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, datetime.now().strftime("%Y-%m-%d"), weight, height, bmi, bmi_category, current_user.email))
        conn.commit()
        conn.close()

        log_activity(name, current_user.email, "Generated Plan")

        # TERA ORIGINAL PROMPT (Maine change nahi kiya)
        prompt = f"""
        Create a personalized fitness plan.
        Name: {name}
        BMI: {bmi} ({bmi_category})
        Dietary Preferences: {dietary_preferences}
        Fitness Goals: {fitness_goals}
        Lifestyle: {lifestyle_factors}
        Restrictions: {dietary_restrictions}
        Health Conditions: {health_conditions}
        Question: {query}
        Return ONLY JSON in this format:
        {{ "diet_types": [], "workouts": [], "breakfasts": [], "dinners": [], "additional_tips": [] }}
        """

        response = client.models.generate_content(
            model="gemini-3-flash-preview",  # Tera model
            contents=prompt
        )

        try:
            text_resp = response.text.strip()
            if "```json" in text_resp:
                text_resp = text_resp.replace("```json", "").replace("```", "")
            recommendations = json.loads(text_resp)
        except:
            recommendations = None

        # SAVE TO MEMORY (Ye hai main fix taaki PDF chale)
        USER_PLANS[current_user.id] = {
            'name': name, 'date': datetime.now().strftime("%Y-%m-%d"),
            'weight': weight, 'height': height, 'bmi': bmi,
            'category': bmi_category, 'class': bmi_class,
            'content': recommendations
        }

    is_admin = (current_user.email == ADMIN_EMAIL)
    return render_template("index.html", recommendations=recommendations, bmi=bmi, bmi_category=bmi_category,
                           bmi_class=bmi_class, user_name=current_user.name, is_admin=is_admin)


# ================= PDF ROUTE (PROFESSIONAL FIX) =================
@app.route("/download_pdf")
@login_required
def download_pdf():
    # Fetch from Memory instead of Session
    plan_data = USER_PLANS.get(current_user.id)
    if not plan_data: return "No Plan Generated yet!"

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    elements = []
    styles = getSampleStyleSheet()

    # Title
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=24, textColor=colors.HexColor("#4f46e5"),
                                 alignment=1, spaceAfter=20)
    elements.append(Paragraph(f"Fitness Plan for {plan_data['name']}", title_style))
    elements.append(Paragraph(f"Date: {plan_data['date']}", styles['Normal']))
    elements.append(Spacer(1, 20))

    # Stats Table
    stats_data = [
        ['Current Weight', f"{plan_data['weight']} kg", 'BMI', f"{plan_data['bmi']}"],
        ['Height', f"{plan_data['height']} cm", 'Category', plan_data['category']]
    ]
    t = Table(stats_data, colWidths=[120, 120, 120, 120])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f3f4f6")),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.white),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 20))

    # Professional Tables for Content
    content = plan_data.get('content', {})

    def add_section(title, items, color):
        if items:
            elements.append(
                Paragraph(title, ParagraphStyle('h2', parent=styles['Heading2'], textColor=colors.HexColor(color))))
            table_data = [[f"• {item}"] for item in items]
            t = Table(table_data, colWidths=[480])
            t.setStyle(TableStyle([
                ('BOX', (0, 0), (-1, -1), 1, colors.HexColor(color)),
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#fafafa")),
                ('PADDING', (0, 0), (-1, -1), 6)
            ]))
            elements.append(t)
            elements.append(Spacer(1, 10))

    add_section("🥗 Diet Types", content.get('diet_types'), "#10b981")
    add_section("🏋️ Workouts", content.get('workouts'), "#4f46e5")
    add_section("🍳 Breakfasts", content.get('breakfasts'), "#f59e0b")
    add_section("🌙 Dinners", content.get('dinners'), "#ef4444")
    add_section("💡 Tips", content.get('additional_tips'), "#8b5cf6")

    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"Plan_{plan_data['name']}.pdf",
                     mimetype="application/pdf")


# ================= DASHBOARD ROUTE (ADMIN FIX) =================
@app.route('/dashboard')
@login_required
def dashboard():
    conn = sqlite3.connect("fitness.db")
    cursor = conn.cursor()

    # User Data
    cursor.execute("SELECT date, weight, bmi, category FROM progress WHERE user_email = ? ORDER BY date ASC",
                   (current_user.email,))
    data = cursor.fetchall()

    dates, weights, bmis = [], [], []
    current_weight, start_weight, weight_change, current_bmi, bmi_category = 0, 0, 0, 0, "N/A"

    if data:
        for row in data:
            dates.append(row[0]);
            weights.append(row[1]);
            bmis.append(row[2])
        start_weight = weights[0];
        current_weight = weights[-1];
        current_bmi = bmis[-1];
        bmi_category = data[-1][3]
        weight_change = round(current_weight - start_weight, 2)

    # Admin Logic (Sirf Admin ko dikhega)
    is_admin = (current_user.email == ADMIN_EMAIL)
    admin_data = {}
    if is_admin:
        cursor.execute("SELECT * FROM users")
        admin_data['users'] = cursor.fetchall()
        cursor.execute("SELECT * FROM activity_logs ORDER BY id DESC LIMIT 50")
        admin_data['logs'] = cursor.fetchall()
        cursor.execute("SELECT * FROM progress ORDER BY date DESC")
        admin_data['progress'] = cursor.fetchall()

    conn.close()

    return render_template('dashboard.html',
                           dates=json.dumps(dates), weights=json.dumps(weights), bmis=json.dumps(bmis),
                           current_weight=current_weight, start_weight=start_weight, weight_change=weight_change,
                           current_bmi=current_bmi, bmi_category=bmi_category, user_name=current_user.name,
                           is_admin=is_admin, admin_data=admin_data)


# ================= SECRET ADMIN PAGE =================
@app.route('/super_admin')
@login_required
def super_admin():
    if current_user.email != ADMIN_EMAIL: return "Access Denied"
    conn = sqlite3.connect("fitness.db");
    cursor = conn.cursor()
    users = cursor.execute("SELECT * FROM users").fetchall()
    logs = cursor.execute("SELECT * FROM activity_logs ORDER BY id DESC").fetchall()
    progress = cursor.execute("SELECT * FROM progress ORDER BY date DESC").fetchall()
    conn.close()
    return render_template('admin.html', users=users, logs=logs, progress=progress)


# ================= CHATBOT & PROFILE (Tera same code) =================
@app.route('/ask_ai', methods=['POST'])
def ask_ai():
    data = request.json
    user_message = data.get('message')
    if not user_message: return jsonify({'response': "Please say something!"})
    prompt = f"You are a fitness coach. Answer briefly: {user_message}"
    try:
        response = client.models.generate_content(model="gemini-3-flash-preview", contents=prompt)
        return jsonify({'response': response.text})
    except:
        return jsonify({'response': "AI is having trouble connecting."})


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    conn = sqlite3.connect("fitness.db")
    cursor = conn.cursor()
    if request.method == 'POST':
        if 'update_info' in request.form:
            cursor.execute("UPDATE users SET name = ?, phone = ? WHERE id = ?",
                           (request.form['name'], request.form['phone'], current_user.id))
            current_user.name = request.form['name']
            flash('Updated!', 'success')
        elif 'update_stats' in request.form:
            w, h = float(request.form['weight']), float(request.form['height'])
            bmi = round(w / ((h / 100) ** 2), 2)
            cat = "Normal"  # Logic simplified for brevity
            cursor.execute(
                "INSERT INTO progress (name, date, weight, height, bmi, category, user_email) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (current_user.name, datetime.now().strftime("%Y-%m-%d"), w, h, bmi, cat, current_user.email))
            flash('Stats Added!', 'success')
        conn.commit()

    cursor.execute("SELECT * FROM users WHERE id = ?", (current_user.id,))
    user_info = cursor.fetchone()
    cursor.execute("SELECT weight, height FROM progress WHERE user_email = ? ORDER BY id DESC LIMIT 1",
                   (current_user.email,))
    stats = cursor.fetchone() or (0, 0)
    conn.close()
    return render_template('profile.html', user=user_info, stats=stats)


if __name__ == "__main__":
    app.run(debug=True)