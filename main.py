import os
import re
import html
import json
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import feedparser
import requests
from google import genai

KST = ZoneInfo("Asia/Seoul")
DEFAULT_LOOKBACK_HOURS = 36
ARTICLES_PER_TOPIC = 10
DEFAULT_MAX_SELECTED = 2
GEMINI_MODEL = "gemini-3.6-flash"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

CONFIG_FILE = "topics.json"

BLOCK_TITLE_WORDS = [
    "협찬",
    "광고",
    "이벤트 진행",
    "무료 시식",
    "브랜드 캠페인",
]


def load_topics() -> list[dict]:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        topics = json.load(f)

    if not isinstance(topics, list) or not topics:
        raise ValueError("topics.json에 주제가 없습니다.")

    for topic in topics:
        if "name" not in topic:
            raise ValueError("topics.json의 각 주제에는 name이 필요합니다.")
        if not topic.get("search_terms"):
            raise ValueError(f"{topic['name']}의 search_terms가 비어 있습니다.")

    return topics


def build_query(search_terms: list[str]) -> str:
    """
    각 검색어를 너무 엄격한 '완전 일치 문구'로 묶지 않고 OR로 연결한다.
    """
    cleaned = [str(term).strip() for term in search_terms if str(term).strip()]
    return " OR ".join(f"({term})" for term in cleaned)


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(title: str) -> str:
    title = re.sub(r"\s*-\s*[^-]{1,30}$", "", title)
    title = re.sub(r"[^0-9A-Za-z가-힣]", "", title)
    return title.lower()


def is_blocked_title(title: str) -> bool:
    return any(word in title for word in BLOCK_TITLE_WORDS)


def get_google_news(query: str, lookback_hours: int) -> list[dict]:
    encoded_query = urllib.parse.quote(query)
    rss_url = (
        f"https://news.google.com/rss/search?q={encoded_query}"
        "&hl=ko&gl=KR&ceid=KR:ko"
    )

    feed = feedparser.parse(rss_url)
    cutoff = datetime.now(KST) - timedelta(hours=lookback_hours)

    articles = []
    seen = set()

    for entry in feed.entries:
        title = clean_text(getattr(entry, "title", ""))

        if not title or is_blocked_title(title):
            continue

        normalized = normalize_title(title)
        if not normalized or normalized in seen:
            continue

        published_dt = None
        if getattr(entry, "published_parsed", None):
            published_dt = datetime(
                *entry.published_parsed[:6],
                tzinfo=ZoneInfo("UTC")
            ).astimezone(KST)

            if published_dt < cutoff:
                continue

        source = ""
        if getattr(entry, "source", None):
            source = clean_text(getattr(entry.source, "title", ""))

        summary = clean_text(getattr(entry, "summary", ""))
        link = getattr(entry, "link", "")

        articles.append({
            "title": title,
            "source": source,
            "summary": summary[:500],
            "link": link,
            "published": published_dt.strftime("%m/%d %H:%M") if published_dt else "",
        })

        seen.add(normalized)

        if len(articles) >= ARTICLES_PER_TOPIC:
            break

    return articles


def build_ai_input(topic: dict, articles: list[dict]) -> str:
    rows = []

    for idx, article in enumerate(articles):
        rows.append(
            f"[{idx}]\n"
            f"제목: {article['title']}\n"
            f"출처: {article['source']}\n"
            f"발행: {article['published']}\n"
            f"RSS 설명: {article['summary']}\n"
        )

    max_selected = int(topic.get("max_articles", DEFAULT_MAX_SELECTED))
    rules = topic.get("rules", "사용자에게 실제로 도움이 되는 최신 기사 위주로 선별한다.")

    return f"""
주제: {topic['name']}

선별 기준:
{rules}

아래 기사 후보 중 사용자에게 실제로 유용한 기사만 최대 {max_selected}개 골라라.

규칙:
1. 같은 사건을 다룬 중복 기사는 1개만 선택한다.
2. 홍보성·광고성·내용이 빈약한 기사는 제외한다.
3. 사용자의 검색 의도와 직접 관련된 기사만 선택한다.
4. 기사에 없는 사실은 절대 만들지 않는다.
5. 요약은 1문장, 80자 이내로 쓴다.
6. 날짜·금액·금리·장소·시간처럼 구체적인 정보가 있으면 요약에 넣는다.
7. 적절한 기사가 없으면 selections를 빈 배열로 반환한다.
8. index는 반드시 아래 후보의 번호 중 하나여야 한다.

반드시 JSON만 반환:
{{
  "selections": [
    {{
      "index": 0,
      "summary": "핵심 내용 1문장"
    }}
  ]
}}

기사 후보:
{chr(10).join(rows)}
""".strip()


