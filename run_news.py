import os
from pathlib import Path

p = Path('main.py')
s = p.read_text(encoding='utf-8')
run_mode = (os.getenv('RUN_MODE') or 'digest').strip().lower()

s = s.replace(
    'summary=summary,\n            link=getattr(entry, "link", ""),',
    'summary=clean_text(getattr(entry, "summary", "")),\n            link=getattr(entry, "link", ""),',
    1,
)

s = s.replace(
    '        if int(a.get("score", 0)) >= min_score\n        and a.get("applicability") != "LOW"',
    '        if a.get("applicability") != "LOW"',
    1,
)

s = s.replace(
    '        f"{badge} <b>중요도 {score}</b> · {app}",\n',
    '',
    1,
)

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

if run_mode == 'manual':
    s = s.replace('HISTORY_DAYS = 7', 'HISTORY_DAYS = 30', 1)

p.write_text(s, encoding='utf-8')

code = compile(s, 'main.py', 'exec')
exec(code, {'__name__': '__main__', '__file__': 'main.py'})
