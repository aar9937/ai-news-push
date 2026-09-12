import os
import re
import html
import json
import hashlib
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import feedparser
import requests
from google import genai

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

KST = ZoneInfo("Asia/Seoul")

RUN_MODE = (os.getenv("RUN_MODE") or "digest").strip().lower()
AI_PROVIDER = (os.getenv("AI_PROVIDER") or "gemini").strip().lower()

GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-3.6-flash"
OPENAI_MODEL = os.getenv("OPENAI_MODEL") or "gpt-5.6-luna"

HISTORY_DAYS = 7
DEFAULT_LOOKBACK_HOURS = 72
ARTICLES_PER_SEARCH_TERM = 4
MAX_CANDIDATES_PER_TOPIC = 14
DEFAULT_MAX_SELECTED = 2
DEFAULT_MIN_SCORE = 70
URGENT_SCORE = 92

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "").strip()
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "").strip()

INCHEON_API_KEY = os.getenv("INCHEON_API_KEY", "").strip()

CONFIG_FILE = "topics.json"
PROFILE_FILE = "profile.json"
SENT_FILE = "sent_news.json"

BLOCK_TITLE_WORDS = [
    "협찬",
    "광고",
    "브랜드 캠페인",
]

OFFICIAL_FEEDS = [
    {
        "name": "보건복지부 보도자료",
        "url": "https://www.mohw.go.kr/rss/board.es?mid=a10503000000&bid=0027&info",
        "topics": ["🎁 혜택·할인·지원금", "👶 영유아·육아"],
    },
    {
        "name": "보건복지부 고시·지침",
        "url": "https://www.mohw.go.kr/rss/board.es?mid=a10409020000&update=&cg_code=&bid=0026",
        "topics": ["🎁 혜택·할인·지원금", "👶 영유아·육아"],
    },
    {
        "name": "고용노동부 정책자료",
        "url": "https://www.moel.go.kr/rss/policy.do",
        "topics": ["🎁 혜택·할인·지원금", "💼 직장인·4대보험·세금"],
    },
    {
        "name": "고용노동부 알려드립니다",
        "url": "https://www.moel.go.kr/rss/notice.do",
        "topics": ["🎁 혜택·할인·지원금", "💼 직장인·4대보험·세금"],
    },
    {
        "name": "고용노동부 입법·행정예고",
        "url": "https://www.moel.go.kr/rss/lawinfo.do",
        "topics": ["💼 직장인·4대보험·세금"],
    },
]


