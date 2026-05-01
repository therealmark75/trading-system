import re

# Fix 1: System page - wrong table name job_runs -> run_log
with open('web/app.py', 'r') as f:
    content = f.read()

# Fix the job_runs reference in the system route
fixed = content.replace('job_runs', 'run_log')
if fixed != content:
    print(f"Fixed job_runs -> run_log references")
    content = fixed
else:
    print("WARNING: job_runs not found in app.py - check manually")

with open('web/app.py', 'w') as f:
    f.write(content)

# Fix 2: Check insiders template for the API call
with open('web/templates/insiders.html', 'r') as f:
    tmpl = f.read()

# Check how the API is being called
if '/api/insider_signals' in tmpl:
    print("Found /api/insider_signals in insiders.html")
    # Check if it's handling auth errors
    if '401' in tmpl or 'login' in tmpl.lower():
        print("Auth handling exists")
    else:
        print("WARNING: No auth error handling in fetch call")

# Show the fetch call
idx = tmpl.find('/api/insider_signals')
if idx > 0:
    print("\nFetch context:")
    print(tmpl[max(0,idx-200):idx+300])
