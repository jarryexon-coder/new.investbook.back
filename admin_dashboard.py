# admin_dashboard.py - New file

from flask import Blueprint, jsonify, request
from flask_socketio import emit

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

# Add this near the top of admin_dashboard.py
def token_required(f):
    """Simple token_required decorator for migration"""
    from functools import wraps
    from flask import request, jsonify
    
    @wraps(f)
    def decorated(*args, **kwargs):
        # During migration, just pass through without checking token
        # In production, this should check the JWT token
        return f(*args, **kwargs)
    return decorated

# --- Admin Routes ---

@admin_bp.route('/dashboard/stats', methods=['GET'])
@token_required
def get_admin_stats(current_user):
    """Get overall platform statistics"""
    # In production, check if user is admin
    if not is_admin(current_user):
        return jsonify({'message': 'Admin access required'}), 403
    
    total_users = User.query.count()
    total_deals = Deal.query.count()
    total_groups = InvestmentGroup.query.count()
    
    # Get recent activity
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    recent_deals = Deal.query.order_by(Deal.created_at.desc()).limit(5).all()
    
    # Get flagged content
    flagged_deals = Deal.query.filter_by(status='flagged').count()
    flagged_users = User.query.filter(User.trust_score < 20).count()
    
    return jsonify({
        'total_users': total_users,
        'total_deals': total_deals,
        'total_groups': total_groups,
        'flagged_deals': flagged_deals,
        'flagged_users': flagged_users,
        'recent_users': [{'id': u.id, 'username': u.username, 'created_at': u.created_at.isoformat()} for u in recent_users],
        'recent_deals': [{'id': d.id, 'title': d.title, 'created_at': d.created_at.isoformat()} for d in recent_deals]
    })

@admin_bp.route('/users/<int:user_id>/ban', methods=['POST'])
@token_required
def ban_user(current_user, user_id):
    """Ban a user from the platform"""
    if not is_admin(current_user):
        return jsonify({'message': 'Admin access required'}), 403
    
    user = User.query.get_or_404(user_id)
    data = request.json
    
    # Ban user
    user.is_verified = False
    user.trust_score = 0
    
    # Add to banned list
    db.session.execute(
        "INSERT INTO banned_users (user_id, admin_id, reason, banned_at) "
        "VALUES (:user, :admin, :reason, :banned)",
        {
            'user': user_id,
            'admin': current_user.id,
            'reason': data.get('reason', 'Terms violation'),
            'banned': datetime.utcnow()
        }
    )
    db.session.commit()
    
    # Notify user via socket
    socketio.emit('user_banned', {
        'user_id': user_id,
        'reason': data.get('reason', 'Terms violation')
    })
    
    return jsonify({'message': 'User banned'})

@admin_bp.route('/deals/<int:deal_id>/flag', methods=['POST'])
@token_required
def flag_deal(current_user, deal_id):
    """Flag a deal for review"""
    if not is_admin(current_user) and not is_moderator(current_user):
        return jsonify({'message': 'Moderator access required'}), 403
    
    deal = Deal.query.get_or_404(deal_id)
    data = request.json
    
    deal.status = 'flagged'
    db.session.commit()
    
    # Log flag
    db.session.execute(
        "INSERT INTO flagged_content (content_type, content_id, admin_id, reason, flagged_at) "
        "VALUES ('deal', :deal_id, :admin, :reason, :flagged)",
        {
            'deal_id': deal_id,
            'admin': current_user.id,
            'reason': data.get('reason', 'Suspicious content'),
            'flagged': datetime.utcnow()
        }
    )
    db.session.commit()
    
    # Notify deal sponsor
    socketio.emit('deal_flagged', {
        'deal_id': deal_id,
        'sponsor_id': deal.sponsor_id,
        'reason': data.get('reason', 'Suspicious content')
    })
    
    return jsonify({'message': 'Deal flagged for review'})

@admin_bp.route('/reviews', methods=['GET'])
@token_required
def get_reported_reviews(current_user):
    """Get reported reviews for moderation"""
    if not is_admin(current_user) and not is_moderator(current_user):
        return jsonify({'message': 'Moderator access required'}), 403
    
    # In production, add a reports table
    # For now, get reviews with low ratings
    reported_reviews = TrustReview.query.filter(TrustReview.rating <= 2).order_by(TrustReview.created_at.desc()).limit(20).all()
    
    return jsonify([{
        'id': r.id,
        'reviewer': r.reviewer.username,
        'reviewee': r.reviewee.username,
        'rating': r.rating,
        'comment': r.comment,
        'created_at': r.created_at.isoformat()
    } for r in reported_reviews])

@admin_bp.route('/reviews/<int:review_id>/delete', methods=['DELETE'])
@token_required
def delete_review(current_user, review_id):
    """Delete a review (moderation)"""
    if not is_admin(current_user) and not is_moderator(current_user):
        return jsonify({'message': 'Moderator access required'}), 403
    
    review = TrustReview.query.get_or_404(review_id)
    
    # Recalculate reviewee's trust score
    reviewee = User.query.get(review.reviewee_id)
    remaining_reviews = TrustReview.query.filter_by(reviewee_id=review.reviewee_id).all()
    
    if remaining_reviews:
        avg = sum(r.rating for r in remaining_reviews) / len(remaining_reviews)
        reviewee.trust_score = avg * 10
    else:
        reviewee.trust_score = 50
    
    db.session.delete(review)
    db.session.commit()
    
    return jsonify({'message': 'Review deleted'})

@admin_bp.route('/fraud-reports', methods=['GET'])
@token_required
def get_fraud_reports(current_user):
    """Get fraud reports for review"""
    if not is_admin(current_user):
        return jsonify({'message': 'Admin access required'}), 403
    
    # Query fraud reports (created earlier)
    reports = db.session.execute(
        "SELECT * FROM fraud_reports ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    
    return jsonify([{
        'id': r[0],
        'reporter_id': r[1],
        'reported_id': r[2],
        'reason': r[3],
        'created_at': r[4].isoformat()
    } for r in reports])

# --- Helper Functions ---

def is_admin(user):
    """Check if user is admin"""
    # In production, check a roles table
    return user.id == 1  # Assuming user ID 1 is admin

def is_moderator(user):
    """Check if user is moderator"""
    # In production, check a roles table
    return user.id in [1, 2, 3]  # Example moderator IDs

# --- Add to app.py ---
# Register admin blueprint
# app.register_blueprint(admin_bp)