def clean_text(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def load_topics():
    topics = load_json(CONFIG_FILE, [])
    if not isinstance(topics, list) or not topics:
        raise ValueError("topics.json에 주제가 없습니다.")
    return topics


def load_profile():
    profile = load_json(PROFILE_FILE, {})
    if not isinstance(profile, dict):
        return {}
    return profile


def profile_text(profile):
    return json.dumps(profile, ensure_ascii=False, indent=2)


def normalize_title(title):
    title = re.sub(r"\s*-\s*[^-]{1,40}$", "", clean_text(title))
    title = re.sub(r"[^0-9A-Za-z가-힣]", "", title)
    return title.lower()


def tokenize_title(title):
    title = re.sub(r"\s*-\s*[^-]{1,40}$", "", clean_text(title))
    return {
        w.lower()
        for w in re.findall(r"[0-9A-Za-z가-힣]+", title)
        if len(w) >= 2
    }


def article_fingerprint(title):
    return hashlib.sha256(
        normalize_title(title).encode("utf-8")
    ).hexdigest()[:20]


def title_is_similar(a, b):
    na = normalize_title(a)
    nb = normalize_title(b)

    if not na or not nb:
        return False
    if na == nb:
        return True

    ratio = SequenceMatcher(None, na, nb).ratio()
    ta = tokenize_title(a)
    tb = tokenize_title(b)

    if ratio >= 0.76:
        return True

    if ta and tb:
        union = len(ta | tb)
        jaccard = len(ta & tb) / union if union else 0

        if jaccard >= 0.50 and ratio >= 0.55:
            return True

        long_common = {w for w in (ta & tb) if len(w) >= 4}
        if len(long_common) >= 2 and ratio >= 0.50:
            return True

    return False


def is_blocked_title(title):
    return any(word in title for word in BLOCK_TITLE_WORDS)


def load_sent_history():
    raw = load_json(SENT_FILE, {})
    if not isinstance(raw, dict):
        raw = {}

    today = datetime.now(KST).date()
    cutoff = today - timedelta(days=HISTORY_DAYS - 1)

    history = {}
    fingerprints = set()
    titles = []

    for date_str, items in raw.items():
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if not (cutoff <= day <= today) or not isinstance(items, list):
            continue

        kept = []

        for item in items:
            if isinstance(item, str):
                fingerprints.add(item)
                kept.append(item)
                continue

            if not isinstance(item, dict):
                continue

            fp = str(item.get("fingerprint", "")).strip()
            title = clean_text(item.get("title", ""))

            if not fp and title:
                fp = article_fingerprint(title)

            if fp:
                fingerprints.add(fp)
            if title:
                titles.append(title)

            kept.append({
                "fingerprint": fp,
                "title": title,
            })

        history[date_str] = kept

    return history, fingerprints, titles


def save_sent_history(history, articles):
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    current = history.get(today_str, [])
    existing = set()
    output = []

    for item in current:
        if isinstance(item, str):
            existing.add(item)
            output.append(item)
        elif isinstance(item, dict):
            fp = str(item.get("fingerprint", "")).strip()
            if fp:
                existing.add(fp)
            output.append(item)

    for article in articles:
        fp = article["fingerprint"]
        if fp in existing:
            continue

        output.append({
            "fingerprint": fp,
            "title": article["title"],
        })
        existing.add(fp)

    history[today_str] = output

    today = datetime.now(KST).date()
    cutoff = today - timedelta(days=HISTORY_DAYS - 1)

    compact = {}
    for date_str, items in history.items():
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if cutoff <= day <= today:
            compact[date_str] = items

    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(compact, f, ensure_ascii=False, indent=2)


def candidate_is_old_duplicate(title, sent_fingerprints, sent_titles):
    fp = article_fingerprint(title)
    if fp in sent_fingerprints:
        return True

    return any(
        title_is_similar(title, old_title)
        for old_title in sent_titles
    )


def make_article(
    title,
    source,
    summary,
    link,
    published_dt,
    matched_term="",
    source_type="news",
    is_official=False,
):
    return {
        "title": clean_text(title),
        "source": clean_text(source) or "출처 미상",
        "summary": clean_text(summary)[:800],
        "link": str(link or "").strip(),
        "published": published_dt.strftime("%m/%d %H:%M") if published_dt else "",
        "published_ts": int(published_dt.timestamp()) if published_dt else 0,
        "fingerprint": article_fingerprint(title),
        "matched_term": matched_term,
        "source_type": source_type,
        "is_official": bool(is_official),
    }


def fetch_google_news(term, lookback_hours, sent_fingerprints, sent_titles):
    encoded = urllib.parse.quote(term)
    url = (
        f"https://news.google.com/rss/search?q={encoded}"
        "&hl=ko&gl=KR&ceid=KR:ko"
    )

    feed = feedparser.parse(url)
    cutoff = datetime.now(KST) - timedelta(hours=lookback_hours)
    results = []

    for entry in feed.entries:
        title = clean_text(getattr(entry, "title", ""))

        if not title or is_blocked_title(title):
            continue
        if candidate_is_old_duplicate(title, sent_fingerprints, sent_titles):
            continue

        published_dt = None

        if getattr(entry, "published_parsed", None):
            published_dt = datetime(
                *entry.published_parsed[:6],
                tzinfo=ZoneInfo("UTC"),
            ).astimezone(KST)

            if published_dt < cutoff:
                continue

        source = ""
        if getattr(entry, "source", None):
            source = clean_text(getattr(entry.source, "title", ""))

        results.append(make_article(
            title=title,
            source=source,
            summary=getattr(entry, "summary", ""),
            link=getattr(entry, "link", ""),
            published_dt=published_dt,
            matched_term=term,
            source_type="google_news",
            is_official=False,
        ))

        if len(results) >= ARTICLES_PER_SEARCH_TERM:
            break

    return results


def fetch_naver_news(term, lookback_hours, sent_fingerprints, sent_titles):
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return []

    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {
        "query": term,
        "display": ARTICLES_PER_SEARCH_TERM,
        "sort": "date",
    }

    r = requests.get(
        "https://openapi.naver.com/v1/search/news.json",
        headers=headers,
        params=params,
        timeout=20,
    )
    r.raise_for_status()

    cutoff = datetime.now(KST) - timedelta(hours=lookback_hours)
    results = []

    for item in r.json().get("items", []):
        title = clean_text(item.get("title", ""))

        if not title or is_blocked_title(title):
            continue
        if candidate_is_old_duplicate(title, sent_fingerprints, sent_titles):
            continue

        published_dt = None
        try:
            published_dt = parsedate_to_datetime(item.get("pubDate", "")).astimezone(KST)
        except Exception:
            pass

        if published_dt and published_dt < cutoff:
            continue

        link = item.get("originallink") or item.get("link") or ""
        domain = urlparse(link).netloc.replace("www.", "")

        results.append(make_article(
            title=title,
            source=domain or "네이버 뉴스검색",
            summary=item.get("description", ""),
            link=link,
            published_dt=published_dt,
            matched_term=term,
            source_type="naver_news",
            is_official=False,
        ))

    return results


def fetch_official_feed(feed_config, lookback_hours, sent_fingerprints, sent_titles):
    feed = feedparser.parse(feed_config["url"])
    cutoff = datetime.now(KST) - timedelta(hours=lookback_hours)
    results = []

    for entry in feed.entries[:20]:
        title = clean_text(getattr(entry, "title", ""))

        if not title:
            continue
        if candidate_is_old_duplicate(title, sent_fingerprints, sent_titles):
            continue

        parsed = (
            getattr(entry, "published_parsed", None)
            or getattr(entry, "updated_parsed", None)
        )

        published_dt = None
        if parsed:
            published_dt = datetime(
                *parsed[:6],
                tzinfo=ZoneInfo("UTC"),
            ).astimezone(KST)

            if published_dt < cutoff:
                continue

        results.append(make_article(
            title=title,
            source=feed_config["name"],
            summary=getattr(entry, "summary", ""),
            link=getattr(entry, "link", ""),
            published_dt=published_dt,
            matched_term="공식자료",
            source_type="official_rss",
            is_official=True,
        ))

    return results


def fetch_incheon_official(lookback_hours, sent_fingerprints, sent_titles):
    if not INCHEON_API_KEY:
        return []

    cutoff = datetime.now(KST) - timedelta(hours=lookback_hours)
    results = []

    configs = [
        {
            "apicode": "11",
            "params": {},
            "source": "인천광역시 새소식",
            "list_url": "https://www.incheon.go.kr/IC010101",
            "title_field": "sj",
            "summary_fields": ["summary", "cn"],
        },
        {
            "apicode": "18",
            "params": {"repType": "Y"},
            "source": "인천광역시 보도자료",
            "list_url": "https://www.incheon.go.kr/IC010205",
            "title_field": "title",
            "summary_fields": ["subTitle", "cn"],
        },
    ]

    for cfg in configs:
        params = {
            "apicode": cfg["apicode"],
            "key": INCHEON_API_KEY,
            "page": 1,
            **cfg["params"],
        }

        try:
            r = requests.get(
                "https://www.incheon.go.kr/dp/openapi/data",
                params=params,
                timeout=20,
            )
            r.raise_for_status()
            root = ET.fromstring(r.text)
        except Exception as exc:
            print(f"[인천API 실패] {cfg['source']}: {exc}")
            continue

        for item in root.findall(".//item")[:20]:
            title = clean_text(item.findtext(cfg["title_field"]) or "")
            if not title:
                continue
            if candidate_is_old_duplicate(title, sent_fingerprints, sent_titles):
                continue

            date_text = clean_text(item.findtext("writngDe") or "")
            published_dt = None

            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d", "%Y%m%d"):
                try:
                    published_dt = datetime.strptime(date_text, fmt).replace(tzinfo=KST)
                    break
                except ValueError:
                    pass

            if published_dt and published_dt < cutoff:
                continue

            summary_parts = [
                clean_text(item.findtext(field) or "")
                for field in cfg["summary_fields"]
            ]
            summary = " ".join(x for x in summary_parts if x)

            results.append(make_article(
                title=title,
                source=cfg["source"],
                summary=summary,
                link=cfg["list_url"],
                published_dt=published_dt,
                matched_term="인천 공식자료",
                source_type="incheon_official",
                is_official=True,
            ))

    return results


def dedupe_candidates(articles, limit=MAX_CANDIDATES_PER_TOPIC):
    articles = sorted(
        articles,
        key=lambda x: x.get("published_ts", 0),
        reverse=True,
    )

    result = []

    for article in articles:
        if any(
            title_is_similar(article["title"], saved["title"])
            for saved in result
        ):
            continue

        result.append(article)

        if len(result) >= limit:
            break

    return result


def collect_topic_articles(topic, sent_fingerprints, sent_titles):
    lookback_hours = int(topic.get("lookback_hours", DEFAULT_LOOKBACK_HOURS))
    merged = []

    for term in topic.get("search_terms", []):
        term = str(term).strip()
        if not term:
            continue

        try:
            google = fetch_google_news(
                term,
                lookback_hours,
                sent_fingerprints,
                sent_titles,
            )
            merged.extend(google)
            print(f"    Google {term}: {len(google)}건")
        except Exception as exc:
            print(f"    [Google 실패] {term}: {exc}")

        try:
            naver = fetch_naver_news(
                term,
                lookback_hours,
                sent_fingerprints,
                sent_titles,
            )
            merged.extend(naver)
            if NAVER_CLIENT_ID:
                print(f"    Naver {term}: {len(naver)}건")
        except Exception as exc:
            print(f"    [Naver 실패] {term}: {exc}")

    topic_name = topic.get("name", "")

    for feed_cfg in OFFICIAL_FEEDS:
        if topic_name not in feed_cfg["topics"]:
            continue

        try:
            official = fetch_official_feed(
                feed_cfg,
                lookback_hours,
                sent_fingerprints,
                sent_titles,
            )
            merged.extend(official)
            print(f"    공식 {feed_cfg['name']}: {len(official)}건")
        except Exception as exc:
            print(f"    [공식 RSS 실패] {feed_cfg['name']}: {exc}")

    if topic_name in {
        "🎁 혜택·할인·지원금",
        "🏙️ 송도·인천",
        "🎪 행사·축제·공연",
    }:
        incheon = fetch_incheon_official(
            lookback_hours,
            sent_fingerprints,
            sent_titles,
        )
        merged.extend(incheon)

    return dedupe_candidates(merged)


def ai_generate(prompt):
    if AI_PROVIDER == "openai":
        if not OPENAI_API_KEY:
            raise RuntimeError("AI_PROVIDER=openai인데 OPENAI_API_KEY가 없습니다.")
        if OpenAI is None:
            raise RuntimeError("openai 패키지를 불러올 수 없습니다.")

        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
        )
        return (response.output_text or "").strip()

    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY가 없습니다.")

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    return (response.text or "").strip()


