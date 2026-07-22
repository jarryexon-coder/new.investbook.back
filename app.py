# requirements.txt
# flask, flask-sqlalchemy, flask-bcrypt, flask-cors, flask-socketio, eventlet, pyjwt, python-dotenv

from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_cors import CORS
import sys
from flask_socketio import SocketIO, join_room, emit
import jwt
from datetime import datetime, timedelta
from functools import wraps
import os
from dotenv import load_dotenv
from trust_algorithm import TrustScoringEngine
from document_signing import DocumentSigning
import json
import hashlib

load_dotenv()

# 1. Create app first
app = Flask(__name__)

# 2. Configure app
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///invest.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 3. Initialize extensions
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# ✅ Simple CORS - Allow all
CORS(app, origins=["http://localhost:3000", "http://localhost:5000", "https://investbook-production.up.railway.app"])

# ✅ KEEP THIS - For SocketIO (real-time features)
socketio = SocketIO(app, cors_allowed_origins="*")

# ✅ Create tables on startup
with app.app_context():
    db.create_all()
    print("✅ Database tables created/verified")

# 4. Import and register blueprints AFTER app is created
from admin_dashboard import admin_bp
app.register_blueprint(admin_bp, url_prefix='/admin')

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found', 'message': 'The requested URL was not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error', 'message': str(error)}), 500

# Add this before your routes
@app.route('/')
def home():
    return jsonify({
        'name': 'InvestBook API',
        'version': '1.0.0',
        'status': 'running',
        'endpoints': {
            'register': '/api/register [POST]',
            'login': '/api/login [POST]',
            'deals': '/api/deals [GET]',
            'create_deal': '/api/deals [POST]',
            'groups': '/api/groups [POST]',
            'group_status': '/api/groups/<id>/status [GET]'
        }
    })

@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'database': 'connected' if db.session.is_active else 'disconnected'
    })

# --- Database Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    bio = db.Column(db.Text, default="")
    trust_score = db.Column(db.Float, default=50.0)
    is_verified = db.Column(db.Boolean, default=False)
    investments_completed = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    subscription_plan = db.Column(db.String(50), nullable=True)
    subscription_expiry = db.Column(db.DateTime, nullable=True)
    stripe_customer_id = db.Column(db.String(200), nullable=True)

class Deal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    asset_type = db.Column(db.String(50), nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    min_investment = db.Column(db.Float, nullable=False)
    location = db.Column(db.String(200))
    expected_roi = db.Column(db.String(50))
    status = db.Column(db.String(50), default='open')
    sponsor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sponsor = db.relationship('User', backref='deals_listed')

class DealInterest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'))
    status = db.Column(db.String(50), default='pending')
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='interests')
    deal = db.relationship('Deal', backref='interested_users')

class TrustReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    reviewee_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'))
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class InvestmentGroup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'))
    name = db.Column(db.String(200))
    total_committed = db.Column(db.Float, default=0.0)
    target_amount = db.Column(db.Float)
    status = db.Column(db.String(50), default='forming')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    deal = db.relationship('Deal', backref='investment_groups')

class InvestmentCommitment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('investment_group.id'))
    investor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    amount_committed = db.Column(db.Float, nullable=False)
    amount_confirmed = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(50), default='pending')
    proof_of_funds = db.Column(db.String(500))
    confirmed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    investor = db.relationship('User', backref='commitments')
    group = db.relationship('InvestmentGroup', backref='commitments')

class InvestmentMilestone(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('investment_group.id'))
    title = db.Column(db.String(200))
    description = db.Column(db.Text)
    due_date = db.Column(db.DateTime)
    completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    group = db.relationship('InvestmentGroup', backref='milestones')

def calculate_trust_score(user):
    avg_rating = db.session.query(db.func.avg(TrustReview.rating)).filter_by(reviewee_id=user.id).scalar() or 3
    review_score = (avg_rating / 5) * 60
    deal_score = min(user.investments_completed / 10, 1) * 30
    verif_score = 10 if user.is_verified else 0
    return review_score + deal_score + verif_score

# --- Authentication ---
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization')
        
        if auth_header:
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
            else:
                token = auth_header
        
        if not token:
            token = request.args.get('token')
        
        if not token:
            return jsonify({'message': 'Token is missing!'}), 401
        
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user = User.query.get(data['user_id'])
            
            if not current_user:
                return jsonify({'message': 'User not found!'}), 401
            
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token has expired!'}), 401
        except jwt.InvalidTokenError as e:
            return jsonify({'message': 'Invalid token!'}), 401
        
        return f(current_user, *args, **kwargs)
    return decorated

