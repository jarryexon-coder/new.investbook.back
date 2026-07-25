import os
import stripe
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timedelta
from functools import wraps
import jwt

# Create a blueprint
stripe_bp = Blueprint('stripe', __name__)

# ✅ Get Stripe key from environment with better error handling
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY')

if not STRIPE_SECRET_KEY:
    print("❌ STRIPE_SECRET_KEY is not set in environment!")
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY')

stripe.api_key = STRIPE_SECRET_KEY
print(f"🔑 Stripe key loaded: {STRIPE_SECRET_KEY[:20] if STRIPE_SECRET_KEY else 'MISSING'}...")

# Your Price IDs
PRICE_IDS = {
    'monthly': 'price_1Tvq829OUuvX0WP5OaHexOvw',
    'yearly': 'price_1Tvq8t9OUuvX0WP5Fa3DJEQL'
}

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
            from app import app, User, db
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

# --- Stripe Routes ---

@stripe_bp.route('/create-payment-intent', methods=['POST'])
@token_required
def create_payment_intent(current_user):
    """Create a Stripe payment intent for subscription"""
    try:
        data = request.json
        price_id = data.get('priceId')
        
        if not price_id:
            return jsonify({'error': 'Price ID is required'}), 400
        
        # Create a payment intent
        intent = stripe.PaymentIntent.create(
            amount=1000,  # Replace with actual amount based on price ID
            currency='usd',
            metadata={
                'user_id': current_user.id,
                'price_id': price_id
            }
        )
        
        return jsonify({
            'clientSecret': intent.client_secret,
            'paymentIntentId': intent.id
        }), 200
    except Exception as e:
        print(f"❌ Stripe error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@stripe_bp.route('/create-subscription', methods=['POST'])
@token_required
def create_subscription(current_user):
    """Create a Stripe subscription"""
    try:
        data = request.json
        price_id = data.get('priceId')
        payment_method_id = data.get('paymentMethodId')
        
        if not price_id or not payment_method_id:
            return jsonify({'error': 'Price ID and Payment Method ID are required'}), 400
        
        # Create a customer if not exists
        if not current_user.stripe_customer_id:
            customer = stripe.Customer.create(
                email=current_user.email,
                metadata={'user_id': current_user.id}
            )
            from app import db
            current_user.stripe_customer_id = customer.id
            db.session.commit()
        
        # Attach payment method to customer
        stripe.PaymentMethod.attach(
            payment_method_id,
            customer=current_user.stripe_customer_id
        )
        
        # Create subscription
        subscription = stripe.Subscription.create(
            customer=current_user.stripe_customer_id,
            items=[{'price': price_id}],
            payment_behavior='default_incomplete',
            payment_settings={'save_default_payment_method': 'on_subscription'},
            expand=['latest_invoice.payment_intent']
        )
        
        return jsonify({
            'subscriptionId': subscription.id,
            'clientSecret': subscription.latest_invoice.payment_intent.client_secret,
            'status': subscription.status
        }), 200
    except Exception as e:
        print(f"❌ Stripe error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@stripe_bp.route('/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events"""
    try:
        payload = request.get_data(as_text=True)
        sig_header = request.headers.get('Stripe-Signature')
        
        # For now, just log the event
        print(f"📩 Webhook received: {payload[:200]}...")
        
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        print(f"❌ Webhook error: {str(e)}")
        return jsonify({'error': str(e)}), 500
