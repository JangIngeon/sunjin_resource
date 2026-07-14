#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
정부기관(과학기술정보통신부/기후에너지환경부/산업통상부) 및
공기업(한국전력공사/한국수자원공사/정보통신산업진흥원/한국지능정보사회진흥원)
보도자료 게시판에서 '오늘' 등록된 글의 제목과 링크를 모아 public/index.html 을 생성한다.

원칙:
- 사이트 접속/파싱에 실패한 경우와, 접속은 됐지만 오늘 글이 정말 없는 경우를
  구분해서 표시한다. (실패를 "오늘 글 없음"으로 오인 표시하지 않는다.)
- 오늘 글이 없더라도 각 기관/기업별로 "더보기"를 펼치면 날짜와 무관하게
  가장 최근 게시물 5건을 확인할 수 있다.
"""

import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
TODAY_LABEL = datetime.now(KST).strftime("%Y년 %m월 %d일")

# 전날 기준
# KST = timezone(timedelta(hours=9))
# TODAY = datetime.now(KST).date() - timedelta(days=1)
# TODAY_LABEL = (datetime.now(KST) - timedelta(days=1)).strftime("%Y년 %m월 %d일")

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
RECENT_LIMIT = 5

GOV_AGENCIES = ["과학기술정보통신부", "기후에너지환경부", "산업통상부"]
PUBLIC_ENTERPRISES = ["한국전력공사", "한국수자원공사", "정보통신산업진흥원", "한국지능정보사회진흥원"]

CATEGORIES = [
    ("gov", "정부기관", GOV_AGENCIES),
    ("public", "공기업", PUBLIC_ENTERPRISES),
]

BOARD_URL = {
    "과학기술정보통신부": "https://www.msit.go.kr/bbs/list.do?sCode=user&mPid=208&mId=307",
    "기후에너지환경부": "https://mcee.go.kr/home/web/index.do?menuId=10598",
    "산업통상부": "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c",
    "한국전력공사": "https://www.kepco.co.kr/home/media/newsroom/pr/boardList.do",
    "한국수자원공사": "https://www.kwater.or.kr/news/repoList.do?brdId=KO26&s_mid=36",
    "정보통신산업진흥원": "https://www.nipa.kr/home/4-4-1",
    "한국지능정보사회진흥원": "https://nia.or.kr/site/nia_kor/ex/bbs/List.do?cbIdx=90549",
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


def extract_date_text(text: str) -> str:
    m = re.search(r"\d{4}[.\-]\d{1,2}[.\-]\d{1,2}", text)
    return m.group(0) if m else ""


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
            "date_raw": pub_date_raw,
        })
    log(f"[INFO] MSIT: {len(items)} items parsed from RSS")
    return items or None


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

        date_text = extract_date_text(row.get_text(" ", strip=True))

        detail_url = (
            f"https://mcee.go.kr/home/web/board/read.do"
            f"?menuId=10598&boardMasterId=939&boardId={board_id}"
        )
        items.append({
            "title": title,
            "link": detail_url,
            "date": parse_date_flexible(date_text),
            "date_raw": date_text,
        })
    log(f"[INFO] MCEE: {len(items)} items parsed")
    return items or None


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

        date_text = extract_date_text(row.get_text(" ", strip=True))

        detail_url = f"https://www.motir.go.kr/kor/article/{board_code}/{article_id}/view"
        items.append({
            "title": title,
            "link": detail_url,
            "date": parse_date_flexible(date_text),
            "date_raw": date_text,
        })
    log(f"[INFO] MOTIR: {len(items)} items parsed")
    return items or None


# 제목으로 취급하지 않을 게시판 UI 텍스트(페이지네이션, 버튼 등)
_TITLE_BLACKLIST = {"이전", "다음", "처음", "마지막", "목록", "검색", "글쓰기", "인쇄", "공유", "리스트"}

# 실제 게시글 목록 테이블을 찾기 위해 우선순위대로 시도하는 CSS 선택자
_TABLE_SELECTORS = [
    "table.bdListTbl", "table.board_list", "table.bbs_list", "table.tbl_list",
    "table.boardList", ".board-list table", ".bbsList table", ".board_list table",
    "table",
]
_LIST_SELECTORS = [
    "ul.board_list li", "ul.bbs_list li", "div.board_list li", ".board-list li",
]


def fetch_generic_board(url: str, label: str):
    """구조를 미리 알 수 없는 게시판 공용 파서.
    표(table) 기반 목록을 우선 시도하고, 없으면 리스트(ul/li) 형태를 시도한다.
    행 안의 첫 <a href> 를 게시글 링크/제목으로, 행 텍스트에서 날짜 패턴을 찾는다."""
    resp = fetch(url, label)
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    rows = []
    for sel in _TABLE_SELECTORS:
        table = soup.select_one(sel)
        if table:
            candidate = table.find_all("tr")
            if len(candidate) > 1:
                rows = candidate
                break
    if not rows:
        for sel in _LIST_SELECTORS:
            candidate = soup.select(sel)
            if len(candidate) > 1:
                rows = candidate
                break

    log(f"[INFO] {label}: {len(rows)} candidate rows found")
    if not rows:
        log(f"[ERROR] {label}: 게시판 구조를 인식하지 못했습니다")
        return None

    items = []
    seen = set()
    for row in rows:
        if row.find("th") and not row.find("td"):
            continue  # 헤더 행

        a = row.find("a", href=True)
        if a is None:
            continue
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        title = a.get("title") or a.get_text(strip=True)
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) < 4 or title in _TITLE_BLACKLIST:
            continue

        full_link = urljoin(url, href)
        if full_link in seen:
            continue
        seen.add(full_link)

        date_text = extract_date_text(row.get_text(" ", strip=True))
        items.append({
            "title": title,
            "link": full_link,
            "date": parse_date_flexible(date_text),
            "date_raw": date_text,
        })

    log(f"[INFO] {label}: {len(items)} items parsed")
    return items or None


def fetch_kepco():
    return fetch_generic_board(BOARD_URL["한국전력공사"], "KEPCO")


def fetch_kwater():
    return fetch_generic_board(BOARD_URL["한국수자원공사"], "K-water")


def fetch_nipa():
    return fetch_generic_board(BOARD_URL["정보통신산업진흥원"], "NIPA")


def fetch_nia():
    return fetch_generic_board(BOARD_URL["한국지능정보사회진흥원"], "NIA")


FETCHERS = {
    "과학기술정보통신부": fetch_msit,
    "기후에너지환경부": fetch_mcee,
    "산업통상부": fetch_motir,
    "한국전력공사": fetch_kepco,
    "한국수자원공사": fetch_kwater,
    "정보통신산업진흥원": fetch_nipa,
    "한국지능정보사회진흥원": fetch_nia,
}


def render_html(today_items: dict, recent_items: dict, fetch_failed: set) -> str:
    def date_label(item: dict) -> str:
        if item.get("date"):
            return item["date"].strftime("%Y-%m-%d")
        return item.get("date_raw") or "-"

    def item_li(item: dict) -> str:
        return (
            f'<li><a href="{escape(item["link"])}" target="_blank" rel="noopener">'
            f'{escape(item["title"])}</a><span class="date">{escape(date_label(item))}</span></li>'
        )

    def org_section(org: str) -> str:
        today = today_items.get(org, [])
        recent = recent_items.get(org, [])

        if org in fetch_failed:
            body = '<p class="msg fail">사이트 접속에 실패하여 확인하지 못했습니다. 다음 자동 실행에서 다시 시도합니다.</p>'
        elif today:
            body = "<ul>" + "\n".join(item_li(it) for it in today) + "</ul>"
        else:
            body = '<p class="msg empty">오늘 등록된 보도자료 없음</p>'

        more_html = ""
        if org not in fetch_failed and recent:
            more_html = f"""
      <details class="more">
        <summary>더보기 (최근 {len(recent)}건, 날짜 무관)</summary>
        <ul>{"".join(item_li(it) for it in recent)}</ul>
      </details>"""

        return f"""
    <section class="agency">
      <h2><a href="{escape(BOARD_URL[org])}" target="_blank" rel="noopener">{escape(org)}</a></h2>
      {body}{more_html}
    </section>"""

    tab_buttons = []
    tab_panels = []
    for i, (key, label, orgs) in enumerate(CATEGORIES):
        active = " active" if i == 0 else ""
        tab_buttons.append(
            f'<button class="tab-btn{active}" data-tab="{key}" onclick="showTab(\'{key}\')">{escape(label)}</button>'
        )
        sections_html = "\n".join(org_section(org) for org in orgs)
        tab_panels.append(f'<div id="tab-{key}" class="tab-panel{active}">{sections_html}\n    </div>')

    tab_buttons_html = "\n    ".join(tab_buttons)
    tab_panels_html = "\n  ".join(tab_panels)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>정부기관·공기업 오늘의 보도자료</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
          max-width: 760px; margin: 0 auto; padding: 32px 16px 60px;
          background: #f7f7f5; color: #222; }}
  header h1 {{ font-size: 22px; margin-bottom: 4px; }}
  header p {{ color: #666; font-size: 14px; margin-top: 0; }}
  .tab-banner {{ display: flex; gap: 8px; margin: 20px 0 24px; }}
  .tab-btn {{ flex: 1; padding: 12px 0; border: 1px solid #d8d6cf; border-radius: 10px;
              background: #fff; color: #444; font-size: 15px; font-weight: 600;
              cursor: pointer; }}
  .tab-btn.active {{ background: #185fa5; border-color: #185fa5; color: #fff; }}
  .tab-panel {{ display: none; }}
  .tab-panel.active {{ display: block; }}
  section.agency {{ background: #fff; border: 1px solid #e3e2dc; border-radius: 10px;
                     margin-bottom: 20px; padding: 4px 20px 16px; }}
  section.agency h2 {{ font-size: 17px; padding: 10px 0; }}
  section.agency h2 a {{ color: #185fa5; text-decoration: none; }}
  section.agency h2 a:hover {{ text-decoration: underline; }}
  ul {{ list-style: none; margin: 0; padding: 0; }}
  li {{ padding: 8px 0; border-top: 1px solid #eee; display: flex; justify-content: space-between;
        align-items: baseline; gap: 12px; }}
  li:first-child {{ border-top: none; }}
  li a {{ color: #222; text-decoration: none; font-size: 14px; line-height: 1.5; }}
  li a:hover {{ text-decoration: underline; color: #185fa5; }}
  li .date {{ font-size: 12px; color: #888; white-space: nowrap; }}
  .msg {{ font-size: 13px; padding: 12px 0; }}
  .msg.empty {{ color: #888; }}
  .msg.fail {{ color: #b3401f; }}
  details.more {{ margin-top: 8px; }}
  details.more summary {{ font-size: 13px; color: #185fa5; cursor: pointer; padding: 8px 0; }}
  details.more ul {{ padding-top: 4px; }}
  footer {{ color: #999; font-size: 12px; text-align: center; margin-top: 32px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #17181a; color: #e8e8e6; }}
    .tab-btn {{ background: #232527; border-color: #33353a; color: #ccc; }}
    section.agency {{ background: #232527; border-color: #33353a; }}
    li {{ border-top-color: #33353a; }}
    li a {{ color: #e8e8e6; }}
    li .date {{ color: #999; }}
    .msg.empty {{ color: #9a9a9a; }}
  }}
</style>
</head>
<body>
  <header>
    <h1>정부기관·공기업 오늘의 보도자료</h1>
    <p>{TODAY_LABEL} 기준 · 매일 자동 업데이트</p>
  </header>
  <div class="tab-banner">
    {tab_buttons_html}
  </div>
  {tab_panels_html}
  <footer>기관·기업명을 클릭하면 해당 보도자료 게시판 전체 목록으로 이동합니다.</footer>
  <script>
    function showTab(tab) {{
      document.querySelectorAll('.tab-panel').forEach(function (el) {{ el.classList.remove('active'); }});
      document.querySelectorAll('.tab-btn').forEach(function (el) {{ el.classList.remove('active'); }});
      document.getElementById('tab-' + tab).classList.add('active');
      document.querySelector('.tab-btn[data-tab="' + tab + '"]').classList.add('active');
    }}
  </script>
</body>
</html>
"""


def main() -> None:
    today_items = {}
    recent_items = {}
    fetch_failed = set()

    for org, fetcher in FETCHERS.items():
        items = fetcher()
        if items is None:
            fetch_failed.add(org)
            log(f"[SUMMARY] {org}: FETCH FAILED")
            continue
        matched = [it for it in items if it["date"] == TODAY]
        today_items[org] = matched
        recent_items[org] = items[:RECENT_LIMIT]
        log(f"[SUMMARY] {org}: {len(matched)} item(s) today (of {len(items)} total parsed)")

    html = render_html(today_items, recent_items, fetch_failed)

    import os
    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    log("[INFO] public/index.html written")


if __name__ == "__main__":
    main()
