# requirements.txt
# flask, flask-sqlalchemy, flask-bcrypt, flask-cors, flask-socketio, eventlet, pyjwt, python-dotenv, flask-caching, psycopg2-binary

from flask import Flask, request, jsonify
from flask_bcrypt import Bcrypt
from flask_cors import CORS
from flask_socketio import SocketIO
import jwt
from datetime import datetime, timedelta
from functools import wraps
import os
from dotenv import load_dotenv
from flask_caching import Cache
from sqlalchemy import text

# Import db from database.py
from database import db

# Import all models
from models import (
    User, Deal, DealInterest, TrustReview, 
    InvestmentGroup, InvestmentCommitment, InvestmentMilestone,
    PortfolioInvestment, DealChatMessage, DealChatParticipant, ChatMessage
)

# Load environment variables
load_dotenv()

# 1. Create app
app = Flask(__name__)

# 2. Configure app
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-me')

# ===== POSTGRESQL CONNECTION =====
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print("⚠️ DATABASE_URL not found, using SQLite for local development")
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///invest.db'
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    print(f"✅ Using PostgreSQL database")

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 5,
    'pool_recycle': 300,
    'pool_pre_ping': True,
    'pool_timeout': 30,
    'max_overflow': 10,
    'connect_args': {
        'connect_timeout': 10,
        'keepalives': 1,
        'keepalives_idle': 30,
        'keepalives_interval': 10,
        'keepalives_count': 5,
    }
}

app.config['CACHE_TYPE'] = 'SimpleCache'
app.config['CACHE_DEFAULT_TIMEOUT'] = 1800

# 3. Initialize extensions with app
db.init_app(app)
bcrypt = Bcrypt(app)
cache = Cache(app)

# CORS
CORS(app, origins=["http://localhost:3000", "http://localhost:5000", "https://investbook-production.up.railway.app"])

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# 4. Register blueprints
from admin_dashboard import admin_bp
app.register_blueprint(admin_bp, url_prefix='/admin')

from stripe_routes import stripe_bp
app.register_blueprint(stripe_bp, url_prefix='/api')

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
        db.session.execute(text('SELECT 1'))
        db_status = 'connected'
        db_type = 'PostgreSQL'
    except Exception as e:
        db_status = f'error: {str(e)}'
        db_type = 'PostgreSQL (error)'
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'database': db_status,
        'database_type': db_type
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
    try:
        db.create_all()
        print("✅ Database tables created/verified")
        print(f"📊 Using database: PostgreSQL" if os.getenv('DATABASE_URL') else "📊 Using database: SQLite")
    except Exception as e:
        print(f"❌ Database creation error: {str(e)}")

# ===== RUN APP =====
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        print(f"Starting production server on port {port}")
        socketio.run(app, host='0.0.0.0', port=port, debug=False)
    else:
        socketio.run(app, debug=True, port=port)
