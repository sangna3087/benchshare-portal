import os
import re
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect

app = Flask(__name__)

# Environment Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'benchshare_secret_key_dev_fallback')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///benchshare.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Secure Session Cookie Settings
is_production = os.environ.get('RENDER') is not None or os.environ.get('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = is_production  # HTTPS on Render, HTTP permitted locally in development

# Initialize Extensions
db = SQLAlchemy(app)
csrf = CSRFProtect(app)

# Helper Validators
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    reg_no = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False)

class Equipment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(50), default='Available')

class Reservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    equipment_id = db.Column(db.Integer, db.ForeignKey('equipment.id'), nullable=False)
    time_slot = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default='Pending')

    user = db.relationship('User', backref=db.backref('reservations', lazy=True))
    equipment = db.relationship('Equipment', backref=db.backref('reservations', lazy=True))

# Database Initialization and Safe Password Migration (runs under Gunicorn and direct python execution)
def init_db_and_migrate():
    db.create_all()

    # Seed initial equipment if directory is empty
    if not Equipment.query.first():
        eq1 = Equipment(name='3D Printer Pro-X', category='ECE/IoT Hardware Lab', status='Available')
        eq2 = Equipment(name='Digital Storage Oscilloscope', category='Electronics Lab', status='Available')
        db.session.add_all([eq1, eq2])
        db.session.commit()

    # Transparently migrate existing plaintext passwords to secure Werkzeug hashes
    users = User.query.all()
    migrated_count = 0
    for u in users:
        if u.password and not (u.password.startswith('scrypt:') or u.password.startswith('pbkdf2:')):
            u.password = generate_password_hash(u.password)
            migrated_count += 1
    if migrated_count > 0:
        db.session.commit()

with app.app_context():
    init_db_and_migrate()

# Routes
@app.route('/')
def index():
    equipments = Equipment.query.all()
    reservations = Reservation.query.all()

    active_slots = {}
    for eq in equipments:
        res = Reservation.query.filter_by(equipment_id=eq.id, status='Approved').first()
        if not res:
            res = Reservation.query.filter_by(equipment_id=eq.id, status='Pending').first()
        active_slots[eq.id] = res.time_slot if res else None

    return render_template('s.html', equipments=equipments, reservations=reservations, active_slots=active_slots)

@app.route('/register', methods=['POST'])
def register():
    # Sanitize and extract form inputs
    name = (request.form.get('name') or '').strip()
    reg_no = (request.form.get('reg_no') or '').strip()
    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''

    # Enforce Role-Based Access: Public registration strictly permits 'Student' role
    role = 'Student'

    # Server-Side Input Validation
    if not name or len(name) < 2 or len(name) > 100:
        flash("Registration failed: Name must be between 2 and 100 characters.", "error")
        return redirect(url_for('index'))

    if not reg_no or len(reg_no) < 2 or len(reg_no) > 50:
        flash("Registration failed: Registration number must be between 2 and 50 characters.", "error")
        return redirect(url_for('index'))

    if not email or len(email) > 100 or not EMAIL_REGEX.match(email):
        flash("Registration failed: Please provide a valid email address.", "error")
        return redirect(url_for('index'))

    if not password or len(password) < 6:
        flash("Registration failed: Password must be at least 6 characters long.", "error")
        return redirect(url_for('index'))

    # Check for duplicate email or registration number (case-insensitive for email)
    existing_user = User.query.filter(
        (func.lower(User.email) == email) | (User.reg_no == reg_no)
    ).first()
    if existing_user:
        flash("Registration failed. Email or Reg No might already exist.", "error")
        return redirect(url_for('index'))

    # Hash password securely using Werkzeug
    hashed_password = generate_password_hash(password)
    new_user = User(name=name, reg_no=reg_no, email=email, password=hashed_password, role=role)
    db.session.add(new_user)
    db.session.commit()
    flash("Registration successful! Please login.", "success")
    return redirect(url_for('index'))

