from flask import Flask
import os

app = Flask(__name__)

@app.route('/')
def hello():
    return "Hello from Railway! 🚀"

@app.route('/health')
def health():
    return {"status": "healthy"}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Test server running on port {port}")
    app.run(host='0.0.0.0', port=port)
