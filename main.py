import os
import re
import html
import json
import hashlib
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from difflib import SequenceMatcher

import feedparser
import requests
from google import genai

KST = ZoneInfo("Asia/Seoul")

DEFAULT_LOOKBACK_HOURS = 72
ARTICLES_PER_SEARCH_TERM = 4
MAX_CANDIDATES_PER_TOPIC = 14
DEFAULT_MAX_SELECTED = 2
GEMINI_MODEL = "gemini-3.6-flash"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

CONFIG_FILE = "topics.json"
SENT_FILE = "sent_news.json"

BLOCK_TITLE_WORDS = [
    "협찬",
    "광고",
    "브랜드 캠페인",
]


def load_topics():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        topics = json.load(f)

    if not isinstance(topics, list) or not topics:
        raise ValueError("topics.json에 주제가 없습니다.")

    return topics


def load_sent_today():
    today = datetime.now(KST).strftime("%Y-%m-%d")

    if not os.path.exists(SENT_FILE):
        return set()

    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return set()

    return set(data.get(today, []))


def save_sent_today(fingerprints):
    today = datetime.now(KST).strftime("%Y-%m-%d")

    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {today: sorted(fingerprints)},
            f,
            ensure_ascii=False,
            indent=2,
        )


def clean_text(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(title):
    # Google News 제목 뒤의 언론사 표기를 어느 정도 제거
    title = re.sub(r"\s*-\s*[^-]{1,35}$", "", title)
    title = re.sub(r"[^0-9A-Za-z가-힣]", "", title)
    return title.lower()


def article_fingerprint(title):
    normalized = normalize_title(title)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def title_is_similar(a, b):
    na = normalize_title(a)
    nb = normalize_title(b)

    if not na or not nb:
        return False

    if na == nb:
        return True

    return SequenceMatcher(None, na, nb).ratio() >= 0.82


def is_blocked_title(title):
    return any(word in title for word in BLOCK_TITLE_WORDS)


def fetch_one_search_term(search_term, lookback_hours, already_sent):
    """
    검색어 하나씩 Google News RSS에서 가져온다.
    긴 OR 검색 하나로 몰아넣지 않는 것이 핵심.
    """
    encoded = urllib.parse.quote(search_term)
    rss_url = (
        f"https://news.google.com/rss/search?q={encoded}"
        "&hl=ko&gl=KR&ceid=KR:ko"
    )

    feed = feedparser.parse(rss_url)
    cutoff = datetime.now(KST) - timedelta(hours=lookback_hours)

    results = []

    for entry in feed.entries:
        title = clean_text(getattr(entry, "title", ""))

        if not title or is_blocked_title(title):
            continue

        fingerprint = article_fingerprint(title)

        if fingerprint in already_sent:
            continue

        published_dt = None
        published_ts = 0

        if getattr(entry, "published_parsed", None):
            published_dt = datetime(
                *entry.published_parsed[:6],
                tzinfo=ZoneInfo("UTC"),
            ).astimezone(KST)

            if published_dt < cutoff:
                continue

            published_ts = int(published_dt.timestamp())

        source = ""
        if getattr(entry, "source", None):
            source = clean_text(getattr(entry.source, "title", ""))

        summary = clean_text(getattr(entry, "summary", ""))
        link = getattr(entry, "link", "")

        results.append({
            "title": title,
            "source": source,
            "summary": summary[:500],
            "link": link,
            "published": published_dt.strftime("%m/%d %H:%M") if published_dt else "",
            "published_ts": published_ts,
            "fingerprint": fingerprint,
            "matched_term": search_term,
        })

        if len(results) >= ARTICLES_PER_SEARCH_TERM:
            break

    return results


def collect_topic_articles(topic, already_sent):
    """
    topic의 search_terms를 각각 검색한 뒤 합친다.
    비슷한 제목은 하나만 남기고 최신순으로 정렬한다.
    """
    lookback_hours = int(
        topic.get("lookback_hours", DEFAULT_LOOKBACK_HOURS)
    )

    merged = []

    for term in topic.get("search_terms", []):
        term = str(term).strip()
        if not term:
            continue

        try:
            found = fetch_one_search_term(
                term,
                lookback_hours,
                already_sent,
            )
            print(f"    {term}: {len(found)}건")
            merged.extend(found)
        except Exception as exc:
            print(f"    [검색 실패] {term}: {exc}")

    merged.sort(
        key=lambda x: x.get("published_ts", 0),
        reverse=True,
    )

    deduped = []

    for article in merged:
        if any(
            title_is_similar(article["title"], saved["title"])
            for saved in deduped
        ):
            continue

        deduped.append(article)

        if len(deduped) >= MAX_CANDIDATES_PER_TOPIC:
            break

    return deduped


def build_ai_input(topic, articles):
    max_selected = int(
        topic.get("max_articles", DEFAULT_MAX_SELECTED)
    )

    rules = topic.get(
        "rules",
        "사용자에게 실제 도움이 되는 기사 위주로 고른다.",
    )

    rows = []

    for idx, article in enumerate(articles):
        rows.append(
            f"[{idx}]\n"
            f"검색어: {article['matched_term']}\n"
            f"제목: {article['title']}\n"
            f"출처: {article['source']}\n"
            f"발행: {article['published']}\n"
            f"RSS 설명: {article['summary']}\n"
        )

    return f"""
주제: {topic['name']}

사용자 선별 기준:
{rules}

후보 기사 중 실제로 도움이 되는 기사를 최대 {max_selected}개 골라라.

중요:
1. 같은 사건의 중복 기사는 하나만 고른다.
2. 광고·보도자료·단순 홍보성 기사는 가급적 제외한다.
3. 혜택·지원금·할인이라면 대상, 금액, 기간, 신청조건이 구체적인 내용을 우선한다.
4. 육아라면 2024년생 영유아 가정에 실제 도움이 되는 정책·건강·보육 정보를 우선한다.
5. 직장인이라면 4대보험, 세금, 육아휴직, 근로시간, 지원제도의 실제 변경사항을 우선한다.
6. 기사에 없는 내용을 만들지 않는다.
7. 요약은 1문장, 90자 이내로 쓴다.
8. 적절한 기사가 있으면 최소 1개는 선택한다.
9. 정말 관련 없는 기사뿐일 때만 selections를 빈 배열로 반환한다.
10. index는 후보 번호 중 하나여야 한다.

JSON만 반환:
{{
  "selections": [
    {{
      "index": 0,
      "summary": "핵심 내용"
    }}
  ]
}}

기사 후보:
{chr(10).join(rows)}
""".strip()


def select_with_gemini(topic, articles):
    if not articles:
        return []

    client = genai.Client(api_key=GEMINI_API_KEY)

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=build_ai_input(topic, articles),
    )

    raw = (response.text or "").strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)

    data = json.loads(raw)
    selections = data.get("selections", [])

    max_selected = int(
        topic.get("max_articles", DEFAULT_MAX_SELECTED)
    )

    selected = []
    used = set()

    for item in selections[:max_selected]:
        try:
            idx = int(item["index"])
            summary = clean_text(str(item["summary"]))
        except (KeyError, TypeError, ValueError):
            continue

        if idx < 0 or idx >= len(articles):
            continue

        if idx in used:
            continue

        article = articles[idx].copy()
        article["ai_summary"] = summary[:140]
        selected.append(article)
        used.add(idx)

    # 후보는 있는데 AI가 전부 탈락시킨 경우,
    # 검색 정확도가 높은 첫 후보 1개라도 보여준다.
    if not selected and articles:
        article = articles[0].copy()
        article["ai_summary"] = article["title"]
        selected.append(article)

    return selected


