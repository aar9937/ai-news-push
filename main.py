import os
import re
import html
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import feedparser
import requests
from google import genai

# =========================
# 사용자 설정
# =========================

KEYWORDS = [
    "송도",
    "드론쇼",
    "신제품 출시",
    "부동산 집",
    "적금",
]

ARTICLES_PER_KEYWORD = 3
LOOKBACK_HOURS = 36
GEMINI_MODEL = "gemini-3.6-flash"

# GitHub Secrets에서 읽음
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

KST = ZoneInfo("Asia/Seoul")


def clean_html(text: str) -> str:
    """RSS 설명에 섞인 HTML 태그를 제거한다."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def get_google_news(keyword: str) -> list[dict]:
    """Google News RSS에서 최근 뉴스를 가져온다."""
    query = urllib.parse.quote(keyword)
    rss_url = (
        f"https://news.google.com/rss/search?q={query}"
        "&hl=ko&gl=KR&ceid=KR:ko"
    )

    feed = feedparser.parse(rss_url)
    cutoff = datetime.now(KST) - timedelta(hours=LOOKBACK_HOURS)

    articles = []
    seen_titles = set()

    for entry in feed.entries:
        title = clean_html(getattr(entry, "title", "")).strip()
        if not title or title in seen_titles:
            continue

        published = None
        if getattr(entry, "published_parsed", None):
            published = datetime(*entry.published_parsed[:6], tzinfo=ZoneInfo("UTC")).astimezone(KST)
            if published < cutoff:
                continue

        source = ""
        if getattr(entry, "source", None):
            source = getattr(entry.source, "title", "") or ""

        summary = clean_html(getattr(entry, "summary", ""))
        link = getattr(entry, "link", "")

        articles.append(
            {
                "keyword": keyword,
                "title": title,
                "source": source,
                "summary": summary,
                "link": link,
                "published": published.strftime("%m/%d %H:%M") if published else "",
            }
        )
        seen_titles.add(title)

        if len(articles) >= ARTICLES_PER_KEYWORD:
            break

    return articles


def build_news_text(news_by_keyword: dict[str, list[dict]]) -> str:
    blocks = []

    for keyword, articles in news_by_keyword.items():
        blocks.append(f"[키워드: {keyword}]")

        if not articles:
            blocks.append("- 최근 관련 뉴스 없음")
            continue

        for i, article in enumerate(articles, 1):
            source = f" / {article['source']}" if article["source"] else ""
            published = f" / {article['published']}" if article["published"] else ""
            blocks.append(
                f"{i}. {article['title']}{source}{published}\n"
                f"   참고: {article['summary'][:350]}\n"
                f"   링크: {article['link']}"
            )

    return "\n\n".join(blocks)


def summarize_with_gemini(news_text: str) -> str:
    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
아래는 Google News RSS에서 수집한 한국어 뉴스 목록이다.

중요 규칙:
- 제공된 제목, RSS 설명, 출처 안에서만 요약한다.
- 기사에 없는 사실을 추측하거나 만들어내지 않는다.
- 같은 내용의 기사는 하나의 이슈로 묶어도 된다.
- 모바일 텔레그램에서 읽기 쉽게 짧게 쓴다.
- 각 키워드마다 최대 3개 이슈만 표시한다.
- 중요한 변화, 날짜, 금액, 출시 여부 같은 구체 정보가 있으면 우선 표시한다.
- 관련 뉴스가 약하거나 의미가 없으면 "특이사항 없음"이라고 적는다.
- 링크는 입력된 링크를 그대로 붙인다.

출력 형식:
🟦 키워드명
• 핵심 내용 한 줄
  출처: 언론사
  링크: URL

뉴스 데이터:
{news_text}
""".strip()

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    return (response.text or "").strip()


def fallback_report(news_by_keyword: dict[str, list[dict]]) -> str:
    """Gemini 호출 실패 시 제목과 링크라도 전송한다."""
    lines = ["⚠️ AI 요약에 실패해 원문 제목으로 보냅니다."]

    for keyword, articles in news_by_keyword.items():
        lines.append(f"\n🟦 {keyword}")
        if not articles:
            lines.append("• 최근 관련 뉴스 없음")
            continue

        for article in articles:
            source = f" ({article['source']})" if article["source"] else ""
            lines.append(f"• {article['title']}{source}")
            lines.append(f"  {article['link']}")

    return "\n".join(lines)


def split_message(text: str, max_length: int = 3900) -> list[str]:
    """Telegram 4096자 제한을 넘지 않도록 메시지를 나눈다."""
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
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        response.raise_for_status()


def main():
    now = datetime.now(KST)
    news_by_keyword = {}

    for keyword in KEYWORDS:
        print(f"[수집] {keyword}")
        news_by_keyword[keyword] = get_google_news(keyword)

    news_text = build_news_text(news_by_keyword)

    try:
        report = summarize_with_gemini(news_text)
        if not report:
            raise RuntimeError("Gemini 응답이 비어 있습니다.")
    except Exception as exc:
        print(f"[경고] Gemini 요약 실패: {exc}")
        report = fallback_report(news_by_keyword)

    header = (
        "📰 오늘의 맞춤 뉴스\n"
        f"📅 {now.strftime('%Y-%m-%d %H:%M')} 기준\n\n"
    )

    send_telegram(header + report)
    print("[완료] Telegram 발송 완료")


if __name__ == "__main__":
    main()
