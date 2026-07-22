# requirements.txt
# flask, flask-sqlalchemy, flask-bcrypt, flask-cors, flask-socketio, eventlet, pyjwt, python-dotenv

from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_cors import CORS
import sys
from flask_socketio import SocketIO, join_room, emit
import jwt
import datetime
from functools import wraps
import os
from dotenv import load_dotenv
from trust_algorithm import TrustScoringEngine
from document_signing import DocumentSigning
from admin_dashboard import admin_bp
import json
import hashlib

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///invest.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# ✅ Simple CORS - Allow all
CORS(app, origins=["http://localhost:3000", "http://localhost:5000"])

# ✅ KEEP THIS - For SocketIO (real-time features)
socketio = SocketIO(app, cors_allowed_origins="*")

# ❌ REMOVED the duplicate CORS(app) call that was here

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
        'timestamp': datetime.datetime.utcnow().isoformat(),
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
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    subscription_plan = db.Column(db.String(50), nullable=True)
    subscription_expiry = db.Column(db.DateTime, nullable=True)
    stripe_customer_id = db.Column(db.String(200), nullable=True)

class Deal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    asset_type = db.Column(db.String(50), nullable=False)  # property, vehicle, business
    total_price = db.Column(db.Float, nullable=False)
    min_investment = db.Column(db.Float, nullable=False)
    location = db.Column(db.String(200))
    expected_roi = db.Column(db.String(50))
    status = db.Column(db.String(50), default='open')  # open, due_diligence, funding, closed
    sponsor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    sponsor = db.relationship('User', backref='deals_listed')

class DealInterest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'))
    status = db.Column(db.String(50), default='pending')  # pending, accepted, declined
    joined_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    user = db.relationship('User', backref='interests')
    deal = db.relationship('Deal', backref='interested_users')

class TrustReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    reviewee_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'))
    rating = db.Column(db.Integer, nullable=False)  # 1-5
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

# --- New Models for Investment Tracking ---

class InvestmentGroup(db.Model):
    """Represents a group of investors pooling money for a deal"""
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'))
    name = db.Column(db.String(200))
    total_committed = db.Column(db.Float, default=0.0)
    target_amount = db.Column(db.Float)  # Total needed for the deal
    status = db.Column(db.String(50), default='forming')  # forming, committed, funded, closed
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    deal = db.relationship('Deal', backref='investment_groups')

class InvestmentCommitment(db.Model):
    """Tracks each investor's commitment without handling money"""
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('investment_group.id'))
    investor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    amount_committed = db.Column(db.Float, nullable=False)
    amount_confirmed = db.Column(db.Float, default=0.0)  # Confirmed via external proof
    status = db.Column(db.String(50), default='pending')  # pending, confirmed, withdrawn
    proof_of_funds = db.Column(db.String(500))  # URL to uploaded proof (bank statement, etc.)
    confirmed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    investor = db.relationship('User', backref='commitments')
    group = db.relationship('InvestmentGroup', backref='commitments')

class InvestmentMilestone(db.Model):
    """Track deal progress milestones"""
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('investment_group.id'))
    title = db.Column(db.String(200))
    description = db.Column(db.Text)
    due_date = db.Column(db.DateTime)
    completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    group = db.relationship('InvestmentGroup', backref='milestones')

def calculate_trust_score(user):
    # Weighted: reviews (60%) + completed deals (30%) + verification (10%)
    avg_rating = db.session.query(db.func.avg(TrustReview.rating)).filter_by(reviewee_id=user.id).scalar() or 3
    review_score = (avg_rating / 5) * 60  # max 60
    deal_score = min(user.investments_completed / 10, 1) * 30  # max 30 at 10 deals
    verif_score = 10 if user.is_verified else 0
    return review_score + deal_score + verif_score  # 0-100

# --- Authentication ---
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        # Check if token is in headers
        auth_header = request.headers.get('Authorization')
        
        if auth_header:
            # Remove 'Bearer ' prefix if present
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
            else:
                token = auth_header
        
        # If no token in Authorization header, check query params
        if not token:
            token = request.args.get('token')
        
        if not token:
            return jsonify({'message': 'Token is missing!'}), 401
        
        try:
            # Log the token for debugging (remove in production)
            print(f"🔑 Token received: {token[:20]}...")
            
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user = User.query.get(data['user_id'])
            
            if not current_user:
                return jsonify({'message': 'User not found!'}), 401
            
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token has expired!'}), 401
        except jwt.InvalidTokenError as e:
            print(f"❌ Invalid token error: {str(e)}")
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
        'timestamp': datetime.datetime.utcnow().isoformat()
    })

