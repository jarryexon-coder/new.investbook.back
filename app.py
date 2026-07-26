# requirements.txt
# flask, flask-sqlalchemy, flask-bcrypt, flask-cors, flask-socketio, eventlet, pyjwt, python-dotenv, flask-caching

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

# 2. Configure app
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///invest.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ✅ Add caching configuration
app.config['CACHE_TYPE'] = 'SimpleCache'
app.config['CACHE_DEFAULT_TIMEOUT'] = 1800  # 30 minutes cache

# 3. Initialize extensions
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
cache = Cache(app)  # Initialize cache

# ✅ Simple CORS - Allow all
CORS(app, origins=["http://localhost:3000", "http://localhost:5000", "https://investbook-production.up.railway.app"])

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ✅ Create tables on startup
with app.app_context():
    db.create_all()
    print("✅ Database tables created/verified")

# 4. Import and register blueprints AFTER app is created
from admin_dashboard import admin_bp
app.register_blueprint(admin_bp, url_prefix='/admin')

# Import and register Stripe blueprint AFTER app is created
from stripe_routes import stripe_bp
app.register_blueprint(stripe_bp, url_prefix='/api')

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found', 'message': 'The requested URL was not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error', 'message': str(error)}), 500

# --- Routes ---
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
            'group_status': '/api/groups/<id>/status [GET]',
            'portfolio': '/api/portfolio [GET]',
            'investments': '/api/investments [POST]'
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

class PortfolioInvestment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'))
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = db.relationship('User', backref='portfolio_investments')
    deal = db.relationship('Deal', backref='portfolio_investments')

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    read = db.Column(db.Boolean, default=False)
    
    user = db.relationship('User', backref='chat_messages')
    deal = db.relationship('Deal', backref='chat_messages')

# ===== ENHANCED CHAT MODELS =====

class DealChatMessage(db.Model):
    """Chat messages for deal-specific conversations"""
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_edited = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    
    user = db.relationship('User', backref='deal_chat_messages')
    deal = db.relationship('Deal', backref='chat_messages_all')

class DealChatParticipant(db.Model):
    """Users participating in deal chats"""
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_read_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_muted = db.Column(db.Boolean, default=False)
    
    user = db.relationship('User', backref='deal_chat_participants')
    deal = db.relationship('Deal', backref='chat_participants')

# Create new tables
with app.app_context():
    db.create_all()
    print("✅ DealChat tables created/verified")

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

# ===== SUBSCRIPTION CHECK DECORATOR =====

def subscription_required(f):
    """Decorator to check if user has an active subscription"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Get current user from token_required first
        # This assumes token_required is used before subscription_required
        current_user = kwargs.get('current_user')
        
        if not current_user:
            return jsonify({'error': 'User not authenticated'}), 401
        
        # Check if user has active subscription
        is_subscribed = (
            current_user.subscription_plan and 
            current_user.subscription_expiry and 
            current_user.subscription_expiry > datetime.utcnow()
        )
        
        # For test account, allow access (or check subscription)
        # Remove this in production!
        if current_user.username == 'testuser':
            print(f"🔓 Test user {current_user.username} bypassing subscription check")
            return f(*args, **kwargs)
        
        if not is_subscribed:
            return jsonify({
                'error': 'Subscription required',
                'message': 'Please subscribe to access this feature',
                'requires_subscription': True
            }), 403
        
        return f(*args, **kwargs)
    return decorated

# ===== PORTFOLIO ENDPOINTS =====

@app.route('/api/portfolio', methods=['GET'])
@token_required
def get_portfolio(current_user):
    """Get user's portfolio investments"""
    try:
        # Get user's investments from database
        investments = PortfolioInvestment.query.filter_by(
            user_id=current_user.id,
            status='active'
        ).all()
        
        if not investments:
            # Return sample data for new users
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
        
        # Format real investments
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
                    "return": 0,  # Calculate actual return if available
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

# ===== PROTECTED ROUTES =====

