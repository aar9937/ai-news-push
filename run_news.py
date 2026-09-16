import os
from pathlib import Path

p = Path('main.py')
s = p.read_text(encoding='utf-8')
run_mode = (os.getenv('RUN_MODE') or 'digest').strip().lower()

# Google News RSS summary bug: fix before execution.
s = s.replace(
    'summary=summary,\n            link=getattr(entry, "link", ""),',
    'summary=clean_text(getattr(entry, "summary", "")),\n            link=getattr(entry, "link", ""),',
    1,
)

# Do not drop otherwise useful articles only because an AI score is below a fixed threshold.
s = s.replace(
    '        if int(a.get("score", 0)) >= min_score\n        and a.get("applicability") != "LOW"',
    '        if a.get("applicability") != "LOW"',
    1,
)

# Telegram output does not show the internal importance score.
s = s.replace(
    '        f"{badge} <b>중요도 {score}</b> · {app}",\n',
    '',
    1,
)

# Always load extra_topics.json.  Manual mode widens the time window.
old_loader = '''def load_topics():
    topics = load_json(CONFIG_FILE, [])
    if not isinstance(topics, list) or not topics:
        raise ValueError("topics.json에 주제가 없습니다.")
    return topics'''
new_loader = '''def load_topics():
    topics = load_json(CONFIG_FILE, [])
    if not isinstance(topics, list) or not topics:
        raise ValueError("topics.json에 주제가 없습니다.")
    extra_topics = load_json("extra_topics.json", [])
    if isinstance(extra_topics, list):
        topics.extend(extra_topics)
    if RUN_MODE == "manual":
        for topic in topics:
            topic["lookback_hours"] = 720
    return topics'''
if old_loader in s:
    s = s.replace(old_loader, new_loader, 1)

# Broaden discovery for future-industry/company-selection stories.  These are
# deliberately broad candidate queries; the topic rules/AI still filter the
# final Telegram selection, which improves recall without sending every hit.
needle = '    encoded = urllib.parse.quote(term)\n    url = (\n        f"https://news.google.com/rss/search?q={encoded}"'
replacement = '''    discovery_term = term
    broad_future_terms = (
        "유망기업", "미래산업", "스타트업", "로보틱스", "AI반도체",
        "인공지능 기업", "피지컬 AI", "Physical AI", "투자 협업",
    )
    if any(k.lower() in term.lower() for k in broad_future_terms):
        discovery_term = f"{term} OR 선정 OR 투자 OR 협업 OR 사업화"
    encoded = urllib.parse.quote(discovery_term)
    url = (
        f"https://news.google.com/rss/search?q={encoded}"'''
if needle in s:
    s = s.replace(needle, replacement, 1)

if run_mode == 'manual':
    s = s.replace('HISTORY_DAYS = 7', 'HISTORY_DAYS = 30', 1)

p.write_text(s, encoding='utf-8')

code = compile(s, 'main.py', 'exec')
exec(code, {'__name__': '__main__', '__file__': 'main.py'})
