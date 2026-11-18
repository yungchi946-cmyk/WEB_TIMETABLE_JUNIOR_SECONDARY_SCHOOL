from flask import Flask, request, jsonify, send_from_directory, session, redirect
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from timetable_engine import TimetableEngine
import json
import os
import re
import secrets

# ── FLASK APP SETUP ─────────────────────────────────────────────────────────
app = Flask(__name__, static_folder='.', template_folder='.')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(os.path.dirname(__file__), 'timetable.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Session signing key: from the SECRET_KEY env var, else a random key saved next to the app
# so logins survive restarts. Keep secret.key private (don't share or commit it).
def _load_secret_key():
    env = os.environ.get('SECRET_KEY')
    if env:
        return env
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'secret.key')
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, 'w') as f:
        f.write(key)
    return key

app.secret_key = _load_secret_key()
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

db = SQLAlchemy(app)

# ── MODELS ─────────────────────────────────────────────────────────────────
class User(db.Model):
    __tablename__ = 'users'

    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email         = db.Column(db.String(254), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    school        = db.Column(db.String(100), nullable=False)
    created       = db.Column(db.DateTime, server_default=db.func.now())

class Teacher(db.Model):
    __tablename__ = 'teachers'

    id       = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)  # owning school
    name     = db.Column(db.String(100), nullable=False)
    classes  = db.Column(db.Text, default='[]')   # JSON string
    subjects = db.Column(db.Text, default='[]')

    def to_dict(self):
        return {
            'id':       self.id,
            'name':     self.name,
            'classes':  json.loads(self.classes),
            'subjects': json.loads(self.subjects),
        }

class Timetable(db.Model):
    __tablename__ = 'timetables'

    id      = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)  # owning school
    data    = db.Column(db.Text, nullable=False)        # full result as JSON
    created = db.Column(db.DateTime, server_default=db.func.now())

    def to_dict(self):
        return json.loads(self.data)

LEVELS = ['JSS1', 'JSS2', 'JSS3']
DEFAULT_ARMS = ['A', 'B', 'C']
ARM_NAME_RE = re.compile(r'^[A-Za-z0-9]{1,12}$')   # URL-safe, short

class ClassArm(db.Model):
    __tablename__ = 'class_arms'
    __table_args__ = (db.UniqueConstraint('user_id', 'level', 'name', name='uq_user_level_arm'),)

    id    = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)  # owning school
    level = db.Column(db.String(10), nullable=False)
    name  = db.Column(db.String(12), nullable=False)   # e.g. "A", "Gold"  (full arm = level + name)

def uid():
    """Id of the logged-in school account (all data is scoped to it)."""
    return session['user_id']

def seed_arms(user_id):
    """New schools start with arms A, B, C on every level (editable on the teachers page)."""
    for lvl in LEVELS:
        for name in DEFAULT_ARMS:
            db.session.add(ClassArm(user_id=user_id, level=lvl, name=name))
    db.session.commit()

def arms_config(user_id):
    """{'JSS1': ['A','B','C'], ...} in the order they were added."""
    cfg = {lvl: [] for lvl in LEVELS}
    for a in ClassArm.query.filter_by(user_id=user_id).order_by(ClassArm.id).all():
        if a.level in cfg:
            cfg[a.level].append(a.name)
    return cfg

# Create tables
with app.app_context():
    # One-time migration: tables from the single-school version have no user_id column.
    # Their contents were demo data, so they are dropped and recreated per school.
    insp = inspect(db.engine)
    existing_tables = insp.get_table_names()
    arms_reset = False
    for tbl in ('teachers', 'class_arms', 'timetables'):
        if tbl in existing_tables and 'user_id' not in [c['name'] for c in insp.get_columns(tbl)]:
            db.session.execute(text(f'DROP TABLE {tbl}'))
            arms_reset = arms_reset or tbl == 'class_arms'
    db.session.commit()

    db.create_all()
    if arms_reset:   # accounts that already exist get the default arms again
        for u in User.query.all():
            seed_arms(u.id)

# ── AUTH ───────────────────────────────────────────────────────────────────
EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
PUBLIC_ENDPOINTS = {'login_page', 'api_signup', 'api_login', 'static'}

@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if session.get('user_id') and db.session.get(User, session['user_id']):
        return None
    session.clear()
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Login required'}), 401
    return redirect('/login')

@app.after_request
def no_cache_pages(resp):
    # keep logged-in pages from being shown from cache after logout
    if request.endpoint != 'static':
        resp.headers['Cache-Control'] = 'no-store'
    return resp

def _start_session(user):
    session.clear()
    session['user_id'] = user.id
    session.permanent = True

@app.route('/login')
def login_page():
    if session.get('user_id') and db.session.get(User, session['user_id']):
        return redirect('/')
    return send_from_directory('.', 'auth.html')

