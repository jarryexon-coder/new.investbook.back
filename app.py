# requirements.txt
# flask, flask-sqlalchemy, flask-bcrypt, flask-cors, flask-socketio, eventlet, pyjwt, python-dotenv, flask-caching, psycopg2-binary

from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_cors import CORS
import sys
from flask_socketio import SocketIO, join_room, leave_room, emit
import jwt
from datetime import datetime, timedelta
from functools import wraps
import os
from dotenv import load_dotenv
from trust_algorithm import TrustScoringEngine
from document_signing import DocumentSigning
import json
import hashlib
from flask_caching import Cache

# Load environment variables FIRST
load_dotenv()

# 1. Create app first
app = Flask(__name__)

# 2. Configure app with PostgreSQL
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-me')

# ===== USE POSTGRESQL =====
# Get the DATABASE_URL from Railway environment
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    # Fallback to SQLite for local development
    print("⚠️ DATABASE_URL not found, using SQLite for local development")
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///invest.db'
else:
    # Use PostgreSQL
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    print(f"✅ Using PostgreSQL database: {DATABASE_URL[:30]}...")

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 10,
    'pool_recycle': 300,
    'pool_pre_ping': True,
}

# Add caching configuration
app.config['CACHE_TYPE'] = 'SimpleCache'
app.config['CACHE_DEFAULT_TIMEOUT'] = 1800  # 30 minutes cache

# 3. Initialize extensions
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
cache = Cache(app)

# CORS - Allow all
CORS(app, origins=["http://localhost:3000", "http://localhost:5000", "https://investbook-production.up.railway.app"])

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# 4. Import and register blueprints
from admin_dashboard import admin_bp
app.register_blueprint(admin_bp, url_prefix='/admin')

from stripe_routes import stripe_bp
app.register_blueprint(stripe_bp, url_prefix='/api')

# ===== DATABASE MODELS =====

class User(db.Model):
    __tablename__ = 'users'  # Explicit table name
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
    stripe_subscription_id = db.Column(db.String(200), nullable=True)

class Deal(db.Model):
    __tablename__ = 'deals'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    asset_type = db.Column(db.String(50), nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    min_investment = db.Column(db.Float, nullable=False)
    location = db.Column(db.String(200))
    expected_roi = db.Column(db.String(50))
    status = db.Column(db.String(50), default='open')
    sponsor_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sponsor = db.relationship('User', backref='deals_listed')

class DealInterest(db.Model):
    __tablename__ = 'deal_interests'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'))
    status = db.Column(db.String(50), default='pending')
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='interests')
    deal = db.relationship('Deal', backref='interested_users')

class TrustReview(db.Model):
    __tablename__ = 'trust_reviews'
    id = db.Column(db.Integer, primary_key=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    reviewee_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'))
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class InvestmentGroup(db.Model):
    __tablename__ = 'investment_groups'
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'))
    name = db.Column(db.String(200))
    total_committed = db.Column(db.Float, default=0.0)
    target_amount = db.Column(db.Float)
    status = db.Column(db.String(50), default='forming')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    deal = db.relationship('Deal', backref='investment_groups')

class InvestmentCommitment(db.Model):
    __tablename__ = 'investment_commitments'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('investment_groups.id'))
    investor_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    amount_committed = db.Column(db.Float, nullable=False)
    amount_confirmed = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(50), default='pending')
    proof_of_funds = db.Column(db.String(500))
    confirmed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    investor = db.relationship('User', backref='commitments')
    group = db.relationship('InvestmentGroup', backref='commitments')

class InvestmentMilestone(db.Model):
    __tablename__ = 'investment_milestones'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('investment_groups.id'))
    title = db.Column(db.String(200))
    description = db.Column(db.Text)
    due_date = db.Column(db.DateTime)
    completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    group = db.relationship('InvestmentGroup', backref='milestones')

class PortfolioInvestment(db.Model):
    __tablename__ = 'portfolio_investments'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'))
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = db.relationship('User', backref='portfolio_investments')
    deal = db.relationship('Deal', backref='portfolio_investments')

class DealChatMessage(db.Model):
    __tablename__ = 'deal_chat_messages'
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_edited = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    user = db.relationship('User', backref='deal_chat_messages')
    deal = db.relationship('Deal', backref='chat_messages_all')

class DealChatParticipant(db.Model):
    __tablename__ = 'deal_chat_participants'
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_read_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_muted = db.Column(db.Boolean, default=False)
    user = db.relationship('User', backref='deal_chat_participants')
    deal = db.relationship('Deal', backref='chat_participants')

class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    read = db.Column(db.Boolean, default=False)
    user = db.relationship('User', backref='chat_messages')
    deal = db.relationship('Deal', backref='chat_messages')

# ===== ERROR HANDLERS =====
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found', 'message': 'The requested URL was not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error', 'message': str(error)}), 500

# ===== ROUTES =====
@app.route('/')
def home():
    return jsonify({
        'name': 'InvestBook API',
        'version': '1.0.0',
        'status': 'running',
        'database': 'PostgreSQL' if os.getenv('DATABASE_URL') else 'SQLite',
        'endpoints': {
            'register': '/api/register [POST]',
            'login': '/api/login [POST]',
            'deals': '/api/deals [GET]',
            'create_deal': '/api/deals [POST]',
            'groups': '/api/groups [POST]',
            'group_status': '/api/groups/<id>/status [GET]',
            'portfolio': '/api/portfolio [GET]',
            'investments': '/api/investments [POST]'
        }
    })