def scoring_prompt(topic, articles, profile):
    rows = []

    for idx, article in enumerate(articles):
        rows.append(
            f"[{idx}]\n"
            f"제목: {article['title']}\n"
            f"출처: {article['source']}\n"
            f"공식자료: {'예' if article['is_official'] else '아니오'}\n"
            f"발행: {article['published']}\n"
            f"검색어: {article['matched_term']}\n"
            f"설명: {article['summary']}\n"
        )

    rules = topic.get("rules", "")
    priority = int(topic.get("priority", 5))

    return f"""
너는 개인 맞춤 뉴스 편집자다.
각 기사를 단순히 흥미롭다는 이유로 높게 평가하지 말고 실제 행동가치와 개인 관련성을 판단한다.

[사용자 프로필]
{profile_text(profile)}

[현재 주제]
{topic['name']}

[주제 선별 기준]
{rules}

[주제 우선순위]
10점 만점 중 {priority}

각 후보를 아래 5개 축으로 평가하고 합계 0~100점을 부여한다.

1. 개인 관련성 0~30
- 사용자 조건에 직접 해당할 가능성이 높을수록 높게.
- 사용자에게 필요한 소득·재산·가입기간 등 정보가 없으면 '확인 필요'로 판단하고 과도하게 점수를 주지 않는다.

2. 행동 가능성 0~25
- 신청, 예약, 가입, 변경, 확인 등 지금 할 일이 구체적이면 높게.
- 단순 전망·의견·홍보는 낮게.

3. 실제 영향 0~20
- 받을 수 있는 금액, 할인, 비용절감, 근로조건, 자녀 보육/안전, 주거·자산 판단에 미치는 영향이 클수록 높게.

4. 시급성 0~15
- 마감 임박, 선착순, 제도 시행 직전, 즉시 안전조치처럼 시간이 중요하면 높게.
- 단순 신제품·장기 산업전망은 낮게.

5. 출처 신뢰도 0~10
- 정부·지자체·공공기관 공식자료는 높게.
- 신뢰할 만한 언론은 중간 이상.
- 출처가 불명확하거나 홍보성이 강하면 낮게.

[점수 해석]
92~100: 긴급. 놓치면 실제 손해/기회상실 가능성이 크고 빠른 행동이 필요함.
82~91: 매우 중요. 정기 알림 최상단.
72~81: 볼 가치 높음.
60~71: 참고할 만하지만 우선순위 낮음.
0~59: 보통은 보내지 않음.

[카테고리별 기준]
- 혜택·지원금: 대상·금액·마감·신청방법이 구체적이고 사용자에게 해당 가능성이 높으면 점수를 크게 올린다.
- 영유아: 2024년생 아이에게 직접 적용되는 보육·지원·공식 건강/안전 안내를 우선한다.
- 직장인: 4대보험 직장인의 보험료·세금·육아휴직·근로시간·지원제도 변화 우선.
- 예적금: 실제 가입 가능한 금리·한도·기간·마감이 있는 상품 우선. 단순 금리 전망은 낮게.
- 행사: 아이와 갈 수 있고 인천/수도권이며 날짜·장소·비용이 구체적이면 높게. 일반 행사는 긴급으로 분류하지 않는다.
- AI·미래산업: 실제 출시·상용화·투자·정책 확정은 높게, 막연한 전망은 낮게.
- 부동산: 인천·송도·금정역·서울의 실제 정책, 정비구역 지정, 분양, GTX, 거래/대출규제 변화는 높게. 칼럼은 낮게.
- 급락·시장기회: 실제 가격 하락이 기사로 확인되고 낙폭·가격·원인이 구체적일수록 높게. 단순 전망이나 소폭 하락은 낮게. 92점 이상 긴급은 시장에서 이례적 급락으로 보도되고 실제 낙폭도 큰 경우만 사용한다.

[긴급 판정]
urgency는 IMMEDIATE / SOON / NORMAL 중 하나.
IMMEDIATE는 원칙적으로 92점 이상이며 1~3일 안에 행동하지 않으면 손해가 생길 수 있는 경우만 사용한다.
일반 뉴스, 산업전망, 단순 신제품은 IMMEDIATE로 두지 않는다.

[해당 가능성]
applicability는 HIGH / CHECK / GENERAL / LOW 중 하나.
- HIGH: 현재 프로필만으로도 해당 가능성이 높음
- CHECK: 소득·재산·가입기간 등 추가조건 확인 필요
- GENERAL: 개인 자격과 무관한 일반정보
- LOW: 현재 프로필과 맞지 않을 가능성이 큼

각 후보를 반드시 모두 평가해 아래 JSON만 반환하라.
기사에 없는 숫자·조건은 만들어내지 마라.

{{
  "items": [
    {{
      "index": 0,
      "score": 0,
      "urgency": "NORMAL",
      "applicability": "GENERAL",
      "summary": "90자 이내 핵심 요약",
      "why": "왜 이 점수인지 짧게",
      "action": "사용자가 확인하거나 할 일. 없으면 빈 문자열"
    }}
  ]
}}

[후보]
{chr(10).join(rows)}
""".strip()


