import os

app_path = os.path.expanduser("~/Documents/trading-system/web/app.py")
with open(app_path, "r") as f:
    content = f.read()

# 1. Add import
if "legal_risk_scraper" not in content:
    old = "from scrapers.screener_scraper import"
    new = "from scrapers.legal_risk_scraper import get_legal_risk, fetch_legal_risk, save_legal_risk\nfrom scrapers.screener_scraper import"
    content = content.replace(old, new, 1)
    print("✅ Import added")
else:
    print("⚠️  Import already present")

with open(app_path, "w") as f:
    f.write(content)

print("✅ app.py import patched")
print()
print(">>> MANUAL STEP REQUIRED <<<")
print("In your ticker route (def ticker / @app.route('/ticker/<ticker>') etc),")
print("add this block BEFORE the return render_template line:")
print()
print("""    legal_risk_data = get_legal_risk(ticker)
    if legal_risk_data is None:
        try:
            result = fetch_legal_risk(ticker)
            save_legal_risk(ticker, result)
            legal_risk_data = result
        except:
            legal_risk_data = {
                'risk_level': 'NONE', 'risk_label': 'Unavailable',
                'risk_color': '#6b7280', 'penalty': 0, 'findings': [],
                'scraped_at': None,
            }
""")
print("Then add  legal_risk=legal_risk_data  to the render_template kwargs.")
