with open('web/app.py', 'r') as f:
    content = f.read()

old = '''def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated'''

new = '''def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            # Return JSON 401 for API routes, redirect for page routes
            if request.path.startswith('/api/'):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated'''

if old in content:
    content = content.replace(old, new)
    with open('web/app.py', 'w') as f:
        f.write(content)
    print("Fixed login_required - API routes now return 401 JSON")
else:
    print("Pattern not found - showing actual decorator:")
    idx = content.find('def login_required')
    print(content[idx:idx+400])
