import os
import stripe
from flask import Blueprint, request, jsonify, current_app
from flask_cors import CORS
from datetime import datetime, timedelta
import jwt
from functools import wraps

stripe_bp = Blueprint('stripe', __name__)
CORS(stripe_bp)

# Initialize Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
print(f"🔑 Stripe key loaded: {stripe.api_key[:15] if stripe.api_key else 'Not set'}...")

# ===== TOKEN VERIFICATION =====
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Import inside function to avoid circular imports
        from app import User, db
        
        token = None
        auth_header = request.headers.get('Authorization')
        
        if auth_header:
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
        
        if not token:
            return jsonify({'message': 'Token is missing!'}), 401
        
        try:
            data = jwt.decode(token, os.getenv('SECRET_KEY', 'dev-secret-change-me'), algorithms=['HS256'])
            
            # Use the app context to query
            with current_app.app_context():
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
        'price_id': os.getenv('STRIPE_VIEW_ONLY_PRICE_ID'),
        'name': 'View Only',
        'price': 4.99,
        'interval': 'month',
    },
    'chat': {
        'price_id': os.getenv('STRIPE_CHAT_PRICE_ID'),
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
        from app import db
        
        data = request.get_json()
        plan_id = data.get('planId', 'view_only')
        
        if plan_id not in PLANS:
            return jsonify({'error': 'Invalid plan'}), 400
        
        plan = PLANS[plan_id]
        price_id = plan['price_id']
        
        if not price_id:
            return jsonify({
                'error': 'Payment not configured',
                'test_mode': True,
                'message': 'Test mode - subscription activated without payment'
            }), 200
        
        # Use app context for database operations
        with current_app.app_context():
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

@stripe_bp.route('/test-activate', methods=['POST'])
@token_required
def test_activate_subscription(current_user):
    """Test mode: Activate subscription without payment"""
    try:
        from app import db
        
        data = request.get_json()
        plan_id = data.get('planId', 'view_only')
        
        # Only allow in test mode
        if os.getenv('ENVIRONMENT') == 'production':
            return jsonify({'error': 'Not available in production'}), 403
        
        days = 30
        expiry = datetime.utcnow() + timedelta(days=days)
        
        with current_app.app_context():
            current_user.subscription_plan = plan_id
            current_user.subscription_expiry = expiry
            db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Test subscription activated for {plan_id}',
            'expiry': expiry.isoformat()
        }), 200
        
    except Exception as e:
        from app import db
        db.session.rollback()
        print(f"❌ Test activate error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@stripe_bp.route('/subscriptions/cancel', methods=['POST'])
@token_required
def cancel_subscription(current_user):
    """Cancel user's subscription"""
    try:
        from app import db
        
        with current_app.app_context():
            current_user.subscription_plan = None
            current_user.subscription_expiry = None
            db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Subscription canceled successfully"
        }), 200
        
    except Exception as e:
        from app import db
        db.session.rollback()
        print(f"❌ Cancel subscription error: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ===== WEBHOOK HANDLER =====
@stripe_bp.route('/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events"""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    
    if not webhook_secret:
        print("⚠️ WEBHOOK_SECRET not set, using test mode")
        event = stripe.Event.construct_from(
            request.get_json(), stripe.api_key
        )
    else:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, webhook_secret
            )
        except ValueError as e:
            return jsonify({'error': 'Invalid payload'}), 400
        except stripe.error.SignatureVerificationError as e:
            return jsonify({'error': 'Invalid signature'}), 400
    
    print(f"📡 Webhook event: {event['type']}")
    
    if event['type'] == 'checkout.session.completed':
        handle_checkout_completed(event['data']['object'])
    elif event['type'] == 'invoice.paid':
        handle_invoice_paid(event['data']['object'])
    elif event['type'] == 'customer.subscription.deleted':
        handle_subscription_deleted(event['data']['object'])
    
    return jsonify({'status': 'success'}), 200

def handle_checkout_completed(session):
    """Handle successful checkout"""
    try:
        from app import User, db
        
        user_id = session.get('metadata', {}).get('user_id')
        plan_id = session.get('metadata', {}).get('plan_id', 'view_only')
        customer_id = session.get('customer')
        
        if not user_id:
            return
        
        with current_app.app_context():
            user = User.query.get(int(user_id))
            if not user:
                return
            
            days = 30
            expiry = datetime.utcnow() + timedelta(days=days)
            
            user.subscription_plan = plan_id
            user.subscription_expiry = expiry
            user.stripe_customer_id = customer_id
            
            db.session.commit()
            print(f"✅ Subscription activated for user {user.username}")
        
    except Exception as e:
        print(f"❌ Webhook error: {str(e)}")
        from app import db
        db.session.rollback()

def handle_invoice_paid(invoice):
    """Handle successful payment"""
    try:
        from app import User, db
        
        customer_id = invoice.get('customer')
        if not customer_id:
            return
        
        with current_app.app_context():
            user = User.query.filter_by(stripe_customer_id=customer_id).first()
            if not user:
                return
            
            if user.subscription_expiry:
                new_expiry = user.subscription_expiry + timedelta(days=30)
            else:
                new_expiry = datetime.utcnow() + timedelta(days=30)
            
            user.subscription_expiry = new_expiry
            db.session.commit()
            print(f"✅ Subscription extended for user {user.username}")
        
    except Exception as e:
        print(f"❌ Invoice error: {str(e)}")
        from app import db
        db.session.rollback()

def handle_subscription_deleted(subscription):
    """Handle subscription cancellation"""
    try:
        from app import User, db
        
        customer_id = subscription.get('customer')
        if not customer_id:
            return
        
        with current_app.app_context():
            user = User.query.filter_by(stripe_customer_id=customer_id).first()
            if not user:
                return
            
            user.subscription_plan = None
            user.subscription_expiry = None
            db.session.commit()
            print(f"✅ Subscription canceled for user {user.username}")
        
    except Exception as e:
        print(f"❌ Cancellation error: {str(e)}")
        from app import db
        db.session.rollback()