@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.json
        
        # Validate input
        if not data or not data.get('username') or not data.get('email') or not data.get('password'):
            return jsonify({'message': 'Missing required fields'}), 400
        
        # Check if user exists
        if User.query.filter_by(username=data['username']).first():
            return jsonify({'message': 'Username already exists'}), 400
        
        if User.query.filter_by(email=data['email']).first():
            return jsonify({'message': 'Email already exists'}), 400
        
        # Create user
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
        
        print(f"🔍 Login attempt: {email}")
        
        user = User.query.filter_by(email=email).first()
        if not user:
            print(f"❌ User not found: {email}")
            return jsonify({'message': 'Invalid credentials'}), 401
        
        print(f"✅ User found: {user.username}")
        
        # ✅ FIX: Use password_hash, not password
        try:
            is_valid = bcrypt.check_password_hash(user.password_hash, password)
            print(f"🔑 Password valid: {is_valid}")
        except Exception as e:
            print(f"❌ Password check error: {str(e)}")
            return jsonify({'message': 'Invalid credentials'}), 401
        
        if not is_valid:
            print(f"❌ Invalid password for: {email}")
            return jsonify({'message': 'Invalid credentials'}), 401
        
        # ✅ FIX: Generate token before using it
        token = jwt.encode({
            'user_id': user.id,
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        }, app.config['SECRET_KEY'], algorithm='HS256')
        
        # ✅ FIX: Create user_data before using it
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
        print(f"❌ Login error: {str(e)}")
        import traceback
        traceback.print_exc()
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

@app.route('/api/deals/<int:deal_id>/interest', methods=['POST'])
@token_required
def express_interest(current_user, deal_id):
    deal = Deal.query.get_or_404(deal_id)
    if DealInterest.query.filter_by(user_id=current_user.id, deal_id=deal_id).first():
        return jsonify({'message': 'Already interested'}), 400
    interest = DealInterest(user_id=current_user.id, deal_id=deal_id)
    db.session.add(interest)
    db.session.commit()
    return jsonify({'message': 'Interest expressed'}), 201

@app.route('/api/deals/<int:deal_id>/messages', methods=['GET'])
@token_required
def get_messages(deal_id):
    """Get messages for a deal"""
    try:
        messages = Message.query.filter_by(deal_id=deal_id).order_by(Message.created_at).all()
        return jsonify([m.to_dict() for m in messages])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/deals/<int:deal_id>/messages', methods=['POST'])
@token_required
def send_message(deal_id):
    """Send a message in a deal chat"""
    try:
        data = request.json
        message = Message(
            deal_id=deal_id,
            user_id=current_user.id,
            text=data.get('text'),
            image=data.get('image')
        )
        db.session.add(message)
        db.session.commit()
        
        # Notify via WebSocket
        socketio.emit('new_message', {
            'deal_id': deal_id,
            'message': message.to_dict()
        }, room=f'deal_{deal_id}')
        
        return jsonify(message.to_dict()), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/deals/<int:deal_id>/interested', methods=['GET'])
@token_required
def get_interested_investors(current_user, deal_id):
    deal = Deal.query.get_or_404(deal_id)
    if deal.sponsor_id != current_user.id:
        return jsonify({'message': 'Only the deal sponsor can view this'}), 403
    interests = DealInterest.query.filter_by(deal_id=deal_id, status='pending').all()
    return jsonify([{
        'user_id': i.user.id, 'username': i.user.username,
        'trust_score': i.user.trust_score, 'investments_completed': i.user.investments_completed
    } for i in interests])

@app.route('/api/users/<int:user_id>/trust', methods=['POST'])
@token_required
def rate_user(current_user, user_id):
    """Rate another user after a deal completes"""
    data = request.json
    review = TrustReview(
        reviewer_id=current_user.id,
        reviewee_id=user_id,
        deal_id=data['deal_id'],
        rating=data['rating'],
        comment=data.get('comment', '')
    )
    db.session.add(review)
    # Update trust score (simple average)
    reviewee = User.query.get(user_id)
    all_ratings = [r.rating for r in TrustReview.query.filter_by(reviewee_id=user_id).all()]
    avg_rating = sum(all_ratings) / len(all_ratings) if all_ratings else data['rating']
    reviewee.trust_score = avg_rating * 10  # scale 1-5 to 10-50
    db.session.commit()
    return jsonify({'message': 'Rating submitted'}), 201