def select_with_gemini(topic: dict, articles: list[dict]) -> list[dict]:
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

    max_selected = int(topic.get("max_articles", DEFAULT_MAX_SELECTED))
    result = []
    used = set()

    for item in selections[:max_selected]:
        try:
            idx = int(item["index"])
            summary = clean_text(str(item["summary"]))
        except (KeyError, TypeError, ValueError):
            continue

        if idx < 0 or idx >= len(articles) or idx in used:
            continue

        article = articles[idx].copy()
        article["ai_summary"] = summary[:120]
        result.append(article)
        used.add(idx)

    return result


def escape_html(text: str) -> str:
    return html.escape(text or "", quote=False)


def make_article_block(article: dict) -> str:
    summary = escape_html(article.get("ai_summary") or article["title"])
    source = escape_html(article.get("source") or "출처 미상")
    published = escape_html(article.get("published") or "")
    link = html.escape(article["link"], quote=True)

    meta = source
    if published:
        meta += f" · {published}"

    return (
        f"• {summary}\n"
        f"  <i>{meta}</i> · <a href=\"{link}\">기사 보기</a>"
    )


def build_message(selected_by_topic: dict[str, list[dict]]) -> str:
    now = datetime.now(KST)

    parts = [
        "📰 <b>오늘의 맞춤 뉴스</b>",
        f"📅 {now.strftime('%Y-%m-%d %H:%M')} 기준",
        "",
    ]

    for topic_name, articles in selected_by_topic.items():
        parts.append(f"<b>{escape_html(topic_name)}</b>")

        if not articles:
            parts.append("• 최근 조건에 맞는 주요 뉴스 없음")
            parts.append("")
            continue

        for article in articles:
            parts.append(make_article_block(article))

        parts.append("")

    return "\n".join(parts).strip()


def split_message(text: str, max_length: int = 3900) -> list[str]:
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


def send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

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

        print("Telegram response:", response.status_code, response.text[:500])
        response.raise_for_status()


def fallback_select(topic: dict, articles: list[dict]) -> list[dict]:
    max_selected = int(topic.get("max_articles", DEFAULT_MAX_SELECTED))
    result = []

    for article in articles[:max_selected]:
        copied = article.copy()
        copied["ai_summary"] = copied["title"]
        result.append(copied)

    return result


def main():
    topics = load_topics()
    selected_by_topic = {}

    for topic in topics:
        query = build_query(topic["search_terms"])
        lookback_hours = int(topic.get("lookback_hours", DEFAULT_LOOKBACK_HOURS))

        print(f"[수집] {topic['name']}")
        print(f"  검색어: {query}")
        print(f"  조회기간: 최근 {lookback_hours}시간")

        articles = get_google_news(query, lookback_hours)
        print(f"  후보 {len(articles)}건")

        try:
            selected = select_with_gemini(topic, articles)
            print(f"  AI 선별 {len(selected)}건")
        except Exception as exc:
            print(f"[경고] {topic['name']} AI 선별 실패: {exc}")
            selected = fallback_select(topic, articles)

        selected_by_topic[topic["name"]] = selected

    message = build_message(selected_by_topic)
    send_telegram(message)

    print("[완료] Telegram 발송 완료")


if __name__ == "__main__":
    main()
