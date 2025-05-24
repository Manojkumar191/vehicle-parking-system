import os
import cv2
import base64
import easyocr
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:@localhost/vehicle_parking_system'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'secret_key_here'

db = SQLAlchemy(app)

class Vehicle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number_plate = db.Column(db.String(20), nullable=False)
    password = db.Column(db.String(4), nullable=False)
    license_image = db.Column(db.String(100), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey('slot.id'))
    entry_time = db.Column(db.DateTime, nullable=False)
    exit_time = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    is_locked = db.Column(db.Boolean, default=False)

class Slot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(20), nullable=False) 
    is_available = db.Column(db.Boolean, default=True)

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False, unique=True)
    password = db.Column(db.String(50), nullable=False)

class Price(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vehicle_type = db.Column(db.String(20), nullable=False)
    price_per_hour = db.Column(db.Float, nullable=False)

with app.app_context():
    db.create_all()
    if not Price.query.first():
        db.session.add_all([
            Price(vehicle_type="Two-Wheeler", price_per_hour=20.0),
            Price(vehicle_type="Four-Wheeler", price_per_hour=40.0)
        ])
        db.session.commit()

@app.route('/')
def home():
    return redirect(url_for('entry'))

@app.route('/entry', methods=['GET', 'POST'])
def entry():
    if request.method == 'POST':
        vehicle_type = request.form['vehicle_type']
        slot_id = request.form['slot_id']
        password = request.form['password']

        plate_path = session.get('plate_image_path')
        license_path = session.get('license_image_path')

        if not plate_path or not license_path:
            return "Both number plate and license images must be captured."

        number_plate = extract_text(plate_path)
        if not number_plate:
            return "Could not detect number plate."

        number_plate = normalize_plate(number_plate)

        if Vehicle.query.filter_by(number_plate=number_plate, is_active=True).first():
            return "Vehicle is already parked."

        slot = Slot.query.get(slot_id)
        if not slot or not slot.is_available or slot.type != vehicle_type:
            return "Selected slot is invalid or unavailable."

        slot.is_available = False
        vehicle = Vehicle(
            number_plate=number_plate,
            password=password,
            license_image=license_path,
            slot_id=slot.id,
            entry_time=datetime.now(),
            is_active=True
        )
        db.session.add(vehicle)
        db.session.commit()

        return redirect(url_for('dashboard', vehicle_number=number_plate, slot_number=slot.id, entry_time=vehicle.entry_time.strftime('%Y-%m-%d %H:%M:%S')))

    slots = Slot.query.filter_by(is_available=True).all()
    return render_template('entry.html', slots=slots)

@app.route('/dashboard')
def dashboard():
    vehicle_number = request.args.get('vehicle_number')
    slot_number = request.args.get('slot_number')
    entry_time = request.args.get('entry_time')
    return render_template('dashboard.html', vehicle_number=vehicle_number, slot_number=slot_number, entry_time=entry_time)

@app.route('/exit', methods=['GET', 'POST'])
def exit():
    if request.method == 'POST':
        password = request.form['password']
        plate_path = session.get('plate_image_path')

        if not plate_path:
            return "Capture number plate image first."

        number_plate = extract_text(plate_path)
        if not number_plate:
            return "Number plate could not be read."

        number_plate = normalize_plate(number_plate)

        vehicle = Vehicle.query.filter_by(number_plate=number_plate, is_active=True).first()
        if not vehicle:
            return "Vehicle record not found or already checked out."

        if vehicle.is_locked:
            return "Vehicle is locked by admin. Contact support."

        if vehicle.password == password:
            vehicle.exit_time = datetime.now()
            vehicle.is_active = False

            slot = Slot.query.get(vehicle.slot_id)
            slot.is_available = True

            price_entry = Price.query.filter_by(vehicle_type=slot.type).first()
            db.session.commit()

            time_spent = vehicle.exit_time - vehicle.entry_time
            hours = time_spent.total_seconds() / 3600
            fee = round(hours * price_entry.price_per_hour)

            return render_template('exit_result.html', duration=time_spent, fee=fee, number_plate=number_plate)

        return "Invalid password or already exited."
    return render_template('exit.html')

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        admin = Admin.query.filter_by(username=username).first()
        if admin and admin.password == password:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        return "Incorrect admin credentials"
    return render_template('admin_login.html')

@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    slots = Slot.query.all()
    vehicles = Vehicle.query.all()
    return render_template("admin_dashboard.html", slots=slots, vehicles=vehicles)

@app.route('/admin_add_slot', methods=['GET', 'POST'])
def add_slot():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    if request.method == 'POST':
        slot_type = request.form['vehicle_type']
        db.session.add(Slot(type=slot_type, is_available=True))
        db.session.commit()
        return redirect(url_for('admin_dashboard'))
    return render_template("add_slot.html")

@app.route('/admin/manage_prices', methods=['GET', 'POST'])
def manage_prices():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    if request.method == 'POST':
        Price.query.filter_by(vehicle_type="Two-Wheeler").first().price_per_hour = float(request.form['two_wheeler_price'])
        Price.query.filter_by(vehicle_type="Four-Wheeler").first().price_per_hour = float(request.form['four_wheeler_price'])
        db.session.commit()
        return redirect(url_for('admin_dashboard'))

    prices = {
        'two_wheeler': Price.query.filter_by(vehicle_type="Two-Wheeler").first().price_per_hour,
        'four_wheeler': Price.query.filter_by(vehicle_type="Four-Wheeler").first().price_per_hour
    }
    return render_template("manage_prices.html", prices=prices)

@app.route('/capture_plate', methods=['POST'])
def capture_plate():
    data = request.get_json()['image'].split(',')[1]
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'{timestamp}_plate.jpg'
    path = os.path.join('static/captured_plates', filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(base64.b64decode(data))
    session['plate_image_path'] = path
    return jsonify({'message': 'Plate captured', 'path': path})

@app.route('/capture_license', methods=['POST'])
def capture_license():
    data = request.get_json()['image'].split(',')[1]
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'{timestamp}_license.jpg'
    path = os.path.join('static/license_images', filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(base64.b64decode(data))
    session['license_image_path'] = path
    return jsonify({'message': 'License image saved', 'path': path})

@app.route('/admin/delete_slot/<int:slot_id>')
def delete_slot(slot_id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    slot = Slot.query.get_or_404(slot_id)
    if Vehicle.query.filter_by(slot_id=slot_id, is_active=True).first():
        return "Slot is currently in use and cannot be removed."
    db.session.delete(slot)
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/toggle_lock/<int:vehicle_id>')
def toggle_vehicle_lock(vehicle_id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    vehicle = Vehicle.query.get_or_404(vehicle_id)
    if vehicle.is_active:
        vehicle.is_locked = not vehicle.is_locked
        db.session.commit()
    return redirect(url_for('admin_dashboard'))

def extract_text(image_path):
    reader = easyocr.Reader(['en'], gpu=False)
    result = reader.readtext(image_path)
    if result:
        return result[-1][1]
    return None

def normalize_plate(plate):
    return plate.upper().replace(" ", "").strip()

if __name__ == '__main__':
    app.run(debug=True)
