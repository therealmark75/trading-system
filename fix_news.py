with open('web/templates/ticker.html', 'r') as f:
    content = f.read()

old = '''            ${n.url ? `<a href="${n.url}" target="_blank" rel="noopener">${n.headline||'-'}</a>` : (n.headline||'-')}
              </div>
              <div class="news-meta">
                <span class="${sc2}">${sLabel}</span>
                <span>${n.source||''}</span>
                <span>${n.published||''}</span>'''

new = '''            ${n.url ? `<a href="${n.url.startsWith('http') ? n.url : 'https://finviz.com'+n.url}" target="_blank" rel="noopener">${n.headline||'-'}</a>` : (n.headline||'-')}
              </div>
              <div class="news-meta">
                <span class="${sc2}">${sLabel}</span>
                <span>${n.source||''}</span>
                <span>${fmtDate(n.published)}</span>'''

if old in content:
    content = content.replace(old, new)
    with open('web/templates/ticker.html', 'w') as f:
        f.write(content)
    print("Fixed news URLs and dates")
else:
    print("Pattern not found - showing exact context:")
    idx = content.find('n.url ? `<a href=')
    print(repr(content[idx:idx+300]))