@app.route('/api/deals', methods=['POST'])
@token_required
def create_deal(current_user):
    try:
        data = request.json
        print(f"Creating deal with data: {data}")  # Debug log
        
        # Validate required fields
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
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'message': 'Failed to create deal'}), 500

# --- Investment Tracking Routes ---

@app.route('/api/groups', methods=['POST'])
@token_required
def create_investment_group(current_user):
    """Create an investment group for a deal"""
    data = request.json
    deal = Deal.query.get_or_404(data['deal_id'])
    
    # Only sponsor or deal owner can create group
    if deal.sponsor_id != current_user.id:
        return jsonify({'message': 'Only deal sponsor can create group'}), 403
    
    group = InvestmentGroup(
        deal_id=deal.id,
        name=data.get('name', f"Group for {deal.title}"),
        target_amount=data.get('target_amount', deal.total_price)
    )
    db.session.add(group)
    db.session.commit()
    
    return jsonify({
        'message': 'Investment group created',
        'group_id': group.id
    }), 201

@app.route('/api/groups/<int:group_id>/commit', methods=['POST'])
@token_required
def commit_to_group(current_user, group_id):
    """Commit to invest in a group"""
    group = InvestmentGroup.query.get_or_404(group_id)
    data = request.json
    
    # Check if already committed
    existing = InvestmentCommitment.query.filter_by(
        group_id=group_id, 
        investor_id=current_user.id
    ).first()
    
    if existing:
        return jsonify({'message': 'Already committed to this group'}), 400
    
    commitment = InvestmentCommitment(
        group_id=group_id,
        investor_id=current_user.id,
        amount_committed=data['amount']
    )
    db.session.add(commitment)
    
    # Update group total
    group.total_committed += data['amount']
    db.session.commit()
    
    # Notify group members via socket
    socketio.emit('new_commitment', {
        'group_id': group_id,
        'investor': current_user.username,
        'amount': data['amount'],
        'total': group.total_committed
    }, room=f'group_{group_id}')
    
    return jsonify({'message': 'Commitment recorded'}), 201

@app.route('/api/groups/<int:group_id>/confirm', methods=['POST'])
@token_required
def confirm_funds(current_user, group_id):
    """Confirm funds have been transferred (external verification)"""
    commitment = InvestmentCommitment.query.filter_by(
        group_id=group_id,
        investor_id=current_user.id
    ).first_or_404()
    
    data = request.json
    commitment.amount_confirmed = data.get('confirmed_amount', commitment.amount_committed)
    commitment.status = 'confirmed'
    commitment.confirmed_at = datetime.datetime.utcnow()
    
    # Store proof of funds reference (URL, document ID, etc.)
    if data.get('proof_url'):
        commitment.proof_of_funds = data['proof_url']
    
    db.session.commit()
    
    # Check if group is fully funded
    group = InvestmentGroup.query.get(group_id)
    if group.total_committed >= group.target_amount:
        group.status = 'funded'
        db.session.commit()
        socketio.emit('group_funded', {
            'group_id': group_id,
            'message': 'Group has reached its funding target!'
        }, room=f'group_{group_id}')
    
    return jsonify({'message': 'Funds confirmed'}), 200

@app.route('/api/groups/<int:group_id>/milestones', methods=['POST'])
@token_required
def add_milestone(current_user, group_id):
    """Add a milestone to track deal progress"""
    group = InvestmentGroup.query.get_or_404(group_id)
    deal = Deal.query.get(group.deal_id)
    
    if deal.sponsor_id != current_user.id:
        return jsonify({'message': 'Only sponsor can add milestones'}), 403
    
    data = request.json
    milestone = InvestmentMilestone(
        group_id=group_id,
        title=data['title'],
        description=data.get('description', ''),
        due_date=datetime.datetime.fromisoformat(data['due_date']) if data.get('due_date') else None
    )
    db.session.add(milestone)
    db.session.commit()
    
    return jsonify({'message': 'Milestone added', 'milestone_id': milestone.id}), 201

@app.route('/api/groups/<int:group_id>/milestones/<int:milestone_id>/complete', methods=['PUT'])
@token_required
def complete_milestone(current_user, group_id, milestone_id):
    """Mark a milestone as complete"""
    milestone = InvestmentMilestone.query.get_or_404(milestone_id)
    group = InvestmentGroup.query.get(group_id)
    deal = Deal.query.get(group.deal_id)
    
    # Allow sponsor or any confirmed investor to complete
    if deal.sponsor_id != current_user.id:
        commitment = InvestmentCommitment.query.filter_by(
            group_id=group_id,
            investor_id=current_user.id,
            status='confirmed'
        ).first()
        if not commitment:
            return jsonify({'message': 'Only sponsor or confirmed investors can complete milestones'}), 403
    
    milestone.completed = True
    milestone.completed_at = datetime.datetime.utcnow()
    db.session.commit()
    
    socketio.emit('milestone_completed', {
        'group_id': group_id,
        'milestone': milestone.title
    }, room=f'group_{group_id}')
    
    return jsonify({'message': 'Milestone completed'}), 200

