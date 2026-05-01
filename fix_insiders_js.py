with open('web/templates/index.html', 'r') as f:
    content = f.read()

old = """async function loadInsiders() {
    const signals = await fetch('/api/insider_signals').then(r=>r.json());
    if(!signals.length){
        document.getElementById('insider-table-content').innerHTML='<div class="loading">No cluster signals in last 14 days</div>';return;
    }"""

new = """async function loadInsiders() {
    let signals;
    try {
        const r = await fetch('/api/insider_signals');
        if(!r.ok){ document.getElementById('insider-table-content').innerHTML='<div class="loading">Unable to load insider signals</div>';return; }
        signals = await r.json();
    } catch(e) {
        document.getElementById('insider-table-content').innerHTML='<div class="loading">Error loading insider signals</div>';return;
    }
    if(!signals || !signals.length){
        document.getElementById('insider-table-content').innerHTML='<div class="loading">No cluster signals in last 14 days</div>';return;
    }"""

if old in content:
    content = content.replace(old, new)
    with open('web/templates/index.html', 'w') as f:
        f.write(content)
    print("Fixed loadInsiders error handling")
else:
    print("Pattern not found - showing actual function:")
    idx = content.find('async function loadInsiders')
    print(content[idx:idx+400])
