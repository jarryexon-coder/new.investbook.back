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

@app.route('/api/subscription-status', methods=['GET'])
@token_required
def get_subscription_status_direct(current_user):
    """Get user's subscription status - Direct endpoint"""
    try:
        is_subscribed = (
            current_user.subscription_plan and 
            current_user.subscription_expiry and 
            current_user.subscription_expiry > datetime.utcnow()
        )
        
        return jsonify({
            'isSubscribed': bool(is_subscribed),
            'planId': current_user.subscription_plan,
            'tier': current_user.subscription_plan,
            'expiryDate': current_user.subscription_expiry.isoformat() if current_user.subscription_expiry else None
        }), 200
        
    except Exception as e:
        print(f"❌ Subscription status error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ===== CUSTOM PAYMENT SUCCESS PAGE =====
@app.route('/payment/success')
def payment_success():
    """Custom payment success page with app redirect"""
    session_id = request.args.get('session_id')
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Payment Successful - InvestBook</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
            }
            .container {
                background: white;
                padding: 48px;
                border-radius: 20px;
                text-align: center;
                max-width: 500px;
                width: 100%;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                animation: slideUp 0.6s ease-out;
            }
            @keyframes slideUp {
                from {
                    opacity: 0;
                    transform: translateY(30px);
                }
                to {
                    opacity: 1;
                    transform: translateY(0);
                }
            }
            .success-icon {
                width: 80px;
                height: 80px;
                background: #10b981;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 20px;
                animation: bounce 0.8s ease-out;
            }
            @keyframes bounce {
                0% { transform: scale(0); }
                50% { transform: scale(1.2); }
                70% { transform: scale(0.9); }
                100% { transform: scale(1); }
            }
            .success-icon svg {
                width: 40px;
                height: 40px;
                fill: white;
            }
            h1 {
                color: #1a1a1a;
                font-size: 28px;
                font-weight: 700;
                margin-bottom: 12px;
            }
            .subtitle {
                color: #666;
                font-size: 16px;
                line-height: 1.6;
                margin-bottom: 24px;
            }
            .details {
                background: #f8f9fa;
                padding: 16px;
                border-radius: 12px;
                margin: 16px 0 24px;
                text-align: left;
                font-size: 14px;
                color: #555;
            }
            .details-row {
                display: flex;
                justify-content: space-between;
                padding: 4px 0;
            }
            .details-row span:first-child {
                font-weight: 500;
                color: #1a1a1a;
            }
            .details-row span:last-child {
                color: #10b981;
                font-weight: 600;
            }
            .button-group {
                display: flex;
                gap: 12px;
                flex-wrap: wrap;
                justify-content: center;
            }
            .btn-primary {
                display: inline-block;
                background: #2563eb;
                color: white;
                padding: 14px 32px;
                border-radius: 12px;
                text-decoration: none;
                font-weight: 600;
                font-size: 16px;
                transition: all 0.2s;
                flex: 1;
                min-width: 140px;
                border: none;
                cursor: pointer;
            }
            .btn-primary:hover {
                background: #1d4ed8;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
            }
            .btn-secondary {
                display: inline-block;
                background: #f3f4f6;
                color: #1a1a1a;
                padding: 14px 32px;
                border-radius: 12px;
                text-decoration: none;
                font-weight: 600;
                font-size: 16px;
                transition: all 0.2s;
                flex: 1;
                min-width: 140px;
                border: none;
                cursor: pointer;
            }
            .btn-secondary:hover {
                background: #e5e7eb;
            }
            .features {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 8px;
                margin: 16px 0;
                text-align: left;
            }
            .feature-item {
                display: flex;
                align-items: center;
                gap: 8px;
                font-size: 14px;
                color: #333;
            }
            .feature-item::before {
                content: "✅";
                font-size: 14px;
            }
            .footer {
                margin-top: 24px;
                padding-top: 16px;
                border-top: 1px solid #e5e5e5;
                font-size: 12px;
                color: #999;
            }
            @media (max-width: 480px) {
                .container {
                    padding: 32px 20px;
                }
                .features {
                    grid-template-columns: 1fr;
                }
                .button-group {
                    flex-direction: column;
                }
                .btn-primary, .btn-secondary {
                    width: 100%;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="success-icon">
                <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
            </div>
            <h1>Welcome to InvestBook! 🎉</h1>
            <p class="subtitle">Your subscription is now active. You have full access to all premium features.</p>
            
            <div class="details">
                <div class="details-row">
                    <span>Status</span>
                    <span>✅ Active</span>
                </div>
                <div class="details-row">
                    <span>Session ID</span>
                    <span style="font-size:12px;word-break:break-all;">''' + str(session_id) + '''</span>
                </div>
            </div>
            
            <div class="features">
                <div class="feature-item">Unlimited property views</div>
                <div class="feature-item">Under $200k deals</div>
                <div class="feature-item">Real-time notifications</div>
                <div class="feature-item">Deal chat</div>
            </div>
            
            <div class="button-group">
                <a href="https://investbook-production.up.railway.app" class="btn-primary">Go to Dashboard</a>
                <a href="https://investbook-production.up.railway.app" class="btn-secondary">Explore Deals</a>
            </div>
            
            <div class="footer">
                Questions? Contact us at <a href="mailto:support@investbook.com" style="color:#2563eb;text-decoration:none;">support@investbook.com</a>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/payment/cancel')
def payment_cancel():
    """Custom payment cancel page"""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Payment Canceled - InvestBook</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
                padding: 20px;
            }
            .container {
                background: white;
                padding: 48px;
                border-radius: 20px;
                text-align: center;
                max-width: 500px;
                width: 100%;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                animation: slideUp 0.6s ease-out;
            }
            @keyframes slideUp {
                from { opacity: 0; transform: translateY(30px); }
                to { opacity: 1; transform: translateY(0); }
            }
            .icon {
                font-size: 72px;
                margin-bottom: 16px;
            }
            h1 {
                color: #1a1a1a;
                font-size: 28px;
                margin-bottom: 12px;
            }
            p {
                color: #666;
                font-size: 16px;
                line-height: 1.6;
                margin-bottom: 8px;
            }
            .btn-primary {
                display: inline-block;
                background: #2563eb;
                color: white;
                padding: 14px 32px;
                border-radius: 12px;
                text-decoration: none;
                font-weight: 600;
                font-size: 16px;
                transition: all 0.2s;
                border: none;
                cursor: pointer;
                margin-top: 16px;
            }
            .btn-primary:hover {
                background: #1d4ed8;
                transform: translateY(-2px);
            }
            .footer {
                margin-top: 24px;
                padding-top: 16px;
                border-top: 1px solid #e5e5e5;
                font-size: 12px;
                color: #999;
            }
            .footer a {
                color: #2563eb;
                text-decoration: none;
            }
            @media (max-width: 480px) {
                .container { padding: 32px 20px; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="icon">↩️</div>
            <h1>Payment Canceled</h1>
            <p>You canceled the payment process. No charges were made to your account.</p>
            <p style="margin-top:8px;">You can try again whenever you're ready to subscribe.</p>
            <a href="https://investbook-production.up.railway.app" class="btn-primary">Return to InvestBook</a>
            <div class="footer">
                Questions? <a href="mailto:support@investbook.com">support@investbook.com</a>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/payment/error')
def payment_error():
    """Payment error page"""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Error - InvestBook</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%);
            }
            .container {
                background: white;
                padding: 48px;
                border-radius: 16px;
                text-align: center;
                max-width: 500px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            }
            .icon {
                font-size: 72px;
                margin-bottom: 16px;
            }
            h1 {
                color: #1a1a1a;
                font-size: 28px;
                margin-bottom: 12px;
            }
            p {
                color: #666;
                font-size: 16px;
                line-height: 1.6;
                margin-bottom: 8px;
            }
            .error-detail {
                background: #fee2e2;
                padding: 12px;
                border-radius: 8px;
                color: #dc2626;
                font-size: 14px;
                margin: 16px 0;
            }
            .button {
                display: inline-block;
                background: #2563eb;
                color: white;
                padding: 12px 32px;
                border-radius: 8px;
                text-decoration: none;
                font-weight: 600;
                transition: background 0.2s;
            }
            .button:hover {
                background: #1d4ed8;
            }
            .support {
                margin-top: 16px;
                font-size: 14px;
                color: #666;
            }
            .support a {
                color: #2563eb;
                text-decoration: none;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="icon">❌</div>
            <h1>Payment Error</h1>
            <p>Something went wrong while processing your payment.</p>
            <div class="error-detail">
                We encountered an issue. Please try again or contact support.
            </div>
            <a href="https://investbook-production.up.railway.app" class="button">Try Again</a>
            <div class="support">
                Need help? <a href="mailto:support@investbook.com">Contact Support</a>
            </div>
        </div>
    </body>
    </html>
    '''

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

@app.route('/api/deals/ensure/<int:listing_id>', methods=['POST'])
@token_required
def ensure_deal_for_listing(current_user, listing_id):
    """Create a deal from a listing if it doesn't exist"""
    try:
        # Check if deal exists
        deal = Deal.query.filter_by(id=listing_id).first()
        
        if not deal:
            # Try to find by propertyId or external ID
            deal = Deal.query.filter_by(title=f"Property {listing_id}").first()
        
        if not deal:
            # Create a new deal from the listing
            data = request.get_json() or {}
            deal = Deal(
                id=listing_id,
                title=data.get('title', f'Property {listing_id}'),
                description=data.get('description', 'Listing from LoopNet'),
                asset_type=data.get('propertyType', 'Commercial'),
                total_price=float(data.get('price', 0)) or 1.0,
                min_investment=1.0,
                location=data.get('location', ''),
                expected_roi='10%',
                status='open',
                sponsor_id=current_user.id
            )
            db.session.add(deal)
            db.session.commit()
            print(f"✅ Created deal for listing {listing_id}")
        
        return jsonify({
            'success': True,
            'deal_id': deal.id,
            'message': 'Deal ready for chat'
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Ensure deal error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ===== CREATE DEAL ENDPOINT =====
@app.route('/api/deals', methods=['POST'])
@token_required
def create_deal(current_user):
    """Create a new deal"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['title', 'description', 'asset_type', 'total_price', 'min_investment']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Create the deal
        deal = Deal(
            title=data['title'],
            description=data['description'],
            asset_type=data['asset_type'],
            total_price=float(data['total_price']),
            min_investment=float(data['min_investment']),
            location=data.get('location', ''),
            expected_roi=data.get('expected_roi', ''),
            status='open',
            sponsor_id=current_user.id
        )
        
        db.session.add(deal)
        db.session.commit()
        
        # Clear cache
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
                'status': deal.status
            }
        }), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Create deal error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ===== GET SINGLE DEAL =====
@app.route('/api/deals/<int:deal_id>', methods=['GET'])
@token_required
def get_deal(current_user, deal_id):
    """Get a specific deal"""
    try:
        deal = Deal.query.get(deal_id)
        if not deal:
            return jsonify({'error': 'Deal not found'}), 404
        
        return jsonify({
            'id': deal.id,
            'title': deal.title,
            'description': deal.description,
            'asset_type': deal.asset_type,
            'total_price': deal.total_price,
            'min_investment': deal.min_investment,
            'location': deal.location,
            'expected_roi': deal.expected_roi,
            'status': deal.status,
            'sponsor_id': deal.sponsor_id,
            'sponsor_username': deal.sponsor.username if deal.sponsor else None,
            'created_at': deal.created_at.isoformat()
        }), 200
        
    except Exception as e:
        print(f"❌ Get deal error: {str(e)}")
        return jsonify({'error': str(e)}), 500

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

# ===== CHAT ENDPOINTS =====

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
        
        # Get messages (not deleted)
        messages = DealChatMessage.query.filter_by(
            deal_id=deal_id,
            is_deleted=False
        ).order_by(DealChatMessage.created_at.asc()).all()
        
        return jsonify([{
            'id': m.id,
            'user_id': m.user_id,
            'username': m.user.username,
            'message': m.message,
            'created_at': m.created_at.isoformat(),
            'is_edited': m.is_edited,
            'is_system': m.user_id == 0
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
        
        return jsonify({
            'id': message.id,
            'user_id': message.user_id,
            'username': current_user.username,
            'message': message.message,
            'created_at': message.created_at.isoformat(),
            'is_edited': message.is_edited,
            'participant_count': participant_count
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
        
        return jsonify({'message': 'Message deleted'}), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Delete message error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ===== TEST IMAGE ENDPOINT =====
@app.route('/api/test-image/<int:index>')
def test_image(index):
    """Test if an image is being served correctly"""
    from flask import send_from_directory
    import os
    
    image_files = [f for f in os.listdir('images') if f.endswith(('.jpg', '.png', '.webp', '.jpeg'))]
    if index < len(image_files):
        return send_from_directory('images', image_files[index])
    return {"error": "Image not found"}, 404

@app.route('/api/image-count')
def image_count():
    """Get count of available images"""
    import os
    image_dir = 'images'
    if os.path.exists(image_dir):
        image_files = [f for f in os.listdir(image_dir) if f.endswith(('.jpg', '.png', '.webp', '.jpeg'))]
        return jsonify({
            'count': len(image_files),
            'mapping_count': len(json.load(open('image_mapping.json'))) if os.path.exists('image_mapping.json') else 0
        })
    return jsonify({'count': 0})

@app.route('/image_mapping.json')
def serve_image_mapping():
    """Serve the image mapping JSON file"""
    import json
    import os
    
    mapping_file = 'image_mapping.json'
    if os.path.exists(mapping_file):
        with open(mapping_file, 'r') as f:
            data = json.load(f)
        return jsonify(data)
    return jsonify({'error': 'Mapping file not found'}), 404

@app.route('/api/image-mapping')
def get_image_mapping():
    """Serve the image mapping JSON"""
    import json
    import os
    
    mapping_file = 'image_mapping.json'
    if os.path.exists(mapping_file):
        with open(mapping_file, 'r') as f:
            data = json.load(f)
        return jsonify(data)
    return jsonify({'error': 'Mapping file not found'}), 404

@app.route('/api/images/count')
def get_image_count():
    """Get count of available images and mapping"""
    import json
    import os
    
    image_dir = 'images'
    image_count = 0
    mapping_count = 0
    
    if os.path.exists(image_dir):
        image_count = len([f for f in os.listdir(image_dir) if f.endswith(('.jpg', '.png', '.webp', '.jpeg'))])
    
    mapping_file = 'image_mapping.json'
    if os.path.exists(mapping_file):
        with open(mapping_file, 'r') as f:
            mapping_count = len(json.load(f))
    
    return jsonify({
        'images': image_count,
        'mapped': mapping_count,
        'status': 'ok'
    })

@app.route('/api/debug/images')
def debug_images():
    """Debug endpoint to check images directory"""
    import os
    import json
    
    image_dir = '/app/images'
    result = {
        'image_dir_exists': os.path.exists(image_dir),
        'image_dir_path': image_dir,
        'files': [],
        'count': 0
    }
    
    if os.path.exists(image_dir):
        try:
            files = os.listdir(image_dir)
            result['count'] = len(files)
            result['files'] = files[:10]  # First 10 files
        except Exception as e:
            result['error'] = str(e)
    
    return jsonify(result)

@app.route('/api/health/images')
def health_images():
    """Simple health check for images"""
    import os
    image_dir = '/app/images'
    exists = os.path.exists(image_dir)
    count = len(os.listdir(image_dir)) if exists else 0
    return jsonify({
        'status': 'ok',
        'image_directory_exists': exists,
        'image_count': count
    })

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
