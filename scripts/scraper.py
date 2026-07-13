#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
정부기관 보도자료 자동 수집기 (오늘 날짜 필터 + 기관별 그룹 버전)

- 과학기술정보통신부 (RSS)
- 기후에너지환경부 (RSS)
- 산업통상부 (게시판 스크래핑)

각 기관의 보도자료 중 "발행일이 오늘(KST)"인 것만 걸러서,
기관별로 묶어 보여줍니다. 기관명(배너)은 해당 기관 보도자료 목록으로,
개별 글 제목은 각 글의 상세 페이지로 연결됩니다.
"""

import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape, unescape

import requests

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = NOW.date()
TODAY_STR = NOW.strftime("%Y년 %m월 %d일 (%a)")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 GovDataCenterBot/1.0"
    )
}

REQUEST_TIMEOUT = 15
MOTIR_MAX_ITEMS = 60  # 목록 첫 페이지에서 몇 건까지 훑어볼지

# 기관별 배너(클릭 시 이동할 목록 페이지) 링크
SOURCE_BOARD_URL = {
    "과학기술정보통신부": "https://www.msit.go.kr/bbs/list.do?sCode=user&mId=307&mPid=208",
    "기후에너지환경부": "https://me.go.kr/home/web/index.do?menuId=281",
    "산업통상부": "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c",
}


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_date_flexible(date_str: str):
    """다양한 날짜 표기(RFC822, 2026.07.13, 2026-07-10 등)를 date 객체로 통일."""
    if not date_str:
        return None
    date_str = date_str.strip()
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST).date()
    except Exception:
        pass
    m = re.match(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", date_str)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def safe_get(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp
    except requests.RequestException as e:
        print(f"[WARN] request failed: {url} ({e})", file=sys.stderr)
        return None


def fetch_msit() -> list:
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
        pub_date_raw = strip_html(item.findtext("pubDate") or "")
        if not title or not link:
            continue
        items.append(
            {
                "source": "과학기술정보통신부",
                "title": title,
                "link": link,
                "date": parse_date_flexible(pub_date_raw),
                "date_str": pub_date_raw,
            }
        )
    return items


def fetch_mcee() -> list:
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
        pub_date_raw = strip_html(item.findtext("pubDate") or "")
        if not title or not link:
            continue
        items.append(
            {
                "source": "기후에너지환경부",
                "title": title,
                "link": link,
                "date": parse_date_flexible(pub_date_raw),
                "date_str": pub_date_raw,
            }
        )
    return items


def fetch_motir() -> list:
    board_code = "ATCL3f49a5a8c"
    list_url = f"https://www.motir.go.kr/kor/article/{board_code}"
    items = []
    resp = safe_get(list_url)
    if resp is None:
        return items

    html = resp.text
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
        pub_date_raw = date_match.group(1) if date_match else ""

        if not title:
            continue

        detail_url = f"https://www.motir.go.kr/kor/article/{board_code}/{article_id}/view"
        items.append(
            {
                "source": "산업통상부",
                "title": title,
                "link": detail_url,
                "date": parse_date_flexible(pub_date_raw),
                "date_str": pub_date_raw,
            }
        )
    return items


def render_html(grouped: dict) -> str:
    def item_row(item: dict) -> str:
        return f"""
        <li class="item">
          <a class="title" href="{escape(item['link'])}" target="_blank" rel="noopener">
            {escape(item['title'])}
          </a>
          <span class="date">{escape(item.get('date_str', ''))}</span>
        </li>"""

    sections = []
    total = 0
    for source, board_url in SOURCE_BOARD_URL.items():
        source_items = grouped.get(source, [])
        total += len(source_items)
        if source_items:
            rows = "\n".join(item_row(it) for it in source_items)
        else:
            rows = '<li class="empty">오늘 등록된 보도자료가 없습니다.</li>'

        sections.append(f"""
    <section class="agency">
      <a class="banner" href="{escape(board_url)}" target="_blank" rel="noopener">
        <span class="banner-name">{escape(source)}</span>
        <span class="banner-count">{len(source_items)}건</span>
        <span class="banner-arrow">전체 보도자료 목록 보기 &rarr;</span>
      </a>
      <ul>
        {rows}
      </ul>
    </section>""")

    sections_html = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>정부기관 보도자료 모니터</title>
<style>
  :root {{
    --bg: #f7f7f5; --card: #ffffff; --border: #e5e3dd;
    --text: #2c2c2a; --muted: #6b6a63; --accent: #185fa5; --accent-dark: #0c447c;
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
  .agency {{ max-width: 760px; margin: 0 auto 28px; }}
  .banner {{
    display: flex; align-items: center; gap: 10px; text-decoration: none;
    background: var(--accent); color: #fff; border-radius: 10px 10px 0 0;
    padding: 14px 16px;
  }}
  .banner:hover {{ background: var(--accent-dark); }}
  .banner-name {{ font-size: 16px; font-weight: 700; }}
  .banner-count {{
    font-size: 12px; background: rgba(255,255,255,0.25); padding: 2px 8px; border-radius: 10px;
  }}
  .banner-arrow {{ margin-left: auto; font-size: 12px; opacity: 0.9; }}
  ul {{
    list-style: none; margin: 0; padding: 0; background: var(--card);
    border: 1px solid var(--border); border-top: none; border-radius: 0 0 10px 10px;
  }}
  .item {{
    padding: 12px 16px; border-bottom: 1px solid var(--border);
    display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
  }}
  .item:last-child {{ border-bottom: none; }}
  .title {{ color: var(--text); text-decoration: none; font-size: 14px; line-height: 1.5; }}
  .title:hover {{ text-decoration: underline; color: var(--accent); }}
  .date {{ font-size: 12px; color: var(--muted); white-space: nowrap; }}
  .empty {{ padding: 20px 16px; color: var(--muted); font-size: 13px; text-align: center; }}
  footer {{ max-width: 760px; margin: 40px auto 0; color: var(--muted); font-size: 12px; text-align: center; }}
</style>
</head>
<body>
  <header>
    <h1>정부기관 보도자료 모니터</h1>
    <div class="updated">{TODAY_STR} 기준 · 오늘 등록된 보도자료 총 {total}건</div>
  </header>

  {sections_html}

  <footer>
    매일 자동 수집 (GitHub Actions) · 기관명을 클릭하면 전체 보도자료 목록으로 이동합니다
  </footer>
</body>
</html>
"""


def main() -> None:
    all_items = []
    for fetcher in (fetch_msit, fetch_mcee, fetch_motir):
        try:
            fetched = fetcher()
            print(f"[INFO] {fetcher.__name__}: {len(fetched)}건 수집")
            all_items.extend(fetched)
        except Exception as e:
            print(f"[ERROR] {fetcher.__name__} failed: {e}", file=sys.stderr)

    today_items = [it for it in all_items if it["date"] == TODAY]

    grouped: dict = {}
    for it in today_items:
        grouped.setdefault(it["source"], []).append(it)

    html = render_html(grouped)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    for source in SOURCE_BOARD_URL:
        print(f"[INFO] {source}: 오늘 {len(grouped.get(source, []))}건")
    print(f"[INFO] index.html 생성 완료 (총 {len(today_items)}건)")


if __name__ == "__main__":
    main()
