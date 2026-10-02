import os
from pathlib import Path

p = Path('main.py')
s = p.read_text(encoding='utf-8')
run_mode = (os.getenv('RUN_MODE') or 'digest').strip().lower()

# Repair Google News RSS summary extraction.
s = s.replace(
    'summary=summary,\n            link=getattr(entry, "link", ""),',
    'summary=clean_text(getattr(entry, "summary", "")),\n            link=getattr(entry, "link", ""),',
    1,
)

# Do not drop relevant articles solely due to a fixed AI score cutoff.
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

# Broaden future-industry search discovery.
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

# Naver official API Hub first; if its credentials are missing/invalid or the
# endpoint errors, fall back to Naver's public news RSS search feed.
start = s.find('def fetch_naver_news(')
end = s.find('\ndef fetch_official_feed(', start)
if start >= 0 and end > start:
    naver_func = '''def fetch_naver_news(term, lookback_hours, sent_fingerprints, sent_titles):
    cutoff = datetime.now(KST) - timedelta(hours=lookback_hours)
    results = []
    items = []
    if NAVER_CLIENT_ID and NAVER_CLIENT_SECRET:
        try:
            headers = {"X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID, "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET}
            params = {"query": term, "display": min(100, max(10, ARTICLES_PER_SEARCH_TERM * 5)), "sort": "date"}
            response = requests.get("https://naverapihub.apigw.ntruss.com/search/v1/news", headers=headers, params=params, timeout=20)
            response.raise_for_status()
            items = response.json().get("items", [])
            print(f"    Naver API HUB {term}: {len(items)} raw")
        except Exception as exc:
            print(f"    [Naver API HUB 실패, RSS 대체] {term}: {exc}")
    if not items:
        try:
            response = requests.get("https://search.naver.com/search.naver", params={"where": "rss", "query": term}, headers={"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}, timeout=20)
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            for entry in parsed.entries:
                items.append({"title": getattr(entry, "title", ""), "description": getattr(entry, "summary", ""), "originallink": getattr(entry, "link", ""), "link": getattr(entry, "link", ""), "pubDate": getattr(entry, "published", ""), "_published_parsed": getattr(entry, "published_parsed", None)})
            print(f"    Naver RSS {term}: {len(items)} raw")
        except Exception as exc:
            print(f"    [Naver RSS 실패] {term}: {exc}")
            return []
    for item in items:
        title = clean_text(item.get("title", ""))
        if not title or is_blocked_title(title) or candidate_is_old_duplicate(title, sent_fingerprints, sent_titles):
            continue
        published_dt = None
        try:
            parsed = item.get("_published_parsed")
            if parsed:
                published_dt = datetime(*parsed[:6], tzinfo=ZoneInfo("UTC")).astimezone(KST)
            elif item.get("pubDate"):
                published_dt = parsedate_to_datetime(item.get("pubDate", "")).astimezone(KST)
        except Exception:
            pass
        if published_dt and published_dt < cutoff:
            continue
        link = item.get("originallink") or item.get("link") or ""
        domain = urlparse(link).netloc.replace("www.", "")
        results.append(make_article(title=title, source=domain or "네이버 뉴스", summary=item.get("description", ""), link=link, published_dt=published_dt, matched_term=term, source_type="naver_news", is_official=False))
        if len(results) >= ARTICLES_PER_SEARCH_TERM:
            break
    return results
'''
    s = s[:start] + naver_func + s[end:]

# Remove all Gemini dependence. Deterministic local scoring/selection only.
score_start = s.find('def score_articles(')
score_end = s.find('\ndef score_badge(', score_start)
if score_start >= 0 and score_end > score_start:
    deterministic_score = '''def score_articles(topic, articles, profile):
    if not articles:
        return []
    result = []
    seen = set()
    topic_name = clean_text(topic.get("name", ""))
    postal_topic = "한국우편사업진흥원" in topic_name
    relocation_words = ("지방이전", "지방 이전", "공공기관 이전", "2차 공공기관", "이전 대상", "이전 후보", "혁신도시", "본사 이전", "기관 이전")
    for article in articles:
        title = clean_text(article.get("title", ""))
        summary = clean_text(article.get("summary", article.get("description", "")))
        link = article.get("link", "")
        key = (title.lower().strip(), link.split("?")[0])
        if not title or key in seen or is_blocked_title(title):
            continue
        seen.add(key)
        text_blob = (title + " " + summary).lower()
        if any(x in text_blob for x in ("광고", "협찬", "체험단", "구인구직", "채용공고")):
            continue
        # This category is intentionally strict: the exact agency AND an actual
        # relocation concept must both appear in the article text. This blocks
        # unrelated organizations whose names merely contain '진흥원'.
        if postal_topic:
            if "한국우편사업진흥원" not in (title + " " + summary):
                continue
            if not any(word in (title + " " + summary) for word in relocation_words):
                continue
        enriched = article.copy()
        enriched.update({"score": 60, "urgency": "NORMAL", "applicability": "GENERAL", "ai_summary": summary[:160] if summary else title, "why": "제목·기사 요약 및 주제 일치 기준 자동 선별", "action": ""})
        result.append(enriched)
    return result
'''
    s = s[:score_start] + deterministic_score + s[score_end:]

# Remove Gemini configuration and prevent accidental API invocation.
s = s.replace('GEMINI_API_KEY', 'GEMINI_API_KEY_DISABLED')
if run_mode == 'manual':
    s = s.replace('HISTORY_DAYS = 7', 'HISTORY_DAYS = 30', 1)

p.write_text(s, encoding='utf-8')
code = compile(s, 'main.py', 'exec')
exec(code, {'__name__': '__main__', '__file__': 'main.py'})
