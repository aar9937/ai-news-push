
가능해. 가장 빠른 방법은 제미나이 대화 전체를 공개 링크로 만든 뒤 여기 붙여주는 것이야. Gemini는 현재 대화 전체를 공유하는 공개 링크를 만들 수 있어. 

방법 1 — 제일 간단
제미나이 PC에서 해당 대화를 연 다음
답변 아래 공유 및 내보내기 → 대화 공유 → 공개 링크 복사
→ 그 링크를 이 채팅에 그대로 보내줘.

그러면 내가 내용을 읽고:

지금까지 결정한 내용

중요한 데이터·수치

만들어 둔 코드나 기능

아직 해결 안 된 문제

앞으로 할 작업

까지 정리한 뒤 제미나이에서 하던 작업 그대로 이어서 진행하면 돼.

단, Gemini 공유 링크는 링크를 아는 사람이 내용을 볼 수 있는 공개 링크라서 보험 개인정보, 주민번호, 주소, 계좌정보 같은 내용이 있으면 이 방법은 추천하지 않아. 

방법 2 — 개인정보가 있거나 대화가 아주 길다면
Gemini에서 **공유 및 내보내기 → Docs로 내보내기**를 사용하면 Google Drive에 문서로 저장할 수 있어. 

그 문서를 여기 파일로 올려도 되고, Google Drive를 ChatGPT에 연결하면 내가 Drive의 문서를 직접 찾아 읽는 방식으로도 작업할 수 있어. 위에 Google Drive 연결 옵션도 띄워뒀어.

내가 추천하는 건 일단 Gemini 대화 공유 링크를 여기 붙여주는 것이야. 링크 보내주면 내가 먼저 전체 내용을 파악해서 “여기까지 했고, 다음은 이것부터 하면 됨” 상태로 만들어서 바로 이어갈게.



9bd0b898-82e8-4049-9181-cbbf2e5c8f10.mp4
파일
이게 다야 

응. 네 화면 보니까 공유 버튼 있어.

이미지 바로 아래 아이콘이 왼쪽부터
👍 → 👎 → 다시 생성(↻) → 공유(□↑) → ··· 순서야.

여기서 네 번째 □↑ 모양을 눌러봐.
지금 화면에서 ··· 바로 왼쪽에 있는 아이콘이야.

그걸 눌렀을 때 뜨는 창을 캡처해서 보여줘. 대화 공유가 나오는지, 다른 메뉴만 나오는지 보고 다음 단계 바로 알려줄게.


46b1e13e-da2c-46cd-933d-abd5b61e71c1.png

79675adc-dc4e-495d-aafb-fdecb9d1bb13.png

8be30c8a-8599-4a48-8599-9f91df4a4ab3.png
안보여

dd3aff23-7e6d-4c27-9b4e-d6027b4db750.png
add file 안보여

c5dea1c7-81d3-44da-a86d-0c417445add1.png
어디에 넣는거야?

b74cfc4c-318a-44c9-90d1-bb08014847e9.png
create api key 안보여

dfc24323-0454-41a2-88e4-c22064137ad7.png

854f2c7f-86db-459d-946e-a754d7a84860.png

8409f3cf-98e8-4a81-bc9d-c95185f6241f.png

53bae376-30f4-4051-bc25-464c8fb68a86.png

5493dc13-9ab9-465f-b3c9-d5debdc04aa5.png

5e42a707-c457-4567-83f7-1645b65b33b8.png

4b88e540-c4db-4345-973a-ef74b17884ca.png

fb585207-1bd3-44b6-8936-085cd6ba9c1c.png

b5f0dd1a-71fa-4d9d-a54b-e4a8a8da612e.png

응. 그럼 혜택·할인·지원금 위주로 우선순위를 바꾸는 게 맞아.

이번 버전에서는 맨 앞에 🎁 혜택·할인·지원금 카테고리를 넣었어. 특히 아래를 먼저 찾게 했어.

정부·지자체 지원금

인천 시민 혜택

직장인·근로자 지원금

육아 지원금·보육료·아이돌봄

카드·통신비·교통비 할인

공공요금 할인

무료 행사·할인 행사

쿠폰·바우처

은행 특판·캐시백

그리고 육아/직장인/금융 카테고리도 금액·할인율·신청기간·대상·조건이 구체적인 기사를 우선하도록 바꿨어.

혜택 우선 topics.json 다운로드

GitHub의 기존 topics.json 내용을 이 파일 내용으로 교체하면 돼.
main.py는 그대로 두면 돼. 


topics_benefits_first.json
코드


다했어

좋아. 이제 텔레그램 뉴스봇 설정은 끝났어.

현재 기준으로:

오전 9:00

오후 5:10

혜택·할인·지원금 우선

2024년생 아이 육아 정보

4대보험 직장인 제도·지원

AI·IT

미래산업

인천·송도 / 금정역 / 서울 부동산

예금·적금·금리

수도권 무료·할인 행사

이제 GitHub에서 Actions → Daily AI News Push → Run workflow로 한 번 수동 실행해봐.

텔레그램으로 온 결과를 그대로 보여주면, 내가 실제 결과를 보고 쓸데없는 기사 제거 / 필요한 분야 강화 / 너무 많은 분야 줄이기까지 한 번 더 다듬어줄게.

그리고 다음 개선은 오전 9시에 보낸 기사를 오후 5:10에 또 보내지 않도록 ‘이미 보낸 뉴스 제외’ 기능을 넣는 걸 추천해. 하루 2번 받는 구조에서는 이 기능이 꽤 중요해.


그렇게 해줘

중복 방지 기능까지 넣었어. 이번에는 파일 3개를 GitHub에 반영해야 해.

새 main.py

새 daily_news.yml

새 sent_news.json

동작은 이렇게 바뀐다.

오전 9시에 보낸 기사 제목을 sent_news.json에 기록

오후 5:10 실행 때 같은 기사는 자동 제외

같은 날 수동 테스트를 여러 번 돌려도 이미 보낸 기사는 제외

날짜가 바뀌면 자동으로 새 기록으로 시작

오후에 새 기사 자체가 없으면 새로운 주요 뉴스 없음으로 표시

GitHub에서:

기존 main.py 전체 교체

.github/workflows/daily_news.yml 전체 교체

저장소 루트에 sent_news.json 새 파일 추가하고 내용은 {}만 입력

각각 Commit changes

topics.json과 Secrets는 그대로 두면 돼.

참고로 이번 daily_news.yml은 sent_news.json을 자동 저장하려고 contents: write 권한을 사용한다. 이 파일에는 기사 중복 확인용 해시값만 저장되고 토큰/API 키는 저장되지 않아. 


main.py
코드


daily_news.yml
코드


sent_news.json
코드



6am
main.py


1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
22
23
24
25
26
27
28
29
30
31
32
33
34
35
36
37
38
39
40
41
42
43
44
45
46
47
48
49
50
51
52
53
54
55
56
57
58
59
60
import os
import re
import html
import json
import hashlib
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
SENT_FILE = "sent_news.json"

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


def load_sent_today() -> set[str]:
    """오늘 이미 발송한 기사 fingerprint를 읽는다."""
    today = datetime.now(KST).strftime("%Y-%m-%d")

    if not os.path.exists(SENT_FILE):
        return set()

    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
