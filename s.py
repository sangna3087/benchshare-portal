from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///benchshare.db'
app.config['SECRET_KEY'] = 'benchshare_secret_key'
db = SQLAlchemy(app)

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    reg_no = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
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
    name = request.form['name']
    reg_no = request.form['reg_no']
    email = request.form['email']
    password = request.form['password']
    role = request.form['role']

    existing_user = User.query.filter((User.email == email) | (User.reg_no == reg_no)).first()
    if existing_user:
        flash("Registration failed. Email or Reg No might already exist.")
        return redirect(url_for('index'))

    new_user = User(name=name, reg_no=reg_no, email=email, password=password, role=role)
    db.session.add(new_user)
    db.session.commit()
    flash("Registration successful! Please login.")
    return redirect(url_for('index'))

@app.route('/login', methods=['POST'])
def login():
    email = request.form['email']
    password = request.form['password']

    user = User.query.filter_by(email=email, password=password).first()
    if user:
        session['user_id'] = user.id
        session['user_name'] = user.name
        session['user_role'] = user.role
        flash("Logged in successfully!")
        return redirect(url_for('index'))
    
    flash("Invalid credentials!")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully.")
    return redirect(url_for('index'))

# Admin-only route to add new equipment
@app.route('/add_equipment', methods=['POST'])
def add_equipment():
    if session.get('user_role') != 'Administrator':
        flash("Unauthorized access!")
        return redirect(url_for('index'))
    
    name = request.form['name']
    category = request.form['category']
    
    new_eq = Equipment(name=name, category=category, status='Available')
    db.session.add(new_eq)
    db.session.commit()
    flash("New equipment added successfully!")
    return redirect(url_for('index'))

@app.route('/reserve/<int:eq_id>', methods=['POST'])
def reserve(eq_id):
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    eq = Equipment.query.get(eq_id)
    if eq and eq.status == 'Reserved':
        flash("Yeh equipment pehle se reserved hai!")
        return redirect(url_for('index'))

    time_slot = request.form['time_slot']
    description = request.form['description']

    existing_booking = Reservation.query.filter_by(
        user_id=session['user_id'], 
        time_slot=time_slot
    ).filter(Reservation.status.in_(['Pending', 'Approved'])).first()

    if existing_booking:
        flash("Aapne is time slot par pehle hi ek equipment reserve kiya hua hai!")
        return redirect(url_for('index'))

    new_res = Reservation(user_id=session['user_id'], equipment_id=eq_id, time_slot=time_slot, description=description, status='Pending')
    db.session.add(new_res)
    
    if eq:
        eq.status = 'Reserved'

    db.session.commit()
    flash("Reservation request submitted successfully!")
    return redirect(url_for('index'))

@app.route('/approve/<int:res_id>')
def approve(res_id):
    if session.get('user_role') not in ['Lab Assistant', 'Administrator']:
        return redirect(url_for('index'))
    
    res = Reservation.query.get(res_id)
    if res:
        existing_approved = Reservation.query.filter_by(
            equipment_id=res.equipment_id,
            time_slot=res.time_slot,
            status='Approved'
        ).first()

        if existing_approved:
            flash(f"Approval failed: Yeh equipment is time slot par pehle hi Reservation #{existing_approved.id} ke liye approved hai!")
            return redirect(url_for('index'))

        res.status = 'Approved'
        if res.equipment:
            res.equipment.status = 'Reserved'
        db.session.commit()
        flash(f"Reservation #{res.id} Approved.")
    return redirect(url_for('index'))

@app.route('/reject/<int:res_id>')
def reject(res_id):
    if session.get('user_role') not in ['Lab Assistant', 'Administrator']:
        return redirect(url_for('index'))
    
    res = Reservation.query.get(res_id)
    if res:
        res.status = 'Rejected'
        if res.equipment:
            res.equipment.status = 'Available'
        db.session.commit()
        flash(f"Reservation #{res.id} Rejected.")
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not Equipment.query.first():
            eq1 = Equipment(name='3D Printer Pro-X', category='ECE/IoT Hardware Lab', status='Available')
            eq2 = Equipment(name='Digital Storage Oscilloscope', category='Electronics Lab', status='Available')
            db.session.add_all([eq1, eq2])
            db.session.commit()
    app.run(debug=True)