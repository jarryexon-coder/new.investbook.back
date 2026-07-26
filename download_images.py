import json
import requests
import os
import hashlib
from urllib.parse import urlparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Create images directory
os.makedirs('images', exist_ok=True)

print("📥 Loading listings...")
with open('listings_1063.json', 'r') as f:
    data = json.load(f)

print(f"✅ Loaded {len(data)} listings")

# Track progress
total_images = 0
downloaded = 0
failed = 0
lock = threading.Lock()

def download_image(item, index):
    global downloaded, failed, total_images
    
    # Get image URL from various fields
    image_url = None
    if item.get('images') and len(item['images']) > 0:
        image_url = item['images'][0]
    elif item.get('photo'):
        image_url = item['photo']
    elif item.get('imageUrl'):
        image_url = item['imageUrl']
    
    if not image_url:
        return None
    
    # Generate unique filename
    url_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]
    ext = '.jpg'
    if '.png' in image_url.lower():
        ext = '.png'
    elif '.webp' in image_url.lower():
        ext = '.webp'
    
    filename = f"{url_hash}{ext}"
    filepath = f"images/{filename}"
    
    # Skip if already downloaded
    if os.path.exists(filepath):
        with lock:
            downloaded += 1
        return filename
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.loopnet.com/',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
        }
        
        response = requests.get(image_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            with open(filepath, 'wb') as f:
                f.write(response.content)
            with lock:
                downloaded += 1
            print(f"✅ [{index}] Downloaded: {filename}")
            return filename
        else:
            with lock:
                failed += 1
            print(f"❌ [{index}] Failed (status {response.status_code}): {image_url[:60]}...")
            return None
            
    except Exception as e:
        with lock:
            failed += 1
        print(f"❌ [{index}] Error: {str(e)[:50]}")
        return None

# Collect all valid image URLs
items_with_images = []
for i, item in enumerate(data):
    if item.get('images') and len(item['images']) > 0:
        items_with_images.append((item, i))
    elif item.get('photo'):
        items_with_images.append((item, i))
    elif item.get('imageUrl'):
        items_with_images.append((item, i))

total_images = len(items_with_images)
print(f"🖼️ Found {total_images} images to download")
print(f"⏳ Starting download (this may take a few minutes)...\n")

# Download images in parallel
with ThreadPoolExecutor(max_workers=10) as executor:
    futures = {executor.submit(download_image, item, i): (item, i) 
               for item, i in items_with_images}
    
    for future in as_completed(futures):
        future.result()

print(f"\n📊 Download Complete!")
print(f"✅ Successfully downloaded: {downloaded}")
print(f"❌ Failed: {failed}")
print(f"📁 Images saved to: images/")

# Update the JSON with local image paths
print("\n🔄 Updating listings with local image paths...")
for item in data:
    if item.get('images') and len(item['images']) > 0:
        image_url = item['images'][0]
        url_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]
        ext = '.jpg'
        if '.png' in image_url.lower():
            ext = '.png'
        elif '.webp' in image_url.lower():
            ext = '.webp'
        filename = f"{url_hash}{ext}"
        if os.path.exists(f"images/{filename}"):
            item['localImage'] = f"/images/{filename}"
            item['imageStatus'] = 'downloaded'
        else:
            item['imageStatus'] = 'failed'
    elif item.get('photo'):
        image_url = item['photo']
        url_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]
        filename = f"{url_hash}.jpg"
        if os.path.exists(f"images/{filename}"):
            item['localImage'] = f"/images/{filename}"
            item['imageStatus'] = 'downloaded'
        else:
            item['imageStatus'] = 'failed'

# Save updated JSON
with open('listings_1063_with_images.json', 'w') as f:
    json.dump(data, f, indent=2)

print(f"✅ Updated listings saved to: listings_1063_with_images.json")
