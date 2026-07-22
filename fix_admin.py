# Run this to fix app.py
import re

with open('app.py', 'r') as f:
    content = f.read()

# Find the admin blueprint registration block and replace it
pattern = r'# Register admin blueprint with a unique name.*?from admin_dashboard import admin_bp as admin_bp_alt.*?app\.register_blueprint\(admin_bp_alt, url_prefix=\'/admin\', name=\'admin_alt\'\)\n\s*except:.*?print\("⚠️ Could not register admin blueprint"\)\n'

replacement = '''# Admin blueprint - registered in admin_dashboard.py
from admin_dashboard import admin_bp'''

content = re.sub(pattern, replacement, content, flags=re.DOTALL)

with open('app.py', 'w') as f:
    f.write(content)

print("✅ app.py fixed!")
