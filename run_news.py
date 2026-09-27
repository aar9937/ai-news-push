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

# If Gemini quota/network fails, keep collected candidates with conservative
# non-AI summaries rather than dropping the entire category.
s = s.replace(
    '''        print(f"  [AI 평가 실패 - 전부 제외] {exc}")
        return []''',
    '''        print(f"  [AI 평가 실패 - 기본 선별로 대체] {exc}")
        fallback = []
        for article in articles:
            title = clean_text(article.get("title", ""))
            summary = clean_text(article.get("summary", ""))
            text_blob = f"{title} {summary}".lower()
            # Exclude obvious promotional/recruitment/duplicative noise.
            if not title or any(x in text_blob for x in ("보도자료 배포", "광고", "협찬", "구인", "채용공고")):
                continue
            item = article.copy()
            item.update({
                "score": 60,
                "urgency": "NORMAL",
                "applicability": "GENERAL",
                "ai_summary": summary[:160] or title,
                "why": "AI 평가 한도 초과로 원문 제목·요약 기준으로 전달",
                "action": "",
            })
            fallback.append(item)
        return fallback''',
    1,
)

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

    # Preferred source: NAVER API HUB. Credentials must be API HUB keys,
    # not legacy developer-center keys.
    if NAVER_CLIENT_ID and NAVER_CLIENT_SECRET:
        try:
            headers = {
                "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
                "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
            }
            params = {
                "query": term,
                "display": min(100, max(10, ARTICLES_PER_SEARCH_TERM * 5)),
                "sort": "date",
            }
            response = requests.get(
                "https://naverapihub.apigw.ntruss.com/search/v1/news",
                headers=headers,
                params=params,
                timeout=20,
            )
            response.raise_for_status()
            items = response.json().get("items", [])
            print(f"    Naver API HUB {term}: {len(items)} raw")
        except Exception as exc:
            print(f"    [Naver API HUB 실패, RSS 대체] {term}: {exc}")

    # Public Naver news RSS does not require API keys.
    if not items:
        try:
            rss_url = "https://search.naver.com/search.naver"
            response = requests.get(
                rss_url,
                params={"where": "rss", "query": term},
                headers={"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"},
                timeout=20,
            )
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            for entry in parsed.entries:
                items.append({
                    "title": getattr(entry, "title", ""),
                    "description": getattr(entry, "summary", ""),
                    "originallink": getattr(entry, "link", ""),
                    "link": getattr(entry, "link", ""),
                    "pubDate": getattr(entry, "published", ""),
                    "_published_parsed": getattr(entry, "published_parsed", None),
                })
            print(f"    Naver RSS {term}: {len(items)} raw")
        except Exception as exc:
            print(f"    [Naver RSS 실패] {term}: {exc}")
            return []

    for item in items:
        title = clean_text(item.get("title", ""))
        if not title or is_blocked_title(title):
            continue
        if candidate_is_old_duplicate(title, sent_fingerprints, sent_titles):
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
        results.append(make_article(
            title=title,
            source=domain or "네이버 뉴스",
            summary=item.get("description", ""),
            link=link,
            published_dt=published_dt,
            matched_term=term,
            source_type="naver_news",
            is_official=False,
        ))
        if len(results) >= ARTICLES_PER_SEARCH_TERM:
            break

    return results
'''
    s = s[:start] + naver_func + s[end:]

if run_mode == 'manual':
    s = s.replace('HISTORY_DAYS = 7', 'HISTORY_DAYS = 30', 1)

p.write_text(s, encoding='utf-8')
code = compile(s, 'main.py', 'exec')
exec(code, {'__name__': '__main__', '__file__': 'main.py'})