def fallback_select(topic, articles):
    if not articles:
        return []

    max_selected = int(
        topic.get("max_articles", DEFAULT_MAX_SELECTED)
    )

    result = []

    for article in articles[:max_selected]:
        copied = article.copy()
        copied["ai_summary"] = copied["title"]
        result.append(copied)

    return result


def escape_html(text):
    return html.escape(text or "", quote=False)


def make_article_block(article):
    summary = escape_html(
        article.get("ai_summary") or article["title"]
    )
    source = escape_html(
        article.get("source") or "출처 미상"
    )
    published = escape_html(
        article.get("published") or ""
    )
    link = html.escape(
        article["link"],
        quote=True,
    )

    meta = source

    if published:
        meta += f" · {published}"

    return (
        f"• {summary}\n"
        f"  <i>{meta}</i> · "
        f"<a href=\"{link}\">기사 보기</a>"
    )


def build_message(selected_by_topic):
    now = datetime.now(KST)

    parts = [
        "📰 <b>오늘의 맞춤 뉴스</b>",
        f"📅 {now.strftime('%Y-%m-%d %H:%M')} 기준",
        "",
    ]

    total = 0

    for topic_name, articles in selected_by_topic.items():
        parts.append(
            f"<b>{escape_html(topic_name)}</b>"
        )

        if not articles:
            parts.append("• 새로운 주요 뉴스 없음")
            parts.append("")
            continue

        for article in articles:
            parts.append(make_article_block(article))
            total += 1

        parts.append("")

    if total == 0:
        parts.insert(
            2,
            "현재 조건에 맞는 새로운 주요 기사가 없습니다.\n",
        )

    return "\n".join(parts).strip()


def split_message(text, max_length=3900):
    if len(text) <= max_length:
        return [text]

    chunks = []
    current = ""

    for paragraph in text.split("\n"):
        candidate = (
            current
            + ("\n" if current else "")
            + paragraph
        )

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
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    for chunk in split_message(message):
        response = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )

        print(
            "Telegram response:",
            response.status_code,
            response.text[:300],
        )

        response.raise_for_status()


def main():
    topics = load_topics()
    already_sent = load_sent_today()

    print(
        f"[중복방지] 오늘 이미 보낸 기사 "
        f"{len(already_sent)}건"
    )

    selected_by_topic = {}
    newly_sent = set()

    for topic in topics:
        if not topic.get("enabled", True):
            print(f"\n[건너뜀] {topic['name']} - 비활성화")
            continue

        print(f"\n[수집] {topic['name']}")

        candidates = collect_topic_articles(
            topic,
            already_sent,
        )

        print(
            f"  합치기·중복제거 후 후보 "
            f"{len(candidates)}건"
        )

        try:
            selected = select_with_gemini(
                topic,
                candidates,
            )
        except Exception as exc:
            print(
                f"  [AI 선별 실패] {exc}"
            )
            selected = fallback_select(
                topic,
                candidates,
            )

        print(
            f"  최종 선별 {len(selected)}건"
        )

        selected_by_topic[
            topic["name"]
        ] = selected

        for article in selected:
            newly_sent.add(
                article["fingerprint"]
            )

    message = build_message(
        selected_by_topic
    )

    send_telegram(message)

    # Telegram 전송에 성공한 뒤에만 저장
    save_sent_today(
        already_sent | newly_sent
    )

    print(
        f"[중복방지] 새로 기록 "
        f"{len(newly_sent)}건"
    )
    print("[완료] Telegram 발송 완료")


if __name__ == "__main__":
    main()
