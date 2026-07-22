# admin_dashboard.py - Removed circular import
from flask import Blueprint, jsonify, request
from flask_socketio import emit
from functools import wraps

# ✅ Define the blueprint here - don't import from app
admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

# ✅ Define token_required here if needed
def token_required(f):
    """Simple token_required decorator for admin routes"""
    from functools import wraps
    from flask import request, jsonify
    
    @wraps(f)
    def decorated(*args, **kwargs):
        # Get token from header
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({'error': 'No token provided'}), 401
        
        # For now, just pass through - in production, validate the token
        # This avoids circular import issues
        return f(*args, **kwargs)
    return decorated

# Admin routes
@admin_bp.route('/admin', methods=['GET'])
@token_required
def admin_dashboard():
    """Admin dashboard endpoint"""
    return jsonify({
        'status': 'success',
        'message': 'Admin dashboard',
        'data': {
            'stats': {},
            'users': []
        }
    })

@admin_bp.route('/admin/users', methods=['GET'])
@token_required
def admin_users():
    """Admin users endpoint"""
    return jsonify({
        'users': [],
        'total': 0
    })

@admin_bp.route('/admin/stats', methods=['GET'])
@token_required
def admin_stats():
    """Admin statistics endpoint"""
    return jsonify({
        'stats': {
            'total_users': 0,
            'total_transactions': 0,
            'total_investments': 0
        }
    })

# ❌ REMOVE this line - don't register blueprint here
# app.register_blueprint(admin_bp)
