with open('web/templates/index.html', 'r') as f:
    content = f.read()

old = '''    insiderSignals = signals;
    const rows = paged.map(s => {'''

new = '''    insiderSignals = signals;
    const paged = signals;
    const rows = paged.map(s => {'''

if old in content:
    content = content.replace(old, new)
    with open('web/templates/index.html', 'w') as f:
        f.write(content)
    print("Fixed: paged now defined for insiders")
else:
    print("Pattern not found - check spacing")
    idx = content.find('insiderSignals = signals')
    print(repr(content[idx:idx+100]))