# --- Routes ---
@app.route('/')
def index():
    return jsonify({
        'message': 'InvestBook API is running!',
        'version': '1.0.0',
        'endpoints': {
            'register': '/api/register',
            'login': '/api/login',
            'deals': '/api/deals',
            'groups': '/api/groups'
        }
    })

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.json
        
        if not data or not data.get('username') or not data.get('email') or not data.get('password'):
            return jsonify({'message': 'Missing required fields'}), 400
        
        if User.query.filter_by(username=data['username']).first():
            return jsonify({'message': 'Username already exists'}), 400
        
        if User.query.filter_by(email=data['email']).first():
            return jsonify({'message': 'Email already exists'}), 400
        
        hashed = bcrypt.generate_password_hash(data['password']).decode('utf-8')
        user = User(username=data['username'], email=data['email'], password_hash=hashed)
        db.session.add(user)
        db.session.commit()
        
        return jsonify({
            'message': 'User created successfully',
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email
            }
        }), 201
    except Exception as e:
        db.session.rollback()
        print(f"Registration error: {str(e)}")
        return jsonify({'message': f'Registration failed: {str(e)}'}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'message': 'No data provided'}), 400
            
        email = data.get('email')
        password = data.get('password')
        
        if not email or not password:
            return jsonify({'message': 'Email and password required'}), 400
        
        user = User.query.filter_by(email=email).first()
        if not user:
            return jsonify({'message': 'Invalid credentials'}), 401
        
        try:
            is_valid = bcrypt.check_password_hash(user.password_hash, password)
        except Exception as e:
            return jsonify({'message': 'Invalid credentials'}), 401
        
        if not is_valid:
            return jsonify({'message': 'Invalid credentials'}), 401
        
        token = jwt.encode({
            'user_id': user.id,
            'exp': datetime.utcnow() + timedelta(hours=24)
        }, app.config['SECRET_KEY'], algorithm='HS256')
        
        user_data = {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'trust_score': user.trust_score
        }
        
        return jsonify({
            'token': token,
            'user': user_data
        }), 200
        
    except Exception as e:
        print(f"Login error: {str(e)}")
        return jsonify({'error': 'Internal server error', 'message': str(e)}), 500

@app.route('/api/deals', methods=['GET'])
def get_deals():
    deals = Deal.query.filter_by(status='open').all()
    return jsonify([{
        'id': d.id, 'title': d.title, 'description': d.description, 
        'asset_type': d.asset_type, 'total_price': d.total_price,
        'min_investment': d.min_investment, 'location': d.location,
        'expected_roi': d.expected_roi, 'sponsor_username': d.sponsor.username
    } for d in deals])

@app.route('/api/deals', methods=['POST'])
@token_required
def create_deal(current_user):
    try:
        data = request.json
        
        required_fields = ['title', 'description', 'asset_type', 'total_price', 'min_investment']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        deal = Deal(
            title=data['title'],
            description=data['description'],
            asset_type=data['asset_type'],
            total_price=float(data['total_price']),
            min_investment=float(data['min_investment']),
            location=data.get('location', ''),
            expected_roi=data.get('expected_roi', ''),
            sponsor_id=current_user.id
        )
        
        db.session.add(deal)
        db.session.commit()
        
        return jsonify({
            'message': 'Deal created successfully',
            'deal_id': deal.id,
            'deal': {
                'id': deal.id,
                'title': deal.title,
                'description': deal.description,
                'asset_type': deal.asset_type,
                'total_price': deal.total_price,
                'min_investment': deal.min_investment,
                'location': deal.location,
                'expected_roi': deal.expected_roi,
                'sponsor_id': deal.sponsor_id,
                'status': deal.status
            }
        }), 201
    except Exception as e:
        db.session.rollback()
        print(f"Error creating deal: {str(e)}")
        return jsonify({'error': str(e), 'message': 'Failed to create deal'}), 500

# Import Stripe routes
from stripe_routes import *

# --- WebSocket for Real-Time Chat ---
@socketio.on('join_deal_chat')
def handle_join_deal_chat(data):
    deal_id = data['deal_id']
    join_room(f'deal_{deal_id}')
    emit('message', {'system': f"User joined deal {deal_id} chat"}, room=f'deal_{deal_id}')

@socketio.on('deal_chat_message')
def handle_deal_chat_message(data):
    emit('message', {
        'user': data['username'],
        'message': data['message'],
        'timestamp': datetime.utcnow().isoformat()
    }, room=f"deal_{data['deal_id']}")

if __name__ == '__main__':
    # ✅ Create tables if they don't exist
    with app.app_context():
        db.create_all()
        print("✅ Database tables created/verified")
    
    # Get port from environment variable
    port = int(os.environ.get('PORT', 5000))
    
    # In production, use eventlet with proper host
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        print(f"Starting production server on port {port}")
        socketio.run(app, host='0.0.0.0', port=port, debug=False)
    else:
        socketio.run(app, debug=True, port=port)