@app.route('/login', methods=['POST'])
def login():
    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''

    if not email or not password:
        flash("Invalid credentials!", "error")
        return redirect(url_for('index'))

    user = User.query.filter(func.lower(User.email) == email).first()
    if user:
        is_valid = False
        # Verify Werkzeug cryptographic hash
        if user.password.startswith('scrypt:') or user.password.startswith('pbkdf2:'):
            is_valid = check_password_hash(user.password, password)
        else:
            # Fallback for un-migrated legacy plaintext password
            if user.password == password:
                is_valid = True
                # Upgrade to hash immediately
                user.password = generate_password_hash(password)
                db.session.commit()

        if is_valid:
            session.clear()
            session['user_id'] = user.id
            session['user_name'] = user.name
            session['user_role'] = user.role
            flash("Logged in successfully!", "success")
            return redirect(url_for('index'))

    flash("Invalid credentials!", "error")
    return redirect(url_for('index'))

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for('index'))

# Admin-only route to add new laboratory equipment
@app.route('/add_equipment', methods=['POST'])
def add_equipment():
    if session.get('user_role') != 'Administrator':
        flash("Unauthorized access!", "error")
        return redirect(url_for('index'))

    name = (request.form.get('name') or '').strip()
    category = (request.form.get('category') or '').strip()

    if not name or len(name) < 2 or len(name) > 100:
        flash("Equipment name must be between 2 and 100 characters.", "error")
        return redirect(url_for('index'))

    if not category or len(category) < 2 or len(category) > 100:
        flash("Category must be between 2 and 100 characters.", "error")
        return redirect(url_for('index'))

    new_eq = Equipment(name=name, category=category, status='Available')
    db.session.add(new_eq)
    db.session.commit()
    flash("New equipment added successfully!", "success")
    return redirect(url_for('index'))

# Admin-only route to securely provision staff accounts (Lab Assistant / Administrator)
@app.route('/create_staff', methods=['POST'])
def create_staff():
    if session.get('user_role') != 'Administrator':
        flash("Unauthorized access!", "error")
        return redirect(url_for('index'))

    name = (request.form.get('name') or '').strip()
    reg_no = (request.form.get('reg_no') or '').strip()
    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''
    role = request.form.get('role')

    if role not in ['Lab Assistant', 'Administrator']:
        flash("Invalid role selection for staff account.", "error")
        return redirect(url_for('index'))

    if not name or len(name) < 2 or len(name) > 100:
        flash("Staff Name must be between 2 and 100 characters.", "error")
        return redirect(url_for('index'))

    if not reg_no or len(reg_no) < 2 or len(reg_no) > 50:
        flash("Registration/Staff ID must be between 2 and 50 characters.", "error")
        return redirect(url_for('index'))

    if not email or len(email) > 100 or not EMAIL_REGEX.match(email):
        flash("Please provide a valid staff email address.", "error")
        return redirect(url_for('index'))

    if not password or len(password) < 6:
        flash("Staff password must be at least 6 characters long.", "error")
        return redirect(url_for('index'))

    existing_user = User.query.filter(
        (func.lower(User.email) == email) | (User.reg_no == reg_no)
    ).first()
    if existing_user:
        flash("Staff account creation failed: Email or Staff ID already exists.", "error")
        return redirect(url_for('index'))

    hashed_pw = generate_password_hash(password)
    staff_user = User(name=name, reg_no=reg_no, email=email, password=hashed_pw, role=role)
    db.session.add(staff_user)
    db.session.commit()
    flash(f"Staff account for {name} ({role}) created successfully!", "success")
    return redirect(url_for('index'))

