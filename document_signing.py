class DocumentSigning:
    def __init__(self):
        pass
    
    def sign_document(self, doc, user):
        return {"status": "signed", "signature": "mock_signature"}
    
    def verify_signature(self, doc_id):
        return True
