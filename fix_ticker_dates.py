with open('web/templates/ticker.html', 'r') as f:
    content = f.read()

# Fix 1: transaction_date
old1 = '>${i.transaction_date||\'-\'}</td>'
new1 = '>${fmtDate(i.transaction_date)}</td>'

# Fix 2: h.date
old2 = '>${h.date||\'-\'}</td>'
new2 = '>${fmtDate(h.date)}</td>'

# Add fmtDate function
fmt_fn = """
function fmtDate(d) {
    if(!d) return '-';
    const dt = new Date(d);
    if(isNaN(dt)) return d;
    return dt.toLocaleDateString('en-GB', {day:'numeric', month:'short', year:'2-digit'});
}
"""

if old1 in content:
    content = content.replace(old1, new1)
    print("Fixed transaction_date")
else:
    print("transaction_date not found")

if old2 in content:
    content = content.replace(old2, new2)
    print("Fixed h.date")
else:
    print("h.date not found")

if 'function fmtDate' not in content:
    idx = content.find('function ')
    content = content[:idx] + fmt_fn + content[idx:]
    print("Added fmtDate function")

with open('web/templates/ticker.html', 'w') as f:
    f.write(content)
