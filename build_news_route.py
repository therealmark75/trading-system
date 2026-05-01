# 1. Add Flask route
with open('web/app.py', 'r') as f:
    content = f.read()

new_route = '''
@app.route('/news/<ticker>')
@login_required
def ticker_news(ticker):
    conn = get_connection(DATABASE_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT headline, source, published, sentiment, url
        FROM news_sentiment
        WHERE ticker = ?
        ORDER BY scraped_at DESC
        LIMIT 50
    """, (ticker.upper(),))
    articles = [dict(r) for r in cur.fetchall()]
    conn.close()
    return render_template('ticker_news.html', ticker=ticker.upper(), articles=articles)

'''

if "route('/news/<ticker>')" not in content:
    content = content.replace("if __name__ == '__main__':", new_route + "if __name__ == '__main__':")
    with open('web/app.py', 'w') as f:
        f.write(content)
    print('Route added')
else:
    print('Route already exists')

# 2. Create ticker_news.html template
template = '''{% extends "base.html" %}
{% block content %}
<div style="max-width:1100px;margin:40px auto;padding:0 20px">
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:32px">
        <a href="/ticker/{{ ticker }}" style="color:var(--muted);text-decoration:none;font-size:13px">← Back to {{ ticker }}</a>
        <h1 style="color:var(--cyan);font-family:var(--mono);margin:0">{{ ticker }} — News Articles</h1>
        <span style="color:var(--muted);font-size:13px">({{ articles|length }} articles)</span>
    </div>

    {% if articles %}
    <div style="display:flex;flex-direction:column;gap:12px">
        {% for a in articles %}
        <a href="{{ 'https://finviz.com' + a.url if a.url and a.url.startswith('/') else a.url or '#' }}"
           target="_blank" rel="noopener"
           style="display:block;background:var(--card);border:1px solid var(--border);border-radius:8px;
                  padding:16px 20px;text-decoration:none;transition:border-color 0.2s"
           onmouseover="this.style.borderColor='var(--cyan)'"
           onmouseout="this.style.borderColor='var(--border)'">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px">
                <div style="flex:1">
                    <div style="color:var(--text);font-size:15px;font-weight:500;margin-bottom:6px;line-height:1.4">
                        {{ a.headline }}
                    </div>
                    <div style="display:flex;gap:16px;align-items:center">
                        <span style="color:var(--muted);font-size:12px">{{ a.source or 'Unknown' }}</span>
                        <span style="color:var(--muted2);font-size:12px">{{ a.published or '' }}</span>
                    </div>
                </div>
                <div style="flex-shrink:0;text-align:right">
                    {% if a.sentiment > 0.1 %}
                        <span style="color:#00ff88;font-family:var(--mono);font-size:13px;font-weight:700">+{{ "%.3f"|format(a.sentiment) }}</span>
                        <div style="color:#00ff88;font-size:10px;margin-top:2px">BULLISH</div>
                    {% elif a.sentiment < -0.1 %}
                        <span style="color:#ff3b5c;font-family:var(--mono);font-size:13px;font-weight:700">{{ "%.3f"|format(a.sentiment) }}</span>
                        <div style="color:#ff3b5c;font-size:10px;margin-top:2px">BEARISH</div>
                    {% else %}
                        <span style="color:var(--muted);font-family:var(--mono);font-size:13px">{{ "%.3f"|format(a.sentiment) }}</span>
                        <div style="color:var(--muted);font-size:10px;margin-top:2px">NEUTRAL</div>
                    {% endif %}
                </div>
            </div>
        </a>
        {% endfor %}
    </div>
    {% else %}
    <div style="color:var(--muted);text-align:center;padding:60px">No articles found for {{ ticker }}</div>
    {% endif %}
</div>
{% endblock %}'''

with open('web/templates/ticker_news.html', 'w') as f:
    f.write(template)
print('Template created')

# 3. Make article count clickable in index.html
with open('web/templates/index.html', 'r') as f:
    content = f.read()

# Find the article_count cell in news sentiment table and make it a link
old = "s.article_count || ' articles'"
new = "'<a href=\"/news/' + s.ticker + '\" target=\"_blank\" style=\"color:var(--cyan);text-decoration:none;font-weight:600\">' + s.article_count + '</a>'"

if old in content:
    content = content.replace(old, new)
    with open('web/templates/index.html', 'w') as f:
        f.write(content)
    print('Article count made clickable')
else:
    print('Pattern not found - need to check index.html manually')
    # Find the news table rendering
    idx = content.find('article_count')
    if idx > 0:
        print('Context:', content[idx-100:idx+100])