def parse_json_response(raw):
    raw = raw.strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def heuristic_score(article, topic):
    score = 55

    if article.get("is_official"):
        score += 10

    title_summary = (article["title"] + " " + article.get("summary", "")).lower()

    high_words = [
        "신청", "지원금", "할인", "마감", "선착순", "시행", "인상", "인하",
        "부모급여", "아동수당", "육아휴직", "근로시간", "금리", "분양",
        "정비구역", "gtx", "무료", "예약",
    ]

    score += min(20, sum(3 for w in high_words if w in title_summary))
    score += min(10, int(topic.get("priority", 5)))

    return min(score, 88)


def score_articles(topic, articles, profile):
    if not articles:
        return []

    try:
        raw = ai_generate(scoring_prompt(topic, articles, profile))
        data = parse_json_response(raw)
        ai_items = data.get("items", [])
    except Exception as exc:
        print(f"  [AI 평가 실패] {exc}")
        ai_items = []

    by_index = {}

    for item in ai_items:
        try:
            idx = int(item.get("index"))
            score = int(item.get("score", 0))
        except Exception:
            continue

        if not (0 <= idx < len(articles)):
            continue

        score = max(0, min(100, score))

        by_index[idx] = {
            "score": score,
            "urgency": str(item.get("urgency", "NORMAL")).upper(),
            "applicability": str(item.get("applicability", "GENERAL")).upper(),
            "ai_summary": clean_text(item.get("summary", ""))[:160],
            "why": clean_text(item.get("why", ""))[:180],
            "action": clean_text(item.get("action", ""))[:180],
        }

    result = []

    for idx, article in enumerate(articles):
        enriched = article.copy()

        if idx in by_index:
            enriched.update(by_index[idx])
        else:
            enriched.update({
                "score": heuristic_score(article, topic),
                "urgency": "NORMAL",
                "applicability": "GENERAL",
                "ai_summary": article["title"],
                "why": "AI 평가 결과가 없어 기본 규칙으로 산정",
                "action": "",
            })

        result.append(enriched)

    return result