# Student route to reserve equipment
@app.route('/reserve/<int:eq_id>', methods=['POST'])
def reserve(eq_id):
    if 'user_id' not in session:
        return redirect(url_for('index'))

    if session.get('user_role') != 'Student':
        flash("Only students can place equipment reservations.", "error")
        return redirect(url_for('index'))

    eq = db.session.get(Equipment, eq_id)
    if not eq:
        flash("Selected equipment does not exist.", "error")
        return redirect(url_for('index'))

    if eq.status == 'Reserved':
        flash("This equipment is already reserved.", "error")
        return redirect(url_for('index'))

    time_slot = (request.form.get('time_slot') or '').strip()
    description = (request.form.get('description') or '').strip()

    if not time_slot or len(time_slot) < 2 or len(time_slot) > 100:
        flash("Please specify a valid time slot (e.g., 2PM - 4PM).", "error")
        return redirect(url_for('index'))

    if not description or len(description) < 3 or len(description) > 1000:
        flash("Please provide an experiment description (at least 3 characters).", "error")
        return redirect(url_for('index'))

    # Check for same-user simultaneous booking conflict
    existing_booking = Reservation.query.filter_by(
        user_id=session['user_id'],
        time_slot=time_slot
    ).filter(Reservation.status.in_(['Pending', 'Approved'])).first()

    if existing_booking:
        flash("You have already reserved equipment for this time slot.", "error")
        return redirect(url_for('index'))

    new_res = Reservation(
        user_id=session['user_id'],
        equipment_id=eq_id,
        time_slot=time_slot,
        description=description,
        status='Pending'
    )
    db.session.add(new_res)
    eq.status = 'Reserved'
    db.session.commit()
    flash("Reservation request submitted successfully!", "success")
    return redirect(url_for('index'))

# Staff route to approve reservation (CSRF-protected POST)
@app.route('/approve/<int:res_id>', methods=['POST'])
def approve(res_id):
    if session.get('user_role') not in ['Lab Assistant', 'Administrator']:
        flash("Unauthorized access!", "error")
        return redirect(url_for('index'))

    res = db.session.get(Reservation, res_id)
    if res:
        # Check if equipment is already approved for another reservation in the same slot
        existing_approved = Reservation.query.filter(
            Reservation.equipment_id == res.equipment_id,
            Reservation.time_slot == res.time_slot,
            Reservation.status == 'Approved',
            Reservation.id != res.id
        ).first()

        if existing_approved:
            flash(f"Approval failed: This equipment is already approved for Reservation #{existing_approved.id} in this time slot.", "error")
            return redirect(url_for('index'))

        res.status = 'Approved'
        if res.equipment:
            res.equipment.status = 'Reserved'
        db.session.commit()
        flash(f"Reservation #{res.id} Approved.", "success")
    return redirect(url_for('index'))

# Staff route to reject reservation (CSRF-protected POST)
@app.route('/reject/<int:res_id>', methods=['POST'])
def reject(res_id):
    if session.get('user_role') not in ['Lab Assistant', 'Administrator']:
        flash("Unauthorized access!", "error")
        return redirect(url_for('index'))

    res = db.session.get(Reservation, res_id)
    if res:
        res.status = 'Rejected'
        # Crucial fix: Only reset equipment to Available if no other Pending or Approved reservation exists for this equipment
        other_active = Reservation.query.filter(
            Reservation.equipment_id == res.equipment_id,
            Reservation.id != res.id,
            Reservation.status.in_(['Pending', 'Approved'])
        ).first()

        if not other_active and res.equipment:
            res.equipment.status = 'Available'

        db.session.commit()
        flash(f"Reservation #{res.id} Rejected.", "success")
    return redirect(url_for('index'))

# Flask CLI Command to provision staff from terminal
@app.cli.command("create-staff-user")
def create_staff_user_cli():
    """CLI to create an administrator or lab assistant interactively."""
    import click
    name = click.prompt("Full Name")
    reg_no = click.prompt("Registration / Staff ID")
    email = click.prompt("Email ID")
    password = click.prompt("Password", hide_input=True)
    role = click.prompt("Role", type=click.Choice(['Lab Assistant', 'Administrator'], case_sensitive=False))

    existing = User.query.filter((func.lower(User.email) == email.lower()) | (User.reg_no == reg_no)).first()
    if existing:
        click.echo("Error: User with this email or reg_no already exists.")
        return

    u = User(name=name, reg_no=reg_no, email=email.lower(), password=generate_password_hash(password), role=role)
    db.session.add(u)
    db.session.commit()
    click.echo(f"Successfully created staff user: {name} ({role})")

if __name__ == '__main__':
    app.run(debug=True)