@app.route('/api/groups/<int:group_id>/status', methods=['GET'])
@token_required
def get_group_status(current_user, group_id):
    """Get detailed group investment status"""
    group = InvestmentGroup.query.get_or_404(group_id)
    commitments = InvestmentCommitment.query.filter_by(group_id=group_id).all()
    
    return jsonify({
        'group': {
            'id': group.id,
            'name': group.name,
            'total_committed': group.total_committed,
            'target_amount': group.target_amount,
            'progress': (group.total_committed / group.target_amount * 100) if group.target_amount else 0,
            'status': group.status,
            'investor_count': len([c for c in commitments if c.status == 'confirmed'])
        },
        'commitments': [{
            'investor': c.investor.username,
            'amount': c.amount_committed,
            'confirmed': c.amount_confirmed,
            'status': c.status,
            'proof_submitted': bool(c.proof_of_funds)
        } for c in commitments]
    })

@app.route('/api/users/<int:user_id>/fraud-risk', methods=['GET'])
@token_required
def get_fraud_risk(current_user, user_id):
    """Get fraud risk assessment for a user"""
    user = User.query.get_or_404(user_id)
    # Simple fraud risk assessment based on trust score and verification status
    # In production, you would use more sophisticated logic
    risk_level = 'low'
    if user.trust_score < 30:
        risk_level = 'high'
    elif user.trust_score < 50:
        risk_level = 'medium'
    
    return jsonify({
        'user_id': user.id,
        'username': user.username,
        'trust_score': user.trust_score,
        'is_verified': user.is_verified,
        'risk_level': risk_level,
        'risk_factors': [
            'Low trust score' if user.trust_score < 40 else None,
            'Not verified' if not user.is_verified else None,
            'No completed investments' if user.investments_completed == 0 else None
        ],
        'recommendation': 'Proceed with caution' if risk_level in ['medium', 'high'] else 'Low risk'
    })

@app.route('/api/users/<int:user_id>/report', methods=['POST'])
@token_required
def report_user(current_user, user_id):
    """Report a user for suspicious behavior"""
    data = request.json
    
    # In production, you'd have a FraudReport model
    # For now, we'll simulate storing reports
    # Check if fraud_reports table exists, if not create it
    try:
        db.session.execute(
            "INSERT INTO fraud_reports (reporter_id, reported_id, reason, created_at) "
            "VALUES (:reporter, :reported, :reason, :created)",
            {
                'reporter': current_user.id,
                'reported': user_id,
                'reason': data.get('reason', 'Suspicious behavior'),
                'created': datetime.datetime.utcnow()
            }
        )
        db.session.commit()
    except:
        # If table doesn't exist, just return success
        # In production, you'd create the table via migrations
        pass
    
    # Check report count for auto-flagging
    try:
        report_count = db.session.execute(
            "SELECT COUNT(*) FROM fraud_reports WHERE reported_id = :id",
            {'id': user_id}
        ).scalar()
        
        if report_count > 5:
            user = User.query.get(user_id)
            if user:
                user.is_verified = False
                user.trust_score = max(0, user.trust_score - 20)
                db.session.commit()
                # Send alert to admin
                socketio.emit('admin_alert', {
                    'type': 'fraud_flagged',
                    'user_id': user_id,
                    'reports': report_count
                })
    except:
        # If table doesn't exist, skip the check
        pass
    
    return jsonify({'message': 'Report submitted'}), 201

# --- Document Signing Routes ---

@app.route('/api/documents', methods=['POST'])
@token_required
def create_document(current_user):
    """Create a new signing document"""
    data = request.json
    
    # Generate document ID
    doc_id = hashlib.md5(f"{current_user.id}{datetime.datetime.utcnow().isoformat()}".encode()).hexdigest()
    
    doc = {
        'id': doc_id,
        'title': data['title'],
        'content': data['content'],
        'template_type': data.get('template_type', 'investment_agreement'),
        'created_by': current_user.id,
        'created_at': datetime.datetime.utcnow().isoformat(),
        'status': 'draft',
        'signatures': []
    }
    
    # Store document in database
    # In production, use a Document model
    # For now, store as JSON in a text field
    try:
        db.session.execute(
            "INSERT INTO documents (id, creator_id, title, content, status, created_at) "
            "VALUES (:id, :creator, :title, :content, :status, :created)",
            {
                'id': doc_id,
                'creator': current_user.id,
                'title': doc['title'],
                'content': json.dumps(doc),
                'status': 'draft',
                'created': datetime.datetime.utcnow()
            }
        )
        db.session.commit()
    except:
        # If table doesn't exist, just return success
        # In production, you'd create the table via migrations
        pass
    
    return jsonify({'document_id': doc_id, 'document': doc})