def score_badge(score):
    if score >= 92:
        return "🚨"
    if score >= 82:
        return "🔥"
    if score >= 72:
        return "⭐"
    return "•"


def applicability_text(value):
    return {
        "HIGH": "✅ 해당 가능성 높음",
        "CHECK": "🔎 조건 확인 필요",
        "GENERAL": "ℹ️ 일반정보",
        "LOW": "➖ 해당 가능성 낮음",
    }.get(value, "ℹ️ 일반정보")


def escape_html(text):
    return html.escape(text or "", quote=False)


def make_article_block(article):
    score = int(article.get("score", 0))
    badge = score_badge(score)
    summary = escape_html(article.get("ai_summary") or article["title"])
    source = escape_html(article.get("source") or "출처 미상")
    published = escape_html(article.get("published") or "")
    app = escape_html(applicability_text(article.get("applicability", "GENERAL")))
    action = escape_html(article.get("action", ""))
    link = html.escape(article.get("link") or "", quote=True)

    official = " 🏛️" if article.get("is_official") else ""
    meta = source + official
    if published:
        meta += f" · {published}"

    lines = [
        f"{badge} <b>중요도 {score}</b> · {app}",
        f"• {summary}",
        f"  <i>{meta}</i>",
    ]

    if action:
        lines.append(f"  → {action}")

    if link:
        lines.append(f"  <a href=\"{link}\">기사 보기</a>")

    return "\n".join(lines)


