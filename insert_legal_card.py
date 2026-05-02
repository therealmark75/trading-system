with open('web/templates/ticker.html', 'r') as f:
    content = f.read()

old = '        <!-- TradingView Chart -->'

new = '''        <!-- Legal & Regulatory Risk Card -->
        <div class="card" id="legal-risk-card">
            <div class="card-title" style="display:flex;align-items:center;justify-content:space-between;">
                <span>⚖️ Legal &amp; Regulatory Risk</span>
                <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={{ ticker }}&type=&dateb=&owner=include&count=10" target="_blank" style="font-size:11px;color:var(--accent);text-decoration:none;opacity:0.8;">SEC EDGAR ↗</a>
            </div>
            {% if legal_risk %}
            {% set lr = legal_risk %}
            <div style="display:inline-flex;align-items:center;gap:8px;padding:5px 12px;border-radius:20px;font-size:13px;font-weight:600;margin-bottom:12px;background:{{ lr.risk_color }}22;border:1.5px solid {{ lr.risk_color }};color:{{ lr.risk_color }};">
                <span style="width:8px;height:8px;border-radius:50%;background:{{ lr.risk_color }};flex-shrink:0;"></span>
                {{ lr.risk_label }}
                {% if lr.penalty != 0 %}
                <span style="font-size:11px;opacity:0.85;margin-left:4px;">{% if lr.penalty > 0 %}+{% endif %}{{ lr.penalty }} pts</span>
                {% endif %}
            </div>
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;font-size:12px;">
                <span style="color:var(--muted);white-space:nowrap;">Score Impact</span>
                <div style="flex:1;height:6px;background:var(--border);border-radius:3px;overflow:hidden;">
                    {% set pct = ((lr.penalty | abs) / 60 * 100) | int %}
                    <div style="height:100%;border-radius:3px;width:{{ [pct,2]|max }}%;background:{% if lr.penalty < 0 %}#ef4444{% elif lr.penalty > 0 %}#22c55e{% else %}#6b7280{% endif %};"></div>
                </div>
                <span style="font-weight:600;min-width:32px;text-align:right;color:{% if lr.penalty < 0 %}#ef4444{% elif lr.penalty > 0 %}#22c55e{% else %}var(--muted){% endif %};">{% if lr.penalty > 0 %}+{% endif %}{{ lr.penalty }}</span>
            </div>
            {% if lr.findings %}
            <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;font-weight:600;">Recent Filings ({{ lr.findings | length }})</div>
            {% for f in lr.findings %}
            <div style="border-left:3px solid {{ lr.risk_color }};padding:7px 10px;margin-bottom:8px;background:var(--bg);border-radius:0 5px 5px 0;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;">
                    <span style="font-size:11px;font-weight:600;color:{{ lr.risk_color }};">{{ f.type }}</span>
                    <span style="font-size:11px;color:var(--muted);">{{ f.date }}</span>
                </div>
                <div style="font-size:12px;color:var(--text);margin-bottom:3px;line-height:1.4;">{{ f.description }}</div>
                {% if f.source_url %}<a href="{{ f.source_url }}" target="_blank" style="font-size:11px;color:var(--accent);text-decoration:none;">View filing ↗</a>{% endif %}
            </div>
            {% endfor %}
            {% else %}
            <div style="font-size:13px;color:var(--muted);text-align:center;padding:10px 0;">No significant legal filings found in the past 2 years.</div>
            {% endif %}
            {% if lr.scraped_at %}<div style="font-size:11px;color:var(--muted2);text-align:right;margin-top:8px;">Updated: {{ lr.scraped_at[:10] }}</div>{% endif %}
            {% else %}
            <div style="font-size:13px;color:var(--muted);text-align:center;padding:10px 0;">Legal risk data unavailable.</div>
            {% endif %}
        </div>

        <!-- TradingView Chart -->'''

if old in content:
    content = content.replace(old, new, 1)
    with open('web/templates/ticker.html', 'w') as f:
        f.write(content)
    print('✅ Legal risk card inserted into ticker.html')
else:
    print('❌ Insertion point not found')