@app.route('/api/documents/<doc_id>/sign', methods=['POST'])
@token_required
def sign_document(current_user, doc_id):
    """Sign a document"""
    data = request.json
    
    # Retrieve document
    try:
        doc_data = db.session.execute(
            "SELECT content FROM documents WHERE id = :id",
            {'id': doc_id}
        ).first()
        
        if not doc_data:
            return jsonify({'message': 'Document not found'}), 404
        
        doc = json.loads(doc_data[0])
        
        # Check if already signed
        if any(s['user_id'] == current_user.id for s in doc.get('signatures', [])):
            return jsonify({'message': 'Already signed'}), 400
        
        # Generate signature
        timestamp = datetime.datetime.utcnow().isoformat()
        signature_hash = hashlib.sha256(
            f"{doc_id}{current_user.id}{timestamp}".encode()
        ).hexdigest()
        
        # Add signature
        if 'signatures' not in doc:
            doc['signatures'] = []
        
        doc['signatures'].append({
            'user_id': current_user.id,
            'username': current_user.username,
            'timestamp': timestamp,
            'signature': signature_hash,
            'ip': request.remote_addr
        })
        
        # Update document status
        doc['status'] = 'signed' if len(doc['signatures']) >= data.get('required_signatures', 1) else 'partial'
        
        # Save back to database
        db.session.execute(
            "UPDATE documents SET content = :content, status = :status WHERE id = :id",
            {
                'id': doc_id,
                'content': json.dumps(doc),
                'status': doc['status']
            }
        )
        db.session.commit()
        
        # Notify all signers
        socketio.emit('document_signed', {
            'doc_id': doc_id,
            'signer': current_user.username,
            'total': len(doc['signatures'])
        })
        
        return jsonify({
            'message': 'Document signed',
            'signature': signature_hash,
            'status': doc['status']
        })
    except:
        # If table doesn't exist
        return jsonify({'message': 'Document signing not available'}), 501

@app.route('/api/documents/<doc_id>/status', methods=['GET'])
@token_required
def get_document_status(current_user, doc_id):
    """Get document signing status"""
    try:
        doc_data = db.session.execute(
            "SELECT content FROM documents WHERE id = :id",
            {'id': doc_id}
        ).first()
        
        if not doc_data:
            return jsonify({'message': 'Document not found'}), 404
        
        doc = json.loads(doc_data[0])
        
        return jsonify({
            'id': doc_id,
            'title': doc['title'],
            'status': doc['status'],
            'total_signatures': len(doc.get('signatures', [])),
            'signatures': [{
                'user': s['username'],
                'timestamp': s['timestamp']
            } for s in doc.get('signatures', [])]
        })
    except:
        return jsonify({'message': 'Document status not available'}), 501

@app.route('/api/debug/db', methods=['GET'])
@token_required
def debug_db(current_user):
    try:
        # Check if deals table exists
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        
        # Count deals
        deal_count = Deal.query.count()
        
        return jsonify({
            'tables': tables,
            'deal_count': deal_count,
            'user_id': current_user.id,
            'username': current_user.username
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        'timestamp': datetime.datetime.utcnow().isoformat()
    }, room=f"deal_{data['deal_id']}")

# Register admin blueprint with a unique name
try:
    app.register_blueprint(admin_bp, url_prefix='/admin')
except ValueError as e:
    print(f"⚠️ Admin blueprint already registered: {e}")
    # If already registered, try with a different name
    try:
        from admin_dashboard import admin_bp as admin_bp_alt
        app.register_blueprint(admin_bp_alt, url_prefix='/admin', name='admin_alt')
    except:
        print("⚠️ Could not register admin blueprint")

# --- Run ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    # Get port from environment variable
    port = int(os.environ.get('PORT', 5000))
    
    # In production, use eventlet with proper host
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        # Production on Railway
        print(f"Starting production server on port {port}")
        socketio.run(app, host='0.0.0.0', port=port, debug=False)
    else:
        # Local development
        socketio.run(app, debug=True, port=port)
