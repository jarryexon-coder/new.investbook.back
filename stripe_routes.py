import os
import stripe
from flask import request, jsonify
from datetime import datetime, timedelta
from app import app, db, User, token_required

# Initialize Stripe with your secret key from environment
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

# Your Price IDs
PRICE_IDS = {
    'monthly': 'price_1Tvq829OUuvX0WP5OaHexOvw',
    'yearly': 'price_1Tvq8t9OUuvX0WP5Fa3DJEQL',
}

SUBSCRIPTION_PLANS = {
    'monthly': {
        'id': 'monthly',
        'name': 'Monthly',
        'price': 4.99,
        'interval': 'month',
        'price_id': 'price_1Tvq829OUuvX0WP5OaHexOvw'
    },
    'yearly': {
        'id': 'yearly',
        'name': 'Yearly',
        'price': 49.99,
        'interval': 'year',
        'price_id': 'price_1Tvq8t9OUuvX0WP5Fa3DJEQL'
    }
}

@app.route('/api/subscriptions/create-payment-intent', methods=['POST'])
@token_required
def create_payment_intent(current_user):
    """Create a Stripe Payment Intent for subscription"""
    try:
        # ✅ Check if Stripe API key is configured
        if not stripe.api_key or stripe.api_key == 'sk_test_...':
            print("❌ Stripe API key is not configured!")
            return jsonify({
                'error': 'Stripe is not configured',
                'message': 'Please set STRIPE_SECRET_KEY environment variable'
            }), 500

        data = request.json
        plan_id = data.get('planId')
        print(f"🔍 Creating payment intent for user: {current_user.username}, plan: {plan_id}")
        
        if plan_id not in PRICE_IDS:
            return jsonify({'error': 'Invalid plan'}), 400
        
        # Create a customer if one doesn't exist
        if not current_user.stripe_customer_id:
            try:
                customer = stripe.Customer.create(
                    email=current_user.email,
                    name=current_user.username,
                    metadata={'user_id': current_user.id}
                )
                current_user.stripe_customer_id = customer.id
                db.session.commit()
                print(f"✅ Created Stripe customer: {customer.id}")
            except Exception as e:
                print(f"❌ Failed to create customer: {str(e)}")
                return jsonify({'error': 'Failed to create customer: ' + str(e)}), 500
        
        # Create a Payment Intent
        try:
            amount = int(SUBSCRIPTION_PLANS[plan_id]['price'] * 100)
            payment_intent = stripe.PaymentIntent.create(
                amount=amount,
                currency='usd',
                customer=current_user.stripe_customer_id,
                metadata={
                    'user_id': current_user.id,
                    'plan_id': plan_id,
                    'username': current_user.username
                },
                description=f"{SUBSCRIPTION_PLANS[plan_id]['name']} Subscription",
                payment_method_types=['card'],
                setup_future_usage='off_session',
            )
            
            print(f"✅ Payment intent created: {payment_intent.id}")
            
            return jsonify({
                'clientSecret': payment_intent.client_secret,
                'paymentIntentId': payment_intent.id
            }), 200
            
        except stripe.error.AuthenticationError as e:
            print(f"❌ Stripe Authentication Error: {str(e)}")
            return jsonify({
                'error': 'Stripe authentication failed',
                'message': 'Please check your Stripe API key'
            }), 500
        except stripe.error.InvalidRequestError as e:
            print(f"❌ Stripe Invalid Request: {str(e)}")
            return jsonify({
                'error': 'Invalid request to Stripe',
                'message': str(e)
            }), 400
        except Exception as e:
            print(f"❌ Stripe Error: {str(e)}")
            return jsonify({
                'error': 'Failed to create payment intent',
                'message': str(e)
            }), 500
        
    except Exception as e:
        print(f"❌ Payment intent error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/subscriptions/activate', methods=['POST'])
@token_required
def activate_subscription(current_user):
    try:
        data = request.json
        plan_id = data.get('planId')
        print(f"🔍 Activating subscription for user: {current_user.username}, plan: {plan_id}")
        
        if plan_id == 'monthly':
            expiry = datetime.utcnow() + timedelta(days=30)
        elif plan_id == 'yearly':
            expiry = datetime.utcnow() + timedelta(days=365)
        else:
            return jsonify({'error': 'Invalid plan'}), 400
        
        current_user.subscription_plan = plan_id
        current_user.subscription_expiry = expiry
        db.session.commit()
        
        print(f"✅ Subscription activated for user: {current_user.username}")
        
        return jsonify({
            'success': True,
            'message': 'Subscription activated',
            'expiry': expiry.isoformat()
        }), 200
        
    except Exception as e:
        print(f"❌ Activation error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/subscriptions/cancel', methods=['POST'])
@token_required
def cancel_subscription(current_user):
    try:
        print(f"🔍 Canceling subscription for user: {current_user.username}")
        
        current_user.subscription_plan = None
        current_user.subscription_expiry = None
        db.session.commit()
        
        print(f"✅ Subscription canceled for user: {current_user.username}")
        
        return jsonify({
            'success': True,
            'message': 'Subscription canceled successfully'
        }), 200
        
    except Exception as e:
        print(f"❌ Cancel error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/subscriptions/status', methods=['GET'])
@token_required
def get_subscription_status(current_user):
    try:
        is_active = False
        expiry = None
        
        if current_user.subscription_expiry:
            is_active = current_user.subscription_expiry > datetime.utcnow()
            expiry = current_user.subscription_expiry.isoformat()
        
        return jsonify({
            'isActive': is_active,
            'plan': current_user.subscription_plan,
            'expiry': expiry,
            'isTrialing': False
        }), 200
    except Exception as e:
        print(f"Status error: {str(e)}")
        return jsonify({'error': str(e)}), 500
