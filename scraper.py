#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
과학기술정보통신부 / 기후에너지환경부 / 산업통상부 보도자료 게시판에서
'오늘' 등록된 글의 제목과 링크를 모아 public/index.html 을 생성한다.

원칙:
- 사이트 접속/파싱에 실패한 경우와, 접속은 됐지만 오늘 글이 정말 없는 경우를
  구분해서 표시한다. (실패를 "오늘 글 없음"으로 오인 표시하지 않는다.)
"""

import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date() - timedelta(days=1)
TODAY_LABEL = (datetime.now(KST) - timedelta(days=1)).strftime("%Y년 %m월 %d일")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}
TIMEOUT = 20
RETRIES = 3
RETRY_WAIT_SECONDS = 5

AGENCIES = ["과학기술정보통신부", "기후에너지환경부", "산업통상부"]

BOARD_URL = {
    "과학기술정보통신부": "https://www.msit.go.kr/bbs/list.do?sCode=user&mPid=208&mId=307",
    "기후에너지환경부": "https://mcee.go.kr/home/web/index.do?menuId=10598",
    "산업통상부": "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c",
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def fetch(url: str, label: str):
    """최대 RETRIES회 재시도 후 성공하면 Response, 전부 실패하면 None."""
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            log(f"[OK] {label}: attempt {attempt} -> status {resp.status_code}, "
                f"{len(resp.text)} chars")
            return resp
        except requests.RequestException as e:
            last_err = e
            log(f"[WARN] {label}: attempt {attempt} failed: {e}")
            if attempt < RETRIES:
                time.sleep(RETRY_WAIT_SECONDS)
    log(f"[ERROR] {label}: all {RETRIES} attempts failed ({last_err})")
    return None


def parse_date_flexible(text: str):
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST).date()
    except (TypeError, ValueError):
        pass
    m = re.search(r"(\d{4})[.\-/년\s](\d{1,2})[.\-/월\s](\d{1,2})", text)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def fetch_msit():
    """RSS 피드로 수집 (게시판과 동일한 목록을 구조화된 형태로 제공)."""
    url = "https://www.msit.go.kr/user/rss/rss.do?bbsSeqNo=94"
    resp = fetch(url, "MSIT")
    if resp is None:
        return None

    try:
        soup = BeautifulSoup(resp.text, "xml")
    except Exception as e:
        log(f"[ERROR] MSIT: XML parse failed: {e}")
        return None

    items = []
    for item in soup.find_all("item"):
        title = (item.title.get_text(strip=True) if item.title else "")
        link = (item.link.get_text(strip=True) if item.link else "")
        pub_date_raw = (item.pubDate.get_text(strip=True) if item.pubDate else "")
        if not title or not link:
            continue
        items.append({
            "title": title,
            "link": link,
            "date": parse_date_flexible(pub_date_raw),
        })
    log(f"[INFO] MSIT: {len(items)} items parsed from RSS")
    return items


def fetch_mcee():
    url = BOARD_URL["기후에너지환경부"]
    resp = fetch(url, "MCEE")
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.find_all("tr")
    if not rows:
        rows = soup.find_all("li")
    log(f"[INFO] MCEE: {len(rows)} candidate rows found")

    items = []
    seen = set()
    for row in rows:
        a = row.find("a", href=lambda h: h and "boardId=" in h)
        if a is None:
            continue
        m = re.search(r"boardId=(\d+)", a["href"])
        if not m:
            continue
        board_id = m.group(1)
        if board_id in seen:
            continue
        seen.add(board_id)

        title = a.get("title") or a.get_text(strip=True)
        title = title.strip()
        if not title:
            continue

        date_text = ""
        m_date = re.search(r"\d{4}[.\-]\d{1,2}[.\-]\d{1,2}", row.get_text(" ", strip=True))
        if m_date:
            date_text = m_date.group(0)

        detail_url = (
            f"https://mcee.go.kr/home/web/board/read.do"
            f"?menuId=10598&boardMasterId=939&boardId={board_id}"
        )
        items.append({
            "title": title,
            "link": detail_url,
            "date": parse_date_flexible(date_text),
        })
    log(f"[INFO] MCEE: {len(items)} items parsed")
    return items


def fetch_motir():
    board_code = "ATCL3f49a5a8c"
    url = BOARD_URL["산업통상부"]
    resp = fetch(url, "MOTIR")
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.find_all("tr")
    log(f"[INFO] MOTIR: {len(rows)} candidate rows found")

    id_pattern = re.compile(
        r"article\.view\(\s*['\"]?(\d+)['\"]?\s*\)|" + re.escape(board_code) + r"/(\d+)/view"
    )

    items = []
    seen = set()
    for row in rows:
        row_html = str(row)
        m = id_pattern.search(row_html)
        if not m:
            continue
        article_id = m.group(1) or m.group(2)
        if article_id in seen:
            continue
        seen.add(article_id)

        a = row.find("a")
        title = a.get_text(strip=True) if a else ""
        if not title:
            continue

        date_text = ""
        m_date = re.search(r"\d{4}[.\-]\d{1,2}[.\-]\d{1,2}", row.get_text(" ", strip=True))
        if m_date:
            date_text = m_date.group(0)

        detail_url = f"https://www.motir.go.kr/kor/article/{board_code}/{article_id}/view"
        items.append({
            "title": title,
            "link": detail_url,
            "date": parse_date_flexible(date_text),
        })
    log(f"[INFO] MOTIR: {len(items)} items parsed")
    return items


FETCHERS = {
    "과학기술정보통신부": fetch_msit,
    "기후에너지환경부": fetch_mcee,
    "산업통상부": fetch_motir,
}


def render_html(today_items: dict, fetch_failed: set) -> str:
    def item_li(item: dict) -> str:
        return (
            f'<li><a href="{escape(item["link"])}" target="_blank" rel="noopener">'
            f'{escape(item["title"])}</a></li>'
        )

    sections = []
    for agency in AGENCIES:
        items = today_items.get(agency, [])
        if agency in fetch_failed:
            body = '<p class="msg fail">사이트 접속에 실패하여 확인하지 못했습니다. 다음 자동 실행에서 다시 시도합니다.</p>'
        elif items:
            body = "<ul>" + "\n".join(item_li(it) for it in items) + "</ul>"
        else:
            body = '<p class="msg empty">오늘 등록된 보도자료 없음</p>'

        sections.append(f"""
    <section class="agency">
      <h2><a href="{escape(BOARD_URL[agency])}" target="_blank" rel="noopener">{escape(agency)}</a></h2>
      {body}
    </section>""")

    sections_html = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>기관별 오늘의 보도자료</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
          max-width: 760px; margin: 0 auto; padding: 32px 16px 60px;
          background: #f7f7f5; color: #222; }}
  header h1 {{ font-size: 22px; margin-bottom: 4px; }}
  header p {{ color: #666; font-size: 14px; margin-top: 0; }}
  section.agency {{ background: #fff; border: 1px solid #e3e2dc; border-radius: 10px;
                     margin-bottom: 20px; padding: 4px 20px 16px; }}
  section.agency h2 {{ font-size: 17px; padding: 10px 0; }}
  section.agency h2 a {{ color: #185fa5; text-decoration: none; }}
  section.agency h2 a:hover {{ text-decoration: underline; }}
  ul {{ list-style: none; margin: 0; padding: 0; }}
  li {{ padding: 8px 0; border-top: 1px solid #eee; }}
  li:first-child {{ border-top: none; }}
  li a {{ color: #222; text-decoration: none; font-size: 14px; line-height: 1.5; }}
  li a:hover {{ text-decoration: underline; color: #185fa5; }}
  .msg {{ font-size: 13px; padding: 12px 0; }}
  .msg.empty {{ color: #888; }}
  .msg.fail {{ color: #b3401f; }}
  footer {{ color: #999; font-size: 12px; text-align: center; margin-top: 32px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #17181a; color: #e8e8e6; }}
    section.agency {{ background: #232527; border-color: #33353a; }}
    li {{ border-top-color: #33353a; }}
    li a {{ color: #e8e8e6; }}
    .msg.empty {{ color: #9a9a9a; }}
  }}
</style>
</head>
<body>
  <header>
    <h1>기관별 오늘의 보도자료</h1>
    <p>{TODAY_LABEL} 기준 · 매일 자동 업데이트</p>
  </header>
  {sections_html}
  <footer>기관명을 클릭하면 해당 기관의 보도자료 게시판 전체 목록으로 이동합니다.</footer>
</body>
</html>
"""


def main() -> None:
    today_items = {}
    fetch_failed = set()

    for agency, fetcher in FETCHERS.items():
        items = fetcher()
        if items is None:
            fetch_failed.add(agency)
            log(f"[SUMMARY] {agency}: FETCH FAILED")
            continue
        matched = [it for it in items if it["date"] == TODAY]
        today_items[agency] = matched
        log(f"[SUMMARY] {agency}: {len(matched)} item(s) today (of {len(items)} total parsed)")

    html = render_html(today_items, fetch_failed)

    import os
    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    log("[INFO] public/index.html written")


if __name__ == "__main__":
    main()
