# 1. Add Flask route for ticker news articles
with open('web/app.py', 'r') as f:
    content = f.read()

new_route = '''
@app.route('/news/<ticker>')
@login_required
def ticker_news(ticker):
    conn = get_connection(DATABASE_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT title, url, source, published_at, sentiment_score
        FROM news_articles
        WHERE ticker = ?
        ORDER BY published_at DESC
        LIMIT 50
    """, (ticker,))
    articles = [dict(r) for r in cur.fetchall()]
    conn.close()
    return render_template('ticker_news.html', ticker=ticker, articles=articles)

'''

# Insert before the last route or before if __name__
if '@app.route(\'/news/<ticker>\')' not in content:
    content = content.replace("if __name__ == '__main__':", new_route + "if __name__ == '__main__':")
    with open('web/app.py', 'w') as f:
        f.write(content)
    print('Route added')
else:
    print('Route already exists')
