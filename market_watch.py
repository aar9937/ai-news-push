import os
import re
import json
import html
import math
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import feedparser
import pandas as pd
import requests
import yfinance as yf
from google import genai

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "market_watch_config.json")
STATE_FILE = os.path.join(BASE_DIR, "market_alerts.json")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

LEVELS = {
    1: ("📉", "단순 하락"),
    2: ("👀", "매수관심"),
    3: ("🚨", "시장충격"),
}

FALL_WORDS = [
    "하락", "급락", "폭락", "약세", "조정", "매도세", "투매",
    "낙폭", "떨어", "내려", "엔저", "달러 약세", "원화 강세",
]


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clean_text(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalized_daily_close(ticker):
    data = yf.Ticker(ticker).history(
        period="2mo",
        interval="1d",
        auto_adjust=True,
        repair=True,
        timeout=15,
    )
    if data is None or data.empty or "Close" not in data:
        raise RuntimeError(f"가격 데이터 없음: {ticker}")

    s = pd.to_numeric(data["Close"], errors="coerce").dropna()
    if s.empty:
        raise RuntimeError(f"종가 데이터 없음: {ticker}")

    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    idx = idx.normalize()
    s.index = idx
    s = s[~s.index.duplicated(keep="last")]
    return s.sort_index()


def load_asset_series(asset):
    synthetic = asset.get("synthetic")
    if synthetic == "JPYKRW100":
        # Yahoo의 KRW=X는 1 USD당 원화, JPY=X는 1 USD당 엔화.
        # 100엔의 원화 가격 = USD/KRW ÷ USD/JPY × 100
        krw = normalized_daily_close("KRW=X").rename("krw")
        jpy = normalized_daily_close("JPY=X").rename("jpy")
        joined = pd.concat([krw, jpy], axis=1).dropna()
        if len(joined) < 2:
            raise RuntimeError("엔/원 합성 환율 데이터 부족")
        return (joined["krw"] / joined["jpy"] * 100.0).rename("Close")

    ticker = asset.get("ticker")
    if not ticker:
        raise RuntimeError(f"티커 없음: {asset.get('name')}")
    return normalized_daily_close(ticker)


def calc_metrics(series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 2:
        raise RuntimeError("가격 이력이 2개 미만")

    current = float(s.iloc[-1])
    previous = float(s.iloc[-2])
    day_pct = (current / previous - 1.0) * 100.0

    five_base = float(s.iloc[-6]) if len(s) >= 6 else float(s.iloc[0])
    five_pct = (current / five_base - 1.0) * 100.0

    high_window = s.iloc[-21:] if len(s) >= 21 else s
    high20 = float(high_window.max())
    drawdown20 = (current / high20 - 1.0) * 100.0 if high20 else 0.0

    returns = s.pct_change().dropna() * 100.0
    zscore = None
    if len(returns) >= 8:
        baseline = returns.iloc[-21:-1] if len(returns) >= 9 else returns.iloc[:-1]
        if len(baseline) >= 7:
            std = float(baseline.std(ddof=1))
            mean = float(baseline.mean())
            if std > 0 and math.isfinite(std):
                zscore = (day_pct - mean) / std

    return {
        "price": current,
        "day_pct": day_pct,
        "five_pct": five_pct,
        "drawdown20": drawdown20,
        "zscore": zscore,
        "asof": s.index[-1].strftime("%Y-%m-%d"),
    }


def classify(asset, m):
    t = asset["thresholds"]
    day = m["day_pct"]
    five = m["five_pct"]
    dd = m["drawdown20"]
    z = m.get("zscore")
    z_abs_ok = abs(day) >= float(t.get("min_z_drop", 0))

    if (
        day <= -float(t["shock_day"])
        or five <= -float(t["shock_5d"])
        or (z is not None and z <= -3.0 and z_abs_ok)
    ):
        return 3

    if (
        day <= -float(t["interest_day"])
        or five <= -float(t["interest_5d"])
        or dd <= -float(t["interest_drawdown"])
        or (z is not None and z <= -2.5 and z_abs_ok)
    ):
        return 2

    if (
        day <= -float(t["simple_day"])
        or (z is not None and z <= -2.0 and z_abs_ok)
    ):
        return 1

    return 0


def parse_entry_time(entry, tz):
    if getattr(entry, "published_parsed", None):
        dt = datetime(*entry.published_parsed[:6], tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(tz)
    return None


def fetch_news(asset, lookback_hours, max_candidates, tz):
    cutoff = datetime.now(tz) - timedelta(hours=lookback_hours)
    found = []
    seen = set()

    for term in asset.get("news_terms", []):
        q = urllib.parse.quote(term)
        url = (
            f"https://news.google.com/rss/search?q={q}"
            "&hl=ko&gl=KR&ceid=KR:ko"
        )
        feed = feedparser.parse(url)

        for entry in feed.entries:
            title = clean_text(getattr(entry, "title", ""))
            if not title:
                continue

            key = re.sub(r"[^0-9A-Za-z가-힣]", "", title).lower()
            if key in seen:
                continue

            published_dt = parse_entry_time(entry, tz)
            if published_dt and published_dt < cutoff:
                continue

            source = ""
            if getattr(entry, "source", None):
                source = clean_text(getattr(entry.source, "title", ""))

            found.append({
                "title": title,
                "summary": clean_text(getattr(entry, "summary", ""))[:600],
                "source": source or "출처 미상",
                "link": getattr(entry, "link", ""),
                "published": published_dt.strftime("%m/%d %H:%M") if published_dt else "",
                "published_ts": int(published_dt.timestamp()) if published_dt else 0,
                "matched_term": term,
            })
            seen.add(key)

    found.sort(key=lambda x: x["published_ts"], reverse=True)
    return found[:max_candidates]


def fallback_news_judge(news):
    for idx, item in enumerate(news):
        text = f"{item['title']} {item['summary']}"
        if any(word in text for word in FALL_WORDS):
            return {
                "confirmed": True,
                "reason": clean_text(item["title"])[:140],
                "indexes": [idx],
            }
    return {"confirmed": False, "reason": "", "indexes": []}


def ai_news_judge(asset, metrics, level, news, model):
    if not news:
        return {"confirmed": False, "reason": "", "indexes": []}

    if not GEMINI_API_KEY:
        return fallback_news_judge(news)

    rows = []
    for i, item in enumerate(news):
        rows.append(
            f"[{i}] 제목: {item['title']}\n"
            f"출처: {item['source']} / 발행: {item['published']}\n"
            f"설명: {item['summary']}"
        )

    _, label = LEVELS[level]
    ztxt = "계산 불가" if metrics.get("zscore") is None else f"{metrics['zscore']:.2f}σ"

    prompt = f"""
너는 금융 뉴스 확인 담당자다. 투자 추천을 하지 말고 사실 일치 여부만 판단한다.

감지 자산: {asset['name']}
시스템 등급: {label}
현재 가격: {metrics['price']}
1거래일 변화: {metrics['day_pct']:.2f}%
5거래일 변화: {metrics['five_pct']:.2f}%
최근 20거래일 고점 대비: {metrics['drawdown20']:.2f}%
최근 변동성 대비 오늘 움직임: {ztxt}

아래 뉴스 중 최소 1개가 이 자산의 실제 최근 하락/약세 또는 그 직접 원인을 설명할 때만 confirmed=true로 한다.
단순 전망, 상승 기사, 오래된 사건, 이름만 우연히 겹치는 기사는 인정하지 않는다.
기사에 없는 원인을 추측하지 않는다.
reason은 확인된 뉴스에 근거해 한국어 1문장 100자 이내로 쓴다.
indexes에는 가장 직접적인 기사 최대 2개의 번호만 넣는다.

JSON만 반환:
{{"confirmed": true, "reason": "...", "indexes": [0]}}

뉴스 후보:
{chr(10).join(rows)}
""".strip()

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model=model, contents=prompt)
        raw = (response.text or "").strip()
        raw = re.sub(r"^```json\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)

        confirmed = bool(data.get("confirmed"))
        reason = clean_text(data.get("reason", ""))[:140]
        indexes = []
        for x in data.get("indexes", [])[:2]:
            try:
                i = int(x)
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(news) and i not in indexes:
                indexes.append(i)

        if confirmed and not indexes:
            fallback = fallback_news_judge(news)
            indexes = fallback["indexes"]
        return {"confirmed": confirmed and bool(indexes), "reason": reason, "indexes": indexes}
    except Exception as exc:
        print(f"  [AI 뉴스판정 실패] {asset['name']}: {exc}")
        return fallback_news_judge(news)


def parse_iso(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def should_send(asset, metrics, level, state, cooldown_hours, now):
    old = state.get(asset["id"], {})
    old_level = int(old.get("level", 0) or 0)
    old_price = old.get("price")
    old_time = parse_iso(old.get("sent_at"))

    if not old_time:
        return True

    if level > old_level:
        return True

    age = now - old_time.astimezone(now.tzinfo)
    if age < timedelta(hours=cooldown_hours):
        return False

    try:
        old_price = float(old_price)
        extra = (float(metrics["price"]) / old_price - 1.0) * 100.0
        return extra <= -float(asset.get("resend_extra_drop", 1.0))
    except (TypeError, ValueError, ZeroDivisionError):
        return True


def fmt_num(value, decimals):
    return f"{value:,.{decimals}f}"


def make_alert_block(alert):
    asset = alert["asset"]
    m = alert["metrics"]
    level = alert["level"]
    judge = alert["judge"]
    news = alert["news"]
    icon, label = LEVELS[level]
    ztxt = "-" if m.get("zscore") is None else f"{m['zscore']:.1f}σ"

    lines = [
        f"{icon} <b>{label} | {html.escape(asset['name'])}</b>",
        f"현재 {fmt_num(m['price'], int(asset.get('decimals', 2)))} {html.escape(asset.get('unit', ''))}",
        f"1일 <b>{m['day_pct']:+.2f}%</b> · 5일 {m['five_pct']:+.2f}% · 20일 고점 대비 {m['drawdown20']:+.2f}%",
        f"평소 대비: {ztxt} · 가격 기준일 {m['asof']}",
    ]

    if judge.get("reason"):
        lines.append(f"뉴스 확인: {html.escape(judge['reason'])}")

    for idx in judge.get("indexes", [])[:2]:
        item = news[idx]
        link = html.escape(item.get("link", ""), quote=True)
        title = html.escape(item.get("title", ""))
        source = html.escape(item.get("source", ""))
        if link:
            lines.append(f"• <a href=\"{link}\">{title}</a> <i>({source})</i>")
        else:
            lines.append(f"• {title} <i>({source})</i>")

    return "\n".join(lines)


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 없습니다.")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    response.raise_for_status()


def main():
    config = load_json(CONFIG_FILE, {})
    settings = config.get("settings", {})
    assets = config.get("assets", [])
    state = load_json(STATE_FILE, {})

    tz = ZoneInfo(settings.get("timezone", "Asia/Seoul"))
    now = datetime.now(tz)
    model = settings.get("gemini_model", "gemini-3.6-flash")
    lookback = int(settings.get("news_lookback_hours", 24))
    max_news = int(settings.get("max_news_candidates", 10))
    cooldown = int(settings.get("alert_cooldown_hours", 72))

    alerts = []

    for asset in assets:
        print(f"\n[가격 확인] {asset['name']}")
        try:
            series = load_asset_series(asset)
            metrics = calc_metrics(series)
            level = classify(asset, metrics)
        except Exception as exc:
            print(f"  [가격 실패] {exc}")
            continue

        print(
            f"  가격={metrics['price']:.4f} / 1일={metrics['day_pct']:+.2f}% / "
            f"5일={metrics['five_pct']:+.2f}% / DD20={metrics['drawdown20']:+.2f}% / "
            f"z={metrics.get('zscore')} / level={level}"
        )

        if level == 0:
            continue

        try:
            news = fetch_news(asset, lookback, max_news, tz)
        except Exception as exc:
            print(f"  [뉴스 수집 실패] {exc}")
            continue

        if not news:
            print("  최근 관련 뉴스 없음 → 알림 안 함")
            continue

        judge = ai_news_judge(asset, metrics, level, news, model)
        if not judge.get("confirmed"):
            print("  가격 이상은 있으나 뉴스 확인 실패 → 알림 안 함")
            continue

        if not should_send(asset, metrics, level, state, cooldown, now):
            print("  최근 같은 이상징후를 이미 알림 → 중복 억제")
            continue

        alerts.append({
            "asset": asset,
            "metrics": metrics,
            "level": level,
            "judge": judge,
            "news": news,
        })

    if not alerts:
        print("\n[완료] 뉴스가 확인된 새로운 투자 이상징후 없음")
        return

    alerts.sort(key=lambda x: (-x["level"], x["metrics"]["day_pct"]))
    blocks = [make_alert_block(x) for x in alerts]

    message = (
        "📡 <b>투자 이상징후 감지</b>\n"
        f"📅 {now.strftime('%Y-%m-%d %H:%M')} 기준\n\n"
        + "\n\n".join(blocks)
        + "\n\n※ 가격 이상징후와 최근 관련 뉴스가 함께 확인된 경우만 발송합니다."
        + "\n※ ‘매수관심’은 자동 분류명이며 매수 권유가 아닙니다."
    )

    send_telegram(message)

    for alert in alerts:
        asset = alert["asset"]
        m = alert["metrics"]
        state[asset["id"]] = {
            "sent_at": now.isoformat(),
            "level": alert["level"],
            "price": m["price"],
            "day_pct": m["day_pct"],
            "five_pct": m["five_pct"],
            "asof": m["asof"],
        }

    save_json(STATE_FILE, state)
    print(f"\n[완료] 투자 이상징후 {len(alerts)}건 Telegram 발송")


if __name__ == "__main__":
    main()