@app.route('/api/signup', methods=['POST'])
def api_signup():
    data     = request.get_json(silent=True) or {}
    email    = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    school   = ' '.join((data.get('school') or '').split())

    if len(school) < 2 or len(school) > 100:
        return jsonify({'error': 'Enter your school name (2-100 characters).'}), 400
    if len(email) > 254 or not EMAIL_RE.match(email):
        return jsonify({'error': 'Enter a valid e-mail address.'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters.'}), 400
    if len(password) > 128:
        return jsonify({'error': 'Password is too long (max 128 characters).'}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'An account with this e-mail already exists. Try logging in.'}), 409

    user = User(email=email, school=school, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()
    seed_arms(user.id)
    _start_session(user)
    return jsonify({'email': user.email, 'school': user.school}), 201

@app.route('/api/login', methods=['POST'])
def api_login():
    data     = request.get_json(silent=True) or {}
    email    = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'error': 'Incorrect e-mail or password.'}), 401
    _start_session(user)
    return jsonify({'email': user.email, 'school': user.school})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/me', methods=['GET'])
def api_me():
    user = db.session.get(User, session['user_id'])
    return jsonify({'email': user.email, 'school': user.school})

# ── SERVE PAGES ────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/timetable')
def timetable_page():
    return send_from_directory('.', 'timetable.html')

# ── TEACHERS API ───────────────────────────────────────────────────────────
@app.route('/api/teachers', methods=['GET'])
def get_teachers():
    teachers = Teacher.query.filter_by(user_id=uid()).all()
    return jsonify([t.to_dict() for t in teachers])

@app.route('/api/teachers', methods=['POST'])
def add_teacher():
    data = request.get_json()
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400

    teacher = Teacher(
        user_id  = uid(),
        name     = name,
        classes  = json.dumps(data.get('classes', [])),
        subjects = json.dumps(data.get('subjects', []))   # <-- add this line
    )
    db.session.add(teacher)
    db.session.commit()
    return jsonify(teacher.to_dict()), 201

@app.route('/api/teachers/<int:tid>', methods=['PUT'])
def update_teacher(tid):
    teacher = Teacher.query.filter_by(id=tid, user_id=uid()).first_or_404(description='Teacher not found')
    data = request.get_json()

    teacher.name     = (data.get('name') or teacher.name).strip()
    teacher.classes  = json.dumps(data.get('classes', json.loads(teacher.classes)))
    teacher.subjects = json.dumps(data.get('subjects', json.loads(teacher.subjects)))

    db.session.commit()
    return jsonify(teacher.to_dict())

@app.route('/api/teachers/<int:tid>', methods=['DELETE'])
def delete_teacher(tid):
    teacher = Teacher.query.filter_by(id=tid, user_id=uid()).first_or_404(description='Teacher not found')
    db.session.delete(teacher)
    db.session.commit()
    return jsonify({'deleted': tid})

# ── CLASS ARMS API ─────────────────────────────────────────────────────────
@app.route('/api/arms', methods=['GET'])
def get_arms():
    return jsonify(arms_config(uid()))

@app.route('/api/arms', methods=['POST'])
def add_arm():
    data  = request.get_json() or {}
    level = (data.get('level') or '').strip().upper()
    name  = (data.get('name') or '').strip()
    if level not in LEVELS:
        return jsonify({'error': 'Unknown class level'}), 400
    if not ARM_NAME_RE.match(name):
        return jsonify({'error': 'Arm name must be 1-12 letters/numbers (e.g. A, B, Gold)'}), 400
    existing = [n.lower() for n in arms_config(uid())[level]]
    if name.lower() in existing:
        return jsonify({'error': f'{level}{name} already exists'}), 409
    db.session.add(ClassArm(user_id=uid(), level=level, name=name))
    db.session.commit()
    return jsonify(arms_config(uid())), 201

@app.route('/api/arms/<level>/<name>', methods=['DELETE'])
def delete_arm(level, name):
    arm = ClassArm.query.filter_by(user_id=uid(), level=level.upper(), name=name).first_or_404(description='Arm not found')
    db.session.delete(arm)
    db.session.commit()
    return jsonify(arms_config(uid()))

# ── TIMETABLE GENERATION API ───────────────────────────────────────────────
@app.route('/api/generate', methods=['POST'])
def generate():
    teachers = Teacher.query.filter_by(user_id=uid()).all()
    if not teachers:
        return jsonify({'error': 'No teachers found. Add teachers first.'}), 400

    teachers_data = [t.to_dict() for t in teachers]
    arms = arms_config(uid())
    if not any(arms.values()):
        return jsonify({'error': 'No class arms defined. Add at least one arm first.'}), 400

    engine = TimetableEngine(teachers_data, arms)
    result = engine.generate()
    if not result:
        return jsonify({'error': 'Nothing to generate. Make sure teachers are assigned to levels that have arms.'}), 400

    # Persist the generated timetable (overwrites previous one by storing newest)
    timetable = Timetable(user_id=uid(), data=json.dumps(result))
    db.session.add(timetable)
    db.session.commit()

    return jsonify(result)

@app.route('/api/timetables', methods=['GET'])
def get_timetables():
    latest = Timetable.query.filter_by(user_id=uid()).order_by(Timetable.id.desc()).first()
    if not latest:
        return jsonify({})
    return jsonify(latest.to_dict())

if __name__ == '__main__':
    app.run(host="0.0.0.0", debug=True, port=5000)