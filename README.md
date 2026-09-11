# AI News Push

Google News RSS → Gemini 요약 → Telegram 알림 자동화입니다.

## 실행 시간
매일 오전 8:05 (Asia/Seoul)

## 필요한 GitHub Secrets
Repository → Settings → Secrets and variables → Actions → New repository secret

- `GEMINI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 수동 테스트
GitHub 저장소 → Actions → Daily AI News Push → Run workflow

## 뉴스 키워드
`main.py`의 `KEYWORDS` 목록을 수정하면 됩니다.