@app.route('/health')
def health_check():
    try:
        # Test database connection
        db.session.execute('SELECT 1')
        db_status = 'connected'
    except Exception as e:
        db_status = f'error: {str(e)}'
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'database': db_status,
        'database_type': 'PostgreSQL' if os.getenv('DATABASE_URL') else 'SQLite'
    })

# ===== TOKEN REQUIRED DECORATOR =====
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

# ===== AUTHENTICATION ROUTES =====
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
            'trust_score': user.trust_score,
            'subscription_plan': user.subscription_plan,
            'subscription_expiry': user.subscription_expiry.isoformat() if user.subscription_expiry else None
        }
        
        return jsonify({
            'token': token,
            'user': user_data
        }), 200
        
    except Exception as e:
        print(f"Login error: {str(e)}")
        return jsonify({'error': 'Internal server error', 'message': str(e)}), 500

# ===== PORTFOLIO ROUTES =====
@app.route('/api/portfolio', methods=['GET'])
@token_required
def get_portfolio(current_user):
    """Get user's portfolio investments"""
    try:
        investments = PortfolioInvestment.query.filter_by(
            user_id=current_user.id,
            status='active'
        ).all()
        
        if not investments:
            sample_portfolio = {
                "investments": [
                    {
                        "id": "1",
                        "title": "Commercial Office Building",
                        "type": "property",
                        "amount": 250000,
                        "date": "2024-01-15",
                        "return": 12.5,
                        "status": "active",
                        "location": "New York, NY",
                        "propertyType": "Office"
                    },
                    {
                        "id": "2",
                        "title": "Tech Startup Investment",
                        "type": "business",
                        "amount": 100000,
                        "date": "2024-02-01",
                        "return": 18.2,
                        "status": "active",
                        "location": "San Francisco, CA",
                        "propertyType": "Technology"
                    },
                    {
                        "id": "3",
                        "title": "Retail Space Portfolio",
                        "type": "property",
                        "amount": 500000,
                        "date": "2024-03-10",
                        "return": 8.7,
                        "status": "pending",
                        "location": "Chicago, IL",
                        "propertyType": "Retail"
                    }
                ],
                "totalValue": 850000,
                "totalInvestments": 3,
                "averageReturn": 13.1
            }
            return jsonify(sample_portfolio), 200
        
        portfolio_data = []
        total_value = 0
        
        for inv in investments:
            deal = Deal.query.get(inv.deal_id)
            if deal:
                portfolio_data.append({
                    "id": str(inv.id),
                    "title": deal.title,
                    "type": "property",
                    "amount": inv.amount,
                    "date": inv.created_at.isoformat(),
                    "return": 0,
                    "status": inv.status,
                    "location": deal.location or "N/A",
                    "propertyType": deal.asset_type
                })
                total_value += inv.amount
        
        return jsonify({
            "investments": portfolio_data,
            "totalValue": total_value,
            "totalInvestments": len(portfolio_data),
            "averageReturn": 0
        }), 200
        
    except Exception as e:
        print(f"❌ Portfolio error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/investments', methods=['POST'])
@token_required
def add_investment(current_user):
    try:
        data = request.get_json()
        deal_id = data.get('dealId')
        amount = data.get('amount')
        
        if not deal_id or not amount:
            return jsonify({"error": "Missing dealId or amount"}), 400
        
        deal = Deal.query.get(deal_id)
        if not deal:
            return jsonify({"error": "Deal not found"}), 404
        
        existing = PortfolioInvestment.query.filter_by(
            user_id=current_user.id,
            deal_id=deal_id,
            status='active'
        ).first()
        
        if existing:
            return jsonify({"error": "Already invested in this deal"}), 400
        
        investment = PortfolioInvestment(
            user_id=current_user.id,
            deal_id=deal_id,
            amount=float(amount),
            status='active'
        )
        
        db.session.add(investment)
        db.session.commit()
        
        current_user.investments_completed = (current_user.investments_completed or 0) + 1
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Investment added successfully",
            "investment": {
                "id": str(investment.id),
                "dealId": deal_id,
                "amount": float(amount),
                "date": investment.created_at.isoformat()
            }
        }), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Add investment error: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ===== DEALS ROUTE =====
@app.route('/api/deals', methods=['GET'])
@cache.cached(timeout=1800, key_prefix='all_deals')
def get_deals():
    deals = Deal.query.filter_by(status='open').all()
    return jsonify([{
        'id': d.id, 'title': d.title, 'description': d.description, 
        'asset_type': d.asset_type, 'total_price': d.total_price,
        'min_investment': d.min_investment, 'location': d.location,
        'expected_roi': d.expected_roi, 'sponsor_username': d.sponsor.username
    } for d in deals])

# ===== CREATE TABLES =====
with app.app_context():
    db.create_all()
    print("✅ Database tables created/verified")
    print(f"📊 Using database: {app.config['SQLALCHEMY_DATABASE_URI'][:50]}...")

# ===== RUN APP =====
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        print(f"Starting production server on port {port}")
        socketio.run(app, host='0.0.0.0', port=port, debug=False)
    else:
        socketio.run(app, debug=True, port=port)