def split_message(text, max_length=3900):
    if len(text) <= max_length:
        return [text]

    chunks = []
    current = ""

    for paragraph in text.split("\n"):
        candidate = current + ("\n" if current else "") + paragraph

        if len(candidate) <= max_length:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)

    return chunks


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    for chunk in split_message(message):
        r = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        print("Telegram:", r.status_code, r.text[:250])
        r.raise_for_status()


def choose_digest(topic, scored):
    min_score = int(topic.get("min_score", DEFAULT_MIN_SCORE))
    max_articles = int(topic.get("max_articles", DEFAULT_MAX_SELECTED))

    usable = [
        a for a in scored
        if int(a.get("score", 0)) >= min_score
        and a.get("applicability") != "LOW"
    ]

    usable.sort(
        key=lambda x: (int(x.get("score", 0)), x.get("published_ts", 0)),
        reverse=True,
    )

    return usable[:max_articles]


def choose_urgent(topic, scored):
    if not topic.get("urgent_enabled", False):
        return []

    usable = [
        a for a in scored
        if int(a.get("score", 0)) >= URGENT_SCORE
        and a.get("urgency") == "IMMEDIATE"
        and a.get("applicability") != "LOW"
    ]

    usable.sort(
        key=lambda x: (int(x.get("score", 0)), x.get("published_ts", 0)),
        reverse=True,
    )

    return usable[:2]


