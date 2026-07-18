from app import app, db
from sqlalchemy import text

with app.app_context():
    # Create all tables if they don't exist
    db.create_all()
    
    # Add additional tables if needed
    print("✅ Database migrations complete!")
