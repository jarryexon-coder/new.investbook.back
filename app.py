# requirements.txt
# flask, flask-sqlalchemy, flask-bcrypt, flask-cors, flask-socketio, eventlet, pyjwt, python-dotenv, flask-caching

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
from flask_caching import Cache

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

# --- Chat Models ---
class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    read = db.Column(db.Boolean, default=False)
    
    user = db.relationship('User', backref='chat_messages')
    deal = db.relationship('Deal', backref='chat_messages')

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

@app.route('/api/refresh-listings', methods=['POST'])
def refresh_listings():
    """Endpoint for Apify webhook to notify when data is refreshed"""
    try:
        data = request.get_json()
        print(f"📩 Webhook received at {datetime.utcnow().isoformat()}")
        print(f"📦 Data: {data}")
        
        # Extract run information - handle both field names
        run_id = data.get('runId', 'unknown')
        dataset_id = data.get('datasetId', 'unknown')
        status = data.get('status', 'unknown')
        
        # Try both field names for item count
        item_count = data.get('itemCount')
        if item_count is None:
            item_count = data.get('totalItems', 0)
        
        print(f"✅ Run {run_id} completed with status: {status}")
        print(f"📊 Items collected: {item_count}")
        
        # Clear the cache
        cache.delete('all_business_listings')
        cache.delete('all_deals')
        print("🗑️ Cache cleared for all_business_listings and all_deals")
        
        # Store the latest run info
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

# --- Cache endpoints ---
@app.route('/api/cache-status', methods=['GET'])
def cache_status():
    """Check cache status"""
    last_run = cache.get('last_apify_run')
    return jsonify({
        'last_apify_run': last_run,
        'cache_health': 'healthy',
        'cache_keys': ['all_deals', 'all_business_listings', 'last_apify_run']
    })

@app.route('/api/cache/businesses', methods=['GET'])
def get_cached_businesses():
    """Get cached business listings"""
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
    """Manually load data into cache"""
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

# ✅ Cached deals endpoint
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
        
        # Clear the cache for deals
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
    """Get all messages for a deal"""
    try:
        # Check if user has access to this deal
        deal = Deal.query.get(deal_id)
        if not deal:
            return jsonify({'error': 'Deal not found'}), 404

        # Check if user is the sponsor or has expressed interest
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
    """Send a message to a deal chat"""
    try:
        data = request.json
        message_text = data.get('message')
        
        if not message_text:
            return jsonify({'error': 'Message is required'}), 400
        
        # Check if deal exists
        deal = Deal.query.get(deal_id)
        if not deal:
            print(f"❌ Deal not found: {deal_id}")
            return jsonify({'error': 'Deal not found'}), 404
        
        # Check if user has access to this deal
        if deal.sponsor_id != current_user.id:
            interest = DealInterest.query.filter_by(
                deal_id=deal_id, 
                user_id=current_user.id
            ).first()
            if not interest:
                return jsonify({'error': 'Access denied'}), 403
        
        # Save message
        message = ChatMessage(
            deal_id=deal_id,
            user_id=current_user.id,
            message=message_text
        )
        db.session.add(message)
        db.session.commit()
        
        print(f"✅ Message saved for deal {deal_id}")
        
        # Emit via WebSocket
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
    """Mark a message as read"""
    try:
        message = ChatMessage.query.get(message_id)
        if not message:
            return jsonify({'error': 'Message not found'}), 404
        
        if message.deal_id != deal_id:
            return jsonify({'error': 'Message does not belong to this deal'}), 400
        
        # Only the recipient can mark as read
        if message.user_id == current_user.id:
            return jsonify({'error': 'Cannot mark own message as read'}), 400
        
        message.read = True
        db.session.commit()
        
        return jsonify({'message': 'Message marked as read'}), 200
    except Exception as e:
        print(f"Error marking message read: {str(e)}")
        return jsonify({'error': str(e)}), 500


# --- Create or get a deal from cached data ---
@app.route('/api/deals/sync', methods=['POST'])
@token_required
def sync_deal(current_user):
    """Create or update a deal from cached data"""
    try:
        data = request.get_json()
        deal_id = data.get('dealId')
        deal_data = data.get('dealData', {})
        
        print(f"🔄 Syncing deal: {deal_id}")
        print(f"📦 Deal data: {deal_data}")
        
        if not deal_id:
            return jsonify({'error': 'Deal ID is required'}), 400
        
        # Try to find existing deal by title first
        title = deal_data.get('title', '')
        existing_deal = None
        
        if title:
            existing_deal = Deal.query.filter_by(title=title).first()
        
        if not existing_deal:
            # Create a new deal
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
    """Debug endpoint to check if token is working"""
    return jsonify({
        'authenticated': True,
        'user_id': current_user.id,
        'username': current_user.username,
        'email': current_user.email
    })

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

# Add these to the existing WebSocket handlers
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
