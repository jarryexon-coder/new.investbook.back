import os
import stripe
from flask import Blueprint, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta
import jwt
from functools import wraps
from models import User, db  # Assuming you have models.py

stripe_bp = Blueprint('stripe', __name__)
CORS(stripe_bp)

# Initialize Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')

# Token verification (reuse from main app)
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization')
        
        if auth_header:
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
        
        if not token:
            return jsonify({'message': 'Token is missing!'}), 401
        
        try:
            data = jwt.decode(token, os.getenv('SECRET_KEY', 'dev-secret-change-me'), algorithms=['HS256'])
            current_user = User.query.get(data['user_id'])
            
            if not current_user:
                return jsonify({'message': 'User not found!'}), 401
            
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token has expired!'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'message': 'Invalid token!'}), 401
        
        return f(current_user, *args, **kwargs)
    return decorated

# ===== SUBSCRIPTION PLANS =====
PLANS = {
    'view_only': {
        'price_id': os.getenv('STRIPE_VIEW_ONLY_PRICE_ID'),  # Set this in .env
        'name': 'View Only',
        'price': 4.99,
        'interval': 'month',
    },
    'chat': {
        'price_id': os.getenv('STRIPE_CHAT_PRICE_ID'),  # Set this in .env
        'name': 'Chat & Network',
        'price': 9.99,
        'interval': 'month',
    },
}

@stripe_bp.route('/create-checkout-session', methods=['POST'])
@token_required
def create_checkout_session(current_user):
    """Create a Stripe Checkout session"""
    try:
        data = request.get_json()
        plan_id = data.get('planId', 'view_only')
        
        if plan_id not in PLANS:
            return jsonify({'error': 'Invalid plan'}), 400
        
        plan = PLANS[plan_id]
        price_id = plan['price_id']
        
        if not price_id:
            # For testing - create a test price if not set
            # In production, you should have real price IDs
            return jsonify({
                'error': 'Payment not configured',
                'test_mode': True,
                'message': 'Test mode - subscription activated without payment'
            }), 200
        
        # Get or create Stripe customer
        if current_user.stripe_customer_id:
            customer_id = current_user.stripe_customer_id
        else:
            customer = stripe.Customer.create(
                email=current_user.email,
                name=current_user.username,
                metadata={
                    'user_id': current_user.id,
                    'username': current_user.username,
                }
            )
            customer_id = customer.id
            current_user.stripe_customer_id = customer_id
            db.session.commit()
        
        # Create checkout session
        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url='https://investbook-production.up.railway.app/success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url='https://investbook-production.up.railway.app/cancel',
            metadata={
                'user_id': current_user.id,
                'plan_id': plan_id,
                'username': current_user.username,
            }
        )
        
        return jsonify({
            'sessionId': checkout_session.id,
            'url': checkout_session.url,
            'test_mode': False,
        }), 200
        
    except Exception as e:
        print(f"❌ Checkout error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@stripe_bp.route('/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events"""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, WEBHOOK_SECRET
        )
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400
    
    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        handle_checkout_completed(session)
    elif event['type'] == 'invoice.paid':
        invoice = event['data']['object']
        handle_invoice_paid(invoice)
    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        handle_subscription_canceled(subscription)
    
    return jsonify({'status': 'success'}), 200

def handle_checkout_completed(session):
    """Handle successful checkout"""
    try:
        user_id = session.get('metadata', {}).get('user_id')
        plan_id = session.get('metadata', {}).get('plan_id', 'view_only')
        
        if not user_id:
            print("❌ No user_id in session metadata")
            return
        
        user = User.query.get(int(user_id))
        if not user:
            print(f"❌ User {user_id} not found")
            return
        
        # Activate subscription
        days = 30 if plan_id == 'view_only' else 30
        expiry = datetime.utcnow() + timedelta(days=days)
        
        user.subscription_plan = plan_id
        user.subscription_expiry = expiry
        user.stripe_customer_id = session.get('customer')
        
        db.session.commit()
        print(f"✅ Subscription activated for user {user.username} ({plan_id})")
        
    except Exception as e:
        print(f"❌ Error handling checkout: {str(e)}")
        db.session.rollback()

def handle_invoice_paid(invoice):
    """Handle successful payment"""
    try:
        customer_id = invoice.get('customer')
        if not customer_id:
            return
        
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if not user:
            return
        
        # Extend subscription by 30 days
        if user.subscription_expiry:
            new_expiry = user.subscription_expiry + timedelta(days=30)
        else:
            new_expiry = datetime.utcnow() + timedelta(days=30)
        
        user.subscription_expiry = new_expiry
        db.session.commit()
        print(f"✅ Subscription extended for user {user.username}")
        
    except Exception as e:
        print(f"❌ Error handling invoice: {str(e)}")
        db.session.rollback()

def handle_subscription_canceled(subscription):
    """Handle subscription cancellation"""
    try:
        customer_id = subscription.get('customer')
        if not customer_id:
            return
        
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if not user:
            return
        
        user.subscription_plan = None
        user.subscription_expiry = None
        db.session.commit()
        print(f"✅ Subscription canceled for user {user.username}")
        
    except Exception as e:
        print(f"❌ Error handling cancellation: {str(e)}")
        db.session.rollback()

@stripe_bp.route('/subscription-status', methods=['GET'])
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
            'isSubscribed': bool(is_subscribed),
            'planId': current_user.subscription_plan,
            'tier': current_user.subscription_plan,
            'expiryDate': current_user.subscription_expiry.isoformat() if current_user.subscription_expiry else None
        }), 200
        
    except Exception as e:
        print(f"❌ Subscription status error: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ===== TEST MODE - REMOVE IN PRODUCTION =====
@stripe_bp.route('/test-activate', methods=['POST'])
@token_required
def test_activate_subscription(current_user):
    """Test mode: Activate subscription without payment (REMOVE IN PRODUCTION)"""
    try:
        data = request.get_json()
        plan_id = data.get('planId', 'view_only')
        
        # Only allow in test mode
        if os.getenv('ENVIRONMENT') == 'production':
            return jsonify({'error': 'Not available in production'}), 403
        
        days = 30 if plan_id == 'view_only' else 30
        expiry = datetime.utcnow() + timedelta(days=days)
        
        current_user.subscription_plan = plan_id
        current_user.subscription_expiry = expiry
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Test subscription activated for {plan_id}',
            'expiry': expiry.isoformat()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Test activate error: {str(e)}")
        return jsonify({'error': str(e)}), 500
