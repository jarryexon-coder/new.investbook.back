# Simple mock for migration
class TrustScoringEngine:
    def __init__(self):
        pass
    
    def calculate_trust_score(self, user_data):
        return 0.5
    
    def update_trust_score(self, user_id, score):
        pass
    
    def get_trust_history(self, user_id):
        return []
