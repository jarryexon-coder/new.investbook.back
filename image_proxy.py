import requests
from flask import Flask, request, Response, send_file
from io import BytesIO
import re

app = Flask(__name__)

@app.route('/proxy/image')
def proxy_image():
    url = request.args.get('url')
    if not url:
        return {'error': 'Missing url parameter'}, 400
    
    # Validate URL
    if not url.startswith('http'):
        return {'error': 'Invalid URL'}, 400
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.loopnet.com/'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        # Check if image fetched successfully
        if response.status_code == 200:
            return Response(
                response.content,
                mimetype=response.headers.get('content-type', 'image/jpeg')
            )
        else:
            return {'error': f'Failed to fetch image: {response.status_code}'}, response.status_code
    except Exception as e:
        return {'error': str(e)}, 500

if __name__ == '__main__':
    app.run(port=5001, debug=True)
