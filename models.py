from datetime import datetime
from database import db

class User(db.Model):
    __tablename__ = 'users'
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
    stripe_subscription_id = db.Column(db.String(200), nullable=True)

class Deal(db.Model):
    __tablename__ = 'deals'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    asset_type = db.Column(db.String(50), nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    min_investment = db.Column(db.Float, nullable=False)
    location = db.Column(db.String(200))
    expected_roi = db.Column(db.String(50))
    status = db.Column(db.String(50), default='open')
    sponsor_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sponsor = db.relationship('User', backref='deals_listed')

class DealInterest(db.Model):
    __tablename__ = 'deal_interests'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'))
    status = db.Column(db.String(50), default='pending')
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='interests')
    deal = db.relationship('Deal', backref='interested_users')

class TrustReview(db.Model):
    __tablename__ = 'trust_reviews'
    id = db.Column(db.Integer, primary_key=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    reviewee_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'))
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class InvestmentGroup(db.Model):
    __tablename__ = 'investment_groups'
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'))
    name = db.Column(db.String(200))
    total_committed = db.Column(db.Float, default=0.0)
    target_amount = db.Column(db.Float)
    status = db.Column(db.String(50), default='forming')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    deal = db.relationship('Deal', backref='investment_groups')

class InvestmentCommitment(db.Model):
    __tablename__ = 'investment_commitments'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('investment_groups.id'))
    investor_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    amount_committed = db.Column(db.Float, nullable=False)
    amount_confirmed = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(50), default='pending')
    proof_of_funds = db.Column(db.String(500))
    confirmed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    investor = db.relationship('User', backref='commitments')
    group = db.relationship('InvestmentGroup', backref='commitments')

class InvestmentMilestone(db.Model):
    __tablename__ = 'investment_milestones'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('investment_groups.id'))
    title = db.Column(db.String(200))
    description = db.Column(db.Text)
    due_date = db.Column(db.DateTime)
    completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    group = db.relationship('InvestmentGroup', backref='milestones')

class PortfolioInvestment(db.Model):
    __tablename__ = 'portfolio_investments'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'))
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = db.relationship('User', backref='portfolio_investments')
    deal = db.relationship('Deal', backref='portfolio_investments')

class DealChatMessage(db.Model):
    __tablename__ = 'deal_chat_messages'
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_edited = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    user = db.relationship('User', backref='deal_chat_messages')
    deal = db.relationship('Deal', backref='chat_messages_all')

class DealChatParticipant(db.Model):
    __tablename__ = 'deal_chat_participants'
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_read_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_muted = db.Column(db.Boolean, default=False)
    user = db.relationship('User', backref='deal_chat_participants')
    deal = db.relationship('Deal', backref='chat_participants')

class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    read = db.Column(db.Boolean, default=False)
    user = db.relationship('User', backref='chat_messages')
    deal = db.relationship('Deal', backref='chat_messages')