def remove_cross_topic_duplicates(selected_by_topic):
    used_titles = []
    output = {}

    for topic_name, articles in selected_by_topic.items():
        kept = []

        for article in articles:
            if any(
                title_is_similar(article["title"], old)
                for old in used_titles
            ):
                continue

            kept.append(article)
            used_titles.append(article["title"])

        output[topic_name] = kept

    return output


def build_digest_message(selected_by_topic):
    now = datetime.now(KST)

    parts = [
        "📰 <b>맞춤 뉴스</b>",
        f"📅 {now.strftime('%Y-%m-%d %H:%M')} 기준",
        "",
    ]

    empty_topics = []
    total = 0

    for topic_name, articles in selected_by_topic.items():
        if not articles:
            empty_topics.append(topic_name)
            continue

        parts.append(f"<b>{escape_html(topic_name)}</b>")

        for article in articles:
            parts.append(make_article_block(article))
            parts.append("")
            total += 1

    if total == 0:
        parts.append("새로 볼 만한 주요 뉴스가 없습니다.")

    if empty_topics:
        names = ", ".join(
            re.sub(r"^[^가-힣A-Za-z0-9]+", "", x)
            for x in empty_topics
        )
        parts.append(f"<i>새 주요 뉴스 없음: {escape_html(names)}</i>")

    return "\n".join(parts).strip()


def build_urgent_message(topic_name, articles):
    now = datetime.now(KST)

    parts = [
        "🚨 <b>놓치면 아까운 긴급 알림</b>",
        f"📅 {now.strftime('%Y-%m-%d %H:%M')}",
        f"<b>{escape_html(topic_name)}</b>",
        "",
    ]

    for article in articles:
        parts.append(make_article_block(article))
        parts.append("")

    return "\n".join(parts).strip()


def main():
    topics = load_topics()
    profile = load_profile()
    history, sent_fingerprints, sent_titles = load_sent_history()

    print(f"[모드] {RUN_MODE}")
    print(f"[AI] {AI_PROVIDER}")
    print(f"[중복방지] 최근 {HISTORY_DAYS}일 제목 {len(sent_titles)}건")

    selected_by_topic = {}
    newly_sent = []

    for topic in topics:
        if not topic.get("enabled", True):
            continue

        if RUN_MODE == "urgent" and not topic.get("urgent_enabled", False):
            continue

        print(f"\n[수집] {topic['name']}")

        candidates = collect_topic_articles(
            topic,
            sent_fingerprints,
            sent_titles,
        )

        print(f"  후보 {len(candidates)}건")

        scored = score_articles(topic, candidates, profile)

        if RUN_MODE == "urgent":
            selected = choose_urgent(topic, scored)
        else:
            selected = choose_digest(topic, scored)

        print(
            "  선별:",
            [(x.get("score"), x.get("title", "")[:35]) for x in selected],
        )

        selected_by_topic[topic["name"]] = selected

    selected_by_topic = remove_cross_topic_duplicates(selected_by_topic)

    if RUN_MODE == "urgent":
        any_sent = False

        for topic_name, articles in selected_by_topic.items():
            if not articles:
                continue

            send_telegram(build_urgent_message(topic_name, articles))
            newly_sent.extend(articles)
            any_sent = True

        if not any_sent:
            print("[긴급] 92점 이상 즉시 알림 없음")
            return

    else:
        message = build_digest_message(selected_by_topic)
        send_telegram(message)

        for articles in selected_by_topic.values():
            newly_sent.extend(articles)

    if newly_sent:
        save_sent_history(history, newly_sent)

    print(f"[완료] 새로 기록 {len(newly_sent)}건")


if __name__ == "__main__":
    main()