@app.route('/api/opportunities', methods=['GET'])
@token_required
@subscription_required
def get_opportunities(current_user):
    """Get opportunities - requires subscription"""
    try:
        # Return cached opportunities
        from scraper_service import get_cached_opportunities
        data = get_cached_opportunities()
        return jsonify({
            'success': True,
            'data': data,
            'requires_subscription': True
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/under200k', methods=['GET'])
@token_required
@subscription_required
def get_under200k(current_user):
    """Get under $200k listings - requires subscription"""
    try:
        # Return cached under 200k data
        from scraper_service import get_under200k_listings
        data = get_under200k_listings()
        return jsonify({
            'success': True,
            'data': data,
            'requires_subscription': True
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/investments', methods=['POST'])
@token_required
def add_investment(current_user):
    """Add an investment to user's portfolio"""
    try:
        data = request.get_json()
        deal_id = data.get('dealId')
        amount = data.get('amount')
        
        if not deal_id or not amount:
            return jsonify({"error": "Missing dealId or amount"}), 400
        
        # Validate deal exists
        deal = Deal.query.get(deal_id)
        if not deal:
            return jsonify({"error": "Deal not found"}), 404
        
        # Check if user already invested in this deal
        existing = PortfolioInvestment.query.filter_by(
            user_id=current_user.id,
            deal_id=deal_id,
            status='active'
        ).first()
        
        if existing:
            return jsonify({"error": "Already invested in this deal"}), 400
        
        # Create investment
        investment = PortfolioInvestment(
            user_id=current_user.id,
            deal_id=deal_id,
            amount=float(amount),
            status='active'
        )
        
        db.session.add(investment)
        db.session.commit()
        
        # Update user's investments count
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

# ===== SUBSCRIPTION ENDPOINTS =====

@app.route('/api/subscriptions/create-payment-intent', methods=['POST'])
@token_required
def create_payment_intent(current_user):
    """Create Stripe payment intent for subscription"""
    try:
        data = request.get_json()
        plan_id = data.get('planId', 'monthly')
        
        # Get price based on plan
        prices = {
            'monthly': 499,  # $4.99 in cents
            'yearly': 4999,  # $49.99 in cents
        }
        
        amount = prices.get(plan_id, 499)
        
        # Return payment intent data (in production, this would call Stripe)
        return jsonify({
            "clientSecret": "pi_1234567890_secret_1234567890",
            "amount": amount,
            "currency": "usd",
            "planId": plan_id
        }), 200
        
    except Exception as e:
        print(f"❌ Payment intent error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/subscriptions/activate', methods=['POST'])
@token_required
def activate_subscription(current_user):
    """Activate user's subscription"""
    try:
        data = request.get_json()
        plan_id = data.get('planId', 'monthly')
        
        # Set subscription expiry (30 days for monthly, 365 for yearly)
        days = 30 if plan_id == 'monthly' else 365
        expiry = datetime.utcnow() + timedelta(days=days)
        
        current_user.subscription_plan = plan_id
        current_user.subscription_expiry = expiry
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"Subscription activated for {plan_id} plan",
            "expiry": expiry.isoformat()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Activate subscription error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/subscriptions/cancel', methods=['POST'])
@token_required
def cancel_subscription(current_user):
    """Cancel user's subscription"""
    try:
        current_user.subscription_plan = None
        current_user.subscription_expiry = None
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Subscription canceled successfully"
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Cancel subscription error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/subscriptions/status', methods=['GET'])
@token_required
def get_subscription_status(current_user):
    """Get user's subscription status"""
    try:
        is_subscribed = (
            current_user.subscription_plan and 
            current_user.subscription_expiry and 
            current_user.subscription_expiry > datetime.utcnow()
        )
        
        return jsonify({
            "isSubscribed": bool(is_subscribed),
            "planId": current_user.subscription_plan,
            "expiryDate": current_user.subscription_expiry.isoformat() if current_user.subscription_expiry else None
        }), 200
        
    except Exception as e:
        print(f"❌ Subscription status error: {str(e)}")
        return jsonify({"error": str(e)}), 500

# --- API Routes ---
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

@app.route('/api/refresh-listings', methods=['POST'])
def refresh_listings():
    try:
        data = request.get_json()
        print(f"📩 Webhook received at {datetime.utcnow().isoformat()}")
        print(f"📦 Data: {data}")
        
        run_id = data.get('runId', 'unknown')
        dataset_id = data.get('datasetId', 'unknown')
        status = data.get('status', 'unknown')
        
        item_count = data.get('itemCount')
        if item_count is None:
            item_count = data.get('totalItems', 0)
        
        print(f"✅ Run {run_id} completed with status: {status}")
        print(f"📊 Items collected: {item_count}")
        
        cache.delete('all_business_listings')
        cache.delete('all_deals')
        print("🗑️ Cache cleared for all_business_listings and all_deals")
        
        cache.set('last_apify_run', {
            'runId': run_id,
            'datasetId': dataset_id,
            'itemCount': item_count,
            'status': status,
            'timestamp': datetime.utcnow().isoformat()
        }, timeout=86400)
        
        return jsonify({
            'status': 'success',
            'message': 'Cache cleared, new data will be fetched',
            'run_id': run_id,
            'dataset_id': dataset_id,
            'item_count': item_count
        }), 200
    except Exception as e:
        print(f"❌ Webhook error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/cache-status', methods=['GET'])
def cache_status():
    last_run = cache.get('last_apify_run')
    return jsonify({
        'last_apify_run': last_run,
        'cache_health': 'healthy',
        'cache_keys': ['all_deals', 'all_business_listings', 'last_apify_run']
    })

@app.route('/api/cache/businesses', methods=['GET'])
def get_cached_businesses():
    data = cache.get('all_business_listings')
    if data:
        return jsonify({
            'status': 'success',
            'count': len(data),
            'data': data
        })
    return jsonify({
        'status': 'empty',
        'message': 'No cached data found'
    }), 404

@app.route('/api/cache/load', methods=['POST'])
def load_cached_data():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Missing data'}), 400
        businesses = data if isinstance(data, list) else data.get('businesses', [])
        if not businesses:
            return jsonify({'error': 'No businesses found in data'}), 400
        cache.set('all_business_listings', businesses, timeout=86400)
        print(f"✅ Manually loaded {len(businesses)} businesses into cache")
        return jsonify({
            'status': 'success',
            'message': f'Loaded {len(businesses)} businesses into cache',
            'count': len(businesses)
        }), 200
    except Exception as e:
        print(f"❌ Error loading cache: {str(e)}")
        return jsonify({'error': str(e)}), 500

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
        
        cache.delete('all_deals')
        
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

# --- Chat Routes ---
@app.route('/api/deals/<int:deal_id>/messages', methods=['GET'])
@token_required
def get_chat_messages(current_user, deal_id):
    try:
        deal = Deal.query.get(deal_id)
        if not deal:
            return jsonify({'error': 'Deal not found'}), 404

        if deal.sponsor_id != current_user.id:
            interest = DealInterest.query.filter_by(
                deal_id=deal_id,
                user_id=current_user.id
            ).first()
            if not interest:
                return jsonify({'error': 'Access denied'}), 403

        messages = ChatMessage.query.filter_by(deal_id=deal_id).order_by(ChatMessage.created_at.asc()).all()

        return jsonify([{
            'id': m.id,
            'user_id': m.user_id,
            'username': m.user.username,
            'message': m.message,
            'created_at': m.created_at.isoformat(),
            'read': m.read
        } for m in messages]), 200
    except Exception as e:
        print(f"Error getting messages: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/deals/<int:deal_id>/messages', methods=['POST'])
@token_required
def send_chat_message(current_user, deal_id):
    try:
        data = request.json
        message_text = data.get('message')
        
        if not message_text:
            return jsonify({'error': 'Message is required'}), 400
        
        deal = Deal.query.get(deal_id)
        if not deal:
            print(f"❌ Deal not found: {deal_id}")
            return jsonify({'error': 'Deal not found'}), 404
        
        if deal.sponsor_id != current_user.id:
            interest = DealInterest.query.filter_by(
                deal_id=deal_id, 
                user_id=current_user.id
            ).first()
            if not interest:
                return jsonify({'error': 'Access denied'}), 403
        
        message = ChatMessage(
            deal_id=deal_id,
            user_id=current_user.id,
            message=message_text
        )
        db.session.add(message)
        db.session.commit()
        
        print(f"✅ Message saved for deal {deal_id}")
        
        socketio.emit('new_message', {
            'deal_id': deal_id,
            'message': {
                'id': message.id,
                'user_id': message.user_id,
                'username': current_user.username,
                'message': message.message,
                'created_at': message.created_at.isoformat(),
                'read': message.read
            }
        }, room=f'deal_{deal_id}')
        
        return jsonify({
            'id': message.id,
            'user_id': message.user_id,
            'username': current_user.username,
            'message': message.message,
            'created_at': message.created_at.isoformat(),
            'read': message.read
        }), 201
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error sending message: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/deals/<int:deal_id>/messages/<int:message_id>/read', methods=['PUT'])
@token_required
def mark_message_read(current_user, deal_id, message_id):
    try:
        message = ChatMessage.query.get(message_id)
        if not message:
            return jsonify({'error': 'Message not found'}), 404
        
        if message.deal_id != deal_id:
            return jsonify({'error': 'Message does not belong to this deal'}), 400
        
        if message.user_id == current_user.id:
            return jsonify({'error': 'Cannot mark own message as read'}), 400
        
        message.read = True
        db.session.commit()
        
        return jsonify({'message': 'Message marked as read'}), 200
    except Exception as e:
        print(f"Error marking message read: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/deals/sync', methods=['POST'])
@token_required
def sync_deal(current_user):
    try:
        data = request.get_json()
        deal_id = data.get('dealId')
        deal_data = data.get('dealData', {})
        
        print(f"🔄 Syncing deal: {deal_id}")
        print(f"📦 Deal data: {deal_data}")
        
        if not deal_id:
            return jsonify({'error': 'Deal ID is required'}), 400
        
        title = deal_data.get('title', '')
        existing_deal = None
        
        if title:
            existing_deal = Deal.query.filter_by(title=title).first()
        
        if not existing_deal:
            price = deal_data.get('price', 0)
            if isinstance(price, str):
                price = float(''.join(filter(str.isdigit, price))) if price else 0
            
            deal = Deal(
                title=title[:200] if title else f'Property {deal_id}',
                description=deal_data.get('description', '')[:500],
                asset_type=deal_data.get('propertyType', 'Commercial') or 'Commercial',
                total_price=float(price) if price else 0,
                min_investment=float(price) / 2 if price else 0,
                location=deal_data.get('location', '')[:200],
                expected_roi='10%',
                status='open',
                sponsor_id=current_user.id
            )
            db.session.add(deal)
            db.session.commit()
            print(f"✅ Created new deal: {deal.title} (ID: {deal.id})")
        else:
            print(f"📌 Deal already exists: {existing_deal.title} (ID: {existing_deal.id})")
            deal = existing_deal
        
        return jsonify({
            'success': True,
            'deal': {
                'id': deal.id,
                'title': deal.title,
                'description': deal.description
            }
        }), 200
    except Exception as e:
        print(f"❌ Error syncing deal: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/debug/token', methods=['GET'])
@token_required
def debug_token(current_user):
    return jsonify({
        'authenticated': True,
        'user_id': current_user.id,
        'username': current_user.username,
        'email': current_user.email
    })

# ===== ENHANCED CHAT ROUTES =====

@app.route('/api/deals/<int:deal_id>/chat/join', methods=['POST'])
@token_required
def join_deal_chat(current_user, deal_id):
    """Join a deal chat room"""
    try:
        # Check if deal exists
        deal = Deal.query.get(deal_id)
        if not deal:
            return jsonify({'error': 'Deal not found'}), 404
        
        # Check if user is already a participant
        existing = DealChatParticipant.query.filter_by(
            deal_id=deal_id,
            user_id=current_user.id
        ).first()
        
        if existing:
            return jsonify({'message': 'Already in chat'}), 200
        
        # Add user as participant
        participant = DealChatParticipant(
            deal_id=deal_id,
            user_id=current_user.id
        )
        db.session.add(participant)
        db.session.commit()
        
        # Send system message
        system_message = DealChatMessage(
            deal_id=deal_id,
            user_id=current_user.id,
            message=f"{current_user.username} joined the chat"
        )
        db.session.add(system_message)
        db.session.commit()
        
        # Emit via WebSocket
        socketio.emit('user_joined', {
            'deal_id': deal_id,
            'user': {
                'id': current_user.id,
                'username': current_user.username
            }
        }, room=f'deal_chat_{deal_id}')
        
        return jsonify({
            'message': 'Joined chat successfully',
            'participant': {
                'user_id': current_user.id,
                'username': current_user.username,
                'joined_at': participant.joined_at.isoformat()
            }
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Join chat error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/deals/<int:deal_id>/chat/participants', methods=['GET'])
@token_required
def get_chat_participants(current_user, deal_id):
    """Get all participants in a deal chat"""
    try:
        participants = DealChatParticipant.query.filter_by(
            deal_id=deal_id
        ).all()
        
        return jsonify([{
            'user_id': p.user_id,
            'username': p.user.username,
            'joined_at': p.joined_at.isoformat(),
            'last_read_at': p.last_read_at.isoformat()
        } for p in participants]), 200
        
    except Exception as e:
        print(f"❌ Get participants error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/deals/<int:deal_id>/chat/messages', methods=['GET'])
@token_required
def get_deal_chat_messages(current_user, deal_id):
    """Get all messages for a deal chat"""
    try:
        # Check if deal exists
        deal = Deal.query.get(deal_id)
        if not deal:
            return jsonify({'error': 'Deal not found'}), 404
        
        # Check if user is a participant
        participant = DealChatParticipant.query.filter_by(
            deal_id=deal_id,
            user_id=current_user.id
        ).first()
        
        if not participant:
            # Auto-join if not a participant
            participant = DealChatParticipant(
                deal_id=deal_id,
                user_id=current_user.id
            )
            db.session.add(participant)
            db.session.commit()
        
        # Get messages (not deleted)
        messages = DealChatMessage.query.filter_by(
            deal_id=deal_id,
            is_deleted=False
        ).order_by(DealChatMessage.created_at.asc()).all()
        
        # Update last_read_at
        participant.last_read_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify([{
            'id': m.id,
            'user_id': m.user_id,
            'username': m.user.username,
            'message': m.message,
            'created_at': m.created_at.isoformat(),
            'is_edited': m.is_edited,
            'is_system': m.user_id == 0  # System messages
        } for m in messages]), 200
        
    except Exception as e:
        print(f"❌ Get chat messages error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/deals/<int:deal_id>/chat/messages', methods=['POST'])
@token_required
def send_deal_chat_message(current_user, deal_id):
    """Send a message to a deal chat"""
    try:
        data = request.json
        message_text = data.get('message')
        
        if not message_text or not message_text.strip():
            return jsonify({'error': 'Message is required'}), 400
        
        # Check if deal exists
        deal = Deal.query.get(deal_id)
        if not deal:
            return jsonify({'error': 'Deal not found'}), 404
        
        # Ensure user is a participant
        participant = DealChatParticipant.query.filter_by(
            deal_id=deal_id,
            user_id=current_user.id
        ).first()
        
        if not participant:
            # Auto-join
            participant = DealChatParticipant(
                deal_id=deal_id,
                user_id=current_user.id
            )
            db.session.add(participant)
            db.session.commit()
        
        # Save message
        message = DealChatMessage(
            deal_id=deal_id,
            user_id=current_user.id,
            message=message_text.strip()
        )
        db.session.add(message)
        db.session.commit()
        
        # Update participant's last_read
        participant.last_read_at = datetime.utcnow()
        db.session.commit()
        
        # Get participant count
        participant_count = DealChatParticipant.query.filter_by(
            deal_id=deal_id
        ).count()
        
        # Emit via WebSocket
        socketio.emit('new_chat_message', {
            'deal_id': deal_id,
            'message': {
                'id': message.id,
                'user_id': message.user_id,
                'username': current_user.username,
                'message': message.message,
                'created_at': message.created_at.isoformat(),
                'is_edited': message.is_edited,
                'participant_count': participant_count
            }
        }, room=f'deal_chat_{deal_id}')
        
        return jsonify({
            'id': message.id,
            'user_id': message.user_id,
            'username': current_user.username,
            'message': message.message,
            'created_at': message.created_at.isoformat(),
            'is_edited': message.is_edited
        }), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Send message error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/deals/<int:deal_id>/chat/messages/<int:message_id>', methods=['DELETE'])
@token_required
def delete_deal_chat_message(current_user, deal_id, message_id):
    """Delete a message (soft delete)"""
    try:
        message = DealChatMessage.query.get(message_id)
        if not message:
            return jsonify({'error': 'Message not found'}), 404
        
        # Only the author can delete
        if message.user_id != current_user.id:
            return jsonify({'error': 'Unauthorized'}), 403
        
        message.is_deleted = True
        db.session.commit()
        
        socketio.emit('message_deleted', {
            'deal_id': deal_id,
            'message_id': message_id
        }, room=f'deal_chat_{deal_id}')
        
        return jsonify({'message': 'Message deleted'}), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Delete message error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ===== WEBSOCKET EVENTS FOR DEAL CHAT =====

@socketio.on('join_deal_chat_room')
def handle_join_deal_chat_room(data):
    """Join a deal chat room via WebSocket"""
    deal_id = data.get('deal_id')
    user_id = data.get('user_id')
    
    if deal_id and user_id:
        room = f'deal_chat_{deal_id}'
        join_room(room)
        print(f"👤 User {user_id} joined chat room for deal {deal_id}")
        
        # Send current participant count
        participant_count = DealChatParticipant.query.filter_by(
            deal_id=deal_id
        ).count()
        emit('participant_count', {
            'deal_id': deal_id,
            'count': participant_count
        }, room=room)

@socketio.on('leave_deal_chat_room')
def handle_leave_deal_chat_room(data):
    """Leave a deal chat room via WebSocket"""
    deal_id = data.get('deal_id')
    user_id = data.get('user_id')
    
    if deal_id and user_id:
        room = f'deal_chat_{deal_id}'
        leave_room(room)
        print(f"👤 User {user_id} left chat room for deal {deal_id}")

@socketio.on('deal_chat_typing')
def handle_deal_chat_typing(data):
    """Handle typing indicator"""
    deal_id = data.get('deal_id')
    username = data.get('username')
    
    if deal_id and username:
        room = f'deal_chat_{deal_id}'
        emit('user_typing', {
            'deal_id': deal_id,
            'username': username
        }, room=room, skip_sid=request.sid)

# --- WebSocket for Real-Time Chat ---
@socketio.on('join_deal_chat')
def handle_join_deal_chat(data):
    deal_id = data.get('deal_id')
    if deal_id:
        join_room(f'deal_{deal_id}')
        emit('message', {'system': f"User joined deal {deal_id} chat"}, room=f'deal_{deal_id}')

@socketio.on('leave_deal_chat')
def handle_leave_deal_chat(data):
    deal_id = data.get('deal_id')
    if deal_id:
        leave_room(f'deal_{deal_id}')
        emit('message', {'system': "User left chat"}, room=f'deal_{deal_id}')

@socketio.on('deal_chat_message')
def handle_deal_chat_message(data):
    deal_id = data.get('deal_id')
    message = data.get('message')
    username = data.get('username', 'Anonymous')
    
    if deal_id and message:
        emit('message', {
            'user': username,
            'message': message,
            'timestamp': datetime.utcnow().isoformat()
        }, room=f"deal_{deal_id}")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        print("✅ Database tables created/verified")
    
    port = int(os.environ.get('PORT', 5000))
    
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        print(f"Starting production server on port {port}")
        socketio.run(app, host='0.0.0.0', port=port, debug=False)
    else:
        socketio.run(app, debug=True, port=port)
