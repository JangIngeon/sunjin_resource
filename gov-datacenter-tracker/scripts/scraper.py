#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
정부기관 AI 데이터센터 관련 보도자료 자동 수집기

- 과학기술정보통신부 (RSS)
- 기후에너지환경부 (RSS)
- 산업통상부 (게시판 스크래핑)

매일 GitHub Actions에서 실행되어:
1. 각 기관 최신 보도자료를 가져오고
2. 키워드로 필터링하고
3. 이미 본 글(data/history.json)은 제외하고
4. index.html을 새로 생성한다
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import unescape, escape

import requests

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST)
TODAY_STR = TODAY.strftime("%Y년 %m월 %d일 (%a)")

# 필터링 키워드 - 여기를 수정하면 관심 주제를 바꿀 수 있습니다.
KEYWORDS = ["데이터센터", "인공지능", "AI", "GPU", "AX", "클라우드"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 GovDataCenterBot/1.0"
    )
}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_PATH = os.path.join(BASE_DIR, "data", "history.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "index.html")

REQUEST_TIMEOUT = 15
MOTIR_MAX_ITEMS = 40  # 목록 첫 페이지에서 몇 건까지 확인할지


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------

def strip_html(text: str) -> str:
    """HTML 태그 제거 + 엔티티 unescape."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def contains_keyword(text: str) -> bool:
    if not text:
        return False
    return any(kw.lower() in text.lower() for kw in KEYWORDS)


def load_history() -> dict:
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"seen_ids": []}


def save_history(history: dict) -> None:
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    # 무한정 커지지 않도록 최근 5000개만 유지
    history["seen_ids"] = history["seen_ids"][-5000:]
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def safe_get(url: str) -> requests.Response | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp
    except requests.RequestException as e:
        print(f"[WARN] request failed: {url} ({e})", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# 소스별 수집기
# ---------------------------------------------------------------------------

def fetch_msit() -> list[dict]:
    """과학기술정보통신부 보도자료 RSS."""
    url = "https://www.msit.go.kr/user/rss/rss.do?bbsSeqNo=94"
    items = []
    resp = safe_get(url)
    if resp is None:
        return items
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        print(f"[WARN] MSIT RSS parse error: {e}", file=sys.stderr)
        return items

    for item in root.findall(".//item"):
        title = strip_html(item.findtext("title") or "")
        link = strip_html(item.findtext("link") or "")
        pub_date = strip_html(item.findtext("pubDate") or "")
        content_encoded = item.findtext(
            "{http://purl.org/rss/1.0/modules/content/}encoded"
        ) or ""
        summary = strip_html(content_encoded)[:150]
        if not title or not link:
            continue
        items.append(
            {
                "source": "과학기술정보통신부",
                "title": title,
                "link": link,
                "date": pub_date,
                "summary": summary,
                "id": f"msit:{link}",
            }
        )
    return items


def fetch_mcee() -> list[dict]:
    """기후에너지환경부 보도·해명자료 RSS."""
    url = "https://www.me.go.kr/home/web/board/rss.do?menuId=286&boardMasterId=1"
    items = []
    resp = safe_get(url)
    if resp is None:
        return items
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        print(f"[WARN] MCEE RSS parse error: {e}", file=sys.stderr)
        return items

    for item in root.findall(".//item"):
        title = strip_html(item.findtext("title") or "")
        link = strip_html(item.findtext("link") or "")
        pub_date = strip_html(item.findtext("pubDate") or "")
        summary = strip_html(item.findtext("description") or "")[:150]
        if not title or not link:
            continue
        items.append(
            {
                "source": "기후에너지환경부",
                "title": title,
                "link": link,
                "date": pub_date,
                "summary": summary,
                "id": f"mcee:{link}",
            }
        )
    return items


def fetch_motir() -> list[dict]:
    """산업통상부 보도·참고자료 게시판.

    목록 페이지는 JS(`article.view('12345')`)로 상세 페이지를 열기 때문에,
    href/onclick에서 게시글 번호를 정규식으로 추출한 뒤
    실제 상세 페이지 URL(/kor/article/ATCL3f49a5a8c/{id}/view)을 직접 구성해 접근한다.
    """
    board_code = "ATCL3f49a5a8c"
    list_url = f"https://www.motir.go.kr/kor/article/{board_code}"
    items = []
    resp = safe_get(list_url)
    if resp is None:
        return items

    html = resp.text

    # <tr> 단위로 잘라서 각 행에서 글번호 + 제목 + 날짜를 함께 추출 (열 순서에 안 흔들리도록)
    rows = re.findall(r"<tr\b.*?</tr>", html, flags=re.S | re.I)
    seen_ids_this_run = set()

    for row in rows[:MOTIR_MAX_ITEMS]:
        id_match = re.search(r"article\.view\(\s*'(\d+)'\s*\)", row)
        if not id_match:
            continue
        article_id = id_match.group(1)
        if article_id in seen_ids_this_run:
            continue
        seen_ids_this_run.add(article_id)

        title_match = re.search(r">([^<>]{4,200})</a>", row)
        title = strip_html(title_match.group(1)) if title_match else ""

        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", row)
        pub_date = date_match.group(1) if date_match else ""

        if not title:
            continue
        if not contains_keyword(title):
            continue  # 목록 단계에서 이미 걸러서, 불필요한 상세페이지 요청을 줄인다

        detail_url = f"https://www.motir.go.kr/kor/article/{board_code}/{article_id}/view"
        items.append(
            {
                "source": "산업통상부",
                "title": title,
                "link": detail_url,
                "date": pub_date,
                "summary": "",
                "id": f"motir:{article_id}",
            }
        )
    return items


# ---------------------------------------------------------------------------
# HTML 생성
# ---------------------------------------------------------------------------

def render_html(new_items: list[dict], all_recent_items: list[dict]) -> str:
    def card(item: dict, is_new: bool) -> str:
        badge = '<span class="badge">NEW</span>' if is_new else ""
        summary_html = (
            f'<p class="summary">{escape(item["summary"])}</p>' if item.get("summary") else ""
        )
        return f"""
        <li class="card">
          <div class="card-head">
            <span class="source">{escape(item['source'])}</span>
            {badge}
          </div>
          <a class="title" href="{escape(item['link'])}" target="_blank" rel="noopener">
            {escape(item['title'])}
          </a>
          {summary_html}
          <div class="date">{escape(item.get('date', ''))}</div>
        </li>"""

    new_cards = "\n".join(card(i, True) for i in new_items) or (
        '<li class="empty">오늘은 새로운 관련 보도자료가 없습니다.</li>'
    )
    all_cards = "\n".join(
        card(i, i["id"] in {n["id"] for n in new_items}) for i in all_recent_items
    )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>정부기관 AI 데이터센터 보도자료 모니터</title>
<style>
  :root {{
    --bg: #f7f7f5; --card: #ffffff; --border: #e5e3dd;
    --text: #2c2c2a; --muted: #6b6a63; --accent: #185fa5;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0 16px 60px;
    font-family: -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
    background: var(--bg); color: var(--text);
  }}
  header {{ max-width: 760px; margin: 0 auto; padding: 40px 0 16px; }}
  h1 {{ font-size: 22px; font-weight: 600; margin: 0 0 6px; }}
  .updated {{ color: var(--muted); font-size: 14px; }}
  .section-title {{ max-width: 760px; margin: 32px auto 12px; font-size: 16px; font-weight: 600; }}
  ul {{ list-style: none; margin: 0 auto; padding: 0; max-width: 760px; display: flex; flex-direction: column; gap: 10px; }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px;
  }}
  .card-head {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }}
  .source {{ font-size: 12px; font-weight: 600; color: var(--accent); }}
  .badge {{
    font-size: 10px; font-weight: 700; color: #fff; background: #d85a30;
    padding: 2px 6px; border-radius: 4px; letter-spacing: 0.03em;
  }}
  .title {{ display: block; font-size: 15px; font-weight: 500; color: var(--text); text-decoration: none; line-height: 1.5; }}
  .title:hover {{ text-decoration: underline; }}
  .summary {{ font-size: 13px; color: var(--muted); margin: 6px 0 0; line-height: 1.6; }}
  .date {{ font-size: 12px; color: var(--muted); margin-top: 8px; }}
  .empty {{ text-align: center; color: var(--muted); padding: 24px 0; background: none; border: none; }}
  footer {{ max-width: 760px; margin: 40px auto 0; color: var(--muted); font-size: 12px; text-align: center; }}
</style>
</head>
<body>
  <header>
    <h1>정부기관 AI 데이터센터 보도자료 모니터</h1>
    <div class="updated">최종 업데이트: {TODAY_STR} · 과기정통부 · 산업통상부 · 기후에너지환경부</div>
  </header>

  <div class="section-title">오늘의 신규 보도자료 ({len(new_items)}건)</div>
  <ul>
    {new_cards}
  </ul>

  <div class="section-title">최근 전체 보기</div>
  <ul>
    {all_cards}
  </ul>

  <footer>
    키워드: {", ".join(KEYWORDS)} · 매일 자동 수집 (GitHub Actions)
  </footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main() -> None:
    history = load_history()
    seen = set(history["seen_ids"])

    all_items = []
    for fetcher in (fetch_msit, fetch_mcee, fetch_motir):
        try:
            fetched = fetcher()
            print(f"[INFO] {fetcher.__name__}: {len(fetched)}건 수집")
            all_items.extend(fetched)
        except Exception as e:  # 한 소스가 실패해도 나머지는 계속 진행
            print(f"[ERROR] {fetcher.__name__} failed: {e}", file=sys.stderr)

    # 키워드 필터 (제목 또는 요약에 포함)
    relevant = [
        it for it in all_items if contains_keyword(it["title"]) or contains_keyword(it["summary"])
    ]

    # 신규 항목만 추출
    new_items = [it for it in relevant if it["id"] not in seen]

    # history 업데이트
    history["seen_ids"] = list(seen | {it["id"] for it in relevant})
    save_history(history)

    # 화면에 보여줄 "최근 전체" = 이번 실행에서 수집된 관련 항목 전체 (최신순 정렬은 소스 순서를 그대로 사용)
    html = render_html(new_items=new_items, all_recent_items=relevant)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[INFO] 신규 {len(new_items)}건 / 전체 관련 {len(relevant)}건 -> {OUTPUT_PATH} 생성 완료")


if __name__ == "__main__":
    main()
