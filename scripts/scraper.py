#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
정부기관 보도자료 자동 수집기 (v5 - 안정성 강화판)

핵심 원칙:
1. direct 요청 + 프록시 4개를 순서대로, 총 3라운드까지 재시도한다.
2. 그래도 다 실패하면 "오늘 보도자료 없음"이라고 거짓으로 표시하지 않고,
   "정보를 가져오지 못했습니다"라고 명확히 구분해서 보여준다.
   (수집 실패와 "진짜로 오늘 글이 없는 것"을 절대 혼동하지 않는다.)
"""

import re
import sys
import time
import urllib.parse
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
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

REQUEST_TIMEOUT = 20
MAX_ROUNDS = 3
BACKOFF_SECONDS = [10, 20]  # 라운드 사이 대기 (라운드1 실패 후 10초, 라운드2 실패 후 20초)
MOTIR_MAX_ITEMS = 60

PROXY_TEMPLATES = [
    "https://api.allorigins.win/raw?url={}",
    "https://corsproxy.io/?url={}",
    "https://api.codetabs.com/v1/proxy?quest={}",
    "https://thingproxy.freeboard.io/fetch/{}",
]

SOURCE_BOARD_URL = {
    "과학기술정보통신부": "https://www.msit.go.kr/bbs/list.do?sCode=user&mId=307&mPid=208",
    "기후에너지환경부": "https://www.mcee.go.kr/home/web/index.do?menuId=10598",
    "산업통상부": "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c",
}


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_date_flexible(date_str: str):
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


def safe_get(url: str, label: str):
    """direct -> 프록시 4개 순서대로, 최대 MAX_ROUNDS번 재시도.
    전부 실패하면 None을 반환한다 (호출부에서 '실패'로 명확히 처리해야 함)."""
    attempts = [("direct", url)]
    encoded = urllib.parse.quote(url, safe="")
    for i, tmpl in enumerate(PROXY_TEMPLATES, start=1):
        attempts.append((f"proxy{i}", tmpl.format(encoded)))

    for round_num in range(MAX_ROUNDS):
        for method, target in attempts:
            try:
                resp = requests.get(target, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                print(f"[DEBUG] {label} [{method}] round{round_num + 1} GET -> "
                      f"status {resp.status_code}, length {len(resp.text)}", file=sys.stderr)
                resp.raise_for_status()
                if len(resp.text) < 50:
                    print(f"[WARN] {label} [{method}] response too short, trying next",
                          file=sys.stderr)
                    continue
                resp.encoding = resp.apparent_encoding or "utf-8"
                return resp
            except requests.RequestException as e:
                print(f"[WARN] {label} [{method}] round{round_num + 1} failed: {e}",
                      file=sys.stderr)
                continue
        if round_num < MAX_ROUNDS - 1:
            wait = BACKOFF_SECONDS[min(round_num, len(BACKOFF_SECONDS) - 1)]
            print(f"[INFO] {label}: {round_num + 1}라운드 전체 실패, {wait}초 대기 후 재시도",
                  file=sys.stderr)
            time.sleep(wait)

    print(f"[ERROR] {label}: 모든 시도({MAX_ROUNDS}라운드) 실패 - 이번 실행에서 수집 불가",
          file=sys.stderr)
    return None


def fetch_msit():
    """returns (items, fetch_ok)"""
    url = "https://www.msit.go.kr/user/rss/rss.do?bbsSeqNo=94"
    resp = safe_get(url, "MSIT")
    if resp is None:
        return [], False

    items = []
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        print(f"[WARN] MSIT RSS parse error: {e}", file=sys.stderr)
        return [], False  # 응답은 왔지만 파싱 실패 -> 이것도 '실패'로 취급

    for item in root.findall(".//item"):
        title = strip_html(item.findtext("title") or "")
        link = strip_html(item.findtext("link") or "")
        pub_date_raw = strip_html(item.findtext("pubDate") or "")
        if not title or not link:
            continue
        items.append({
            "source": "과학기술정보통신부",
            "title": title,
            "link": link,
            "date": parse_date_flexible(pub_date_raw),
            "date_str": pub_date_raw,
        })
    return items, True


def fetch_mcee():
    url = "https://www.mcee.go.kr/home/web/index.do?menuId=10598"
    resp = safe_get(url, "MCEE")
    if resp is None:
        return [], False

    html = resp.text
    blocks = re.findall(r"<a\b[^>]*>.*?</a>", html, flags=re.S)
    print(f"[DEBUG] MCEE: found {len(blocks)} <a> blocks in HTML", file=sys.stderr)

    items = []
    seen_ids_this_run = set()
    for block in blocks:
        if "boardMasterId=939" not in block:
            continue
        id_match = re.search(r"boardId=(\d+)", block)
        title_match = re.search(r'title="([^"]*)"', block)
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", block)
        if not id_match or not title_match:
            continue
        board_id = id_match.group(1)
        if board_id in seen_ids_this_run:
            continue
        seen_ids_this_run.add(board_id)

        title = strip_html(unescape(title_match.group(1)))
        pub_date_raw = date_match.group(1) if date_match else ""
        if not title:
            continue

        detail_url = (
            f"https://www.mcee.go.kr/home/web/board/read.do"
            f"?menuId=10598&boardMasterId=939&boardId={board_id}"
        )
        items.append({
            "source": "기후에너지환경부",
            "title": title,
            "link": detail_url,
            "date": parse_date_flexible(pub_date_raw),
            "date_str": pub_date_raw,
        })

    if not blocks:
        return [], False  # 페이지 자체가 이상하면 실패로 취급
    return items, True


def fetch_motir():
    board_code = "ATCL3f49a5a8c"
    list_url = f"https://www.motir.go.kr/kor/article/{board_code}"
    resp = safe_get(list_url, "MOTIR")
    if resp is None:
        return [], False

    html = resp.text
    id_pattern = re.compile(
        r"article\.view\(\s*['\"]?(\d+)['\"]?\s*\)|" + re.escape(board_code) + r"/(\d+)/view"
    )
    rows = re.findall(r"<tr\b.*?</tr>", html, flags=re.S | re.I)
    print(f"[DEBUG] MOTIR: found {len(rows)} <tr> rows in HTML", file=sys.stderr)

    items = []
    seen_ids_this_run = set()
    for row in rows[:MOTIR_MAX_ITEMS]:
        m = id_pattern.search(row)
        if not m:
            continue
        article_id = m.group(1) or m.group(2)
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
        items.append({
            "source": "산업통상부",
            "title": title,
            "link": detail_url,
            "date": parse_date_flexible(pub_date_raw),
            "date_str": pub_date_raw,
        })

    if not rows:
        return [], False
    return items, True


def render_html(grouped: dict, fetch_status: dict) -> str:
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
    any_fetch_failed = False

    for source, board_url in SOURCE_BOARD_URL.items():
        source_items = grouped.get(source, [])
        ok = fetch_status.get(source, False)
        total += len(source_items)

        if not ok:
            any_fetch_failed = True
            rows = (
                '<li class="fail">사이트 접속에 실패했습니다. '
                '이 기관은 이번 업데이트에서 확인하지 못했습니다. '
                '다음 자동 실행 때 다시 시도됩니다.</li>'
            )
        elif source_items:
            rows = "\n".join(item_row(it) for it in source_items)
        else:
            rows = '<li class="empty">오늘 등록된 보도자료가 없습니다. (정상 확인됨)</li>'

        badge_class = "banner-count-fail" if not ok else "banner-count"
        badge_text = "확인 실패" if not ok else f"{len(source_items)}건"

        sections.append(f"""
    <section class="agency">
      <a class="banner" href="{escape(board_url)}" target="_blank" rel="noopener">
        <span class="banner-name">{escape(source)}</span>
        <span class="{badge_class}">{badge_text}</span>
        <span class="banner-arrow">전체 보도자료 목록 보기 &rarr;</span>
      </a>
      <ul>
        {rows}
      </ul>
    </section>""")

    sections_html = "\n".join(sections)
    warning_banner = ""
    if any_fetch_failed:
        warning_banner = (
            '<div class="global-warning">⚠️ 일부 기관은 사이트 접속 문제로 이번엔 '
            '확인하지 못했습니다. 아래에서 "확인 실패" 표시를 참고하세요.</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>정부기관 보도자료 모니터</title>
<style>
  :root {{ --bg:#f7f7f5;--card:#fff;--border:#e5e3dd;--text:#2c2c2a;--muted:#6b6a63;--accent:#185fa5;--accent-dark:#0c447c;--warn:#d85a30; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0;padding:0 16px 60px;font-family:-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;background:var(--bg);color:var(--text); }}
  header {{ max-width:760px;margin:0 auto;padding:40px 0 16px; }}
  h1 {{ font-size:22px;font-weight:600;margin:0 0 6px; }}
  .updated {{ color:var(--muted);font-size:14px; }}
  .global-warning {{ max-width:760px;margin:0 auto 20px;background:#faece7;color:#4a1b0c;border:1px solid var(--warn);border-radius:8px;padding:10px 14px;font-size:13px; }}
  .agency {{ max-width:760px;margin:0 auto 28px; }}
  .banner {{ display:flex;align-items:center;gap:10px;text-decoration:none;background:var(--accent);color:#fff;border-radius:10px 10px 0 0;padding:14px 16px; }}
  .banner:hover {{ background:var(--accent-dark); }}
  .banner-name {{ font-size:16px;font-weight:700; }}
  .banner-count {{ font-size:12px;background:rgba(255,255,255,0.25);padding:2px 8px;border-radius:10px; }}
  .banner-count-fail {{ font-size:12px;background:var(--warn);padding:2px 8px;border-radius:10px;font-weight:700; }}
  .banner-arrow {{ margin-left:auto;font-size:12px;opacity:0.9; }}
  ul {{ list-style:none;margin:0;padding:0;background:var(--card);border:1px solid var(--border);border-top:none;border-radius:0 0 10px 10px; }}
  .item {{ padding:12px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:baseline;gap:12px; }}
  .item:last-child {{ border-bottom:none; }}
  .title {{ color:var(--text);text-decoration:none;font-size:14px;line-height:1.5; }}
  .title:hover {{ text-decoration:underline;color:var(--accent); }}
  .date {{ font-size:12px;color:var(--muted);white-space:nowrap; }}
  .empty {{ padding:20px 16px;color:var(--muted);font-size:13px;text-align:center; }}
  .fail {{ padding:20px 16px;color:var(--warn);font-size:13px;text-align:center;font-weight:500; }}
  footer {{ max-width:760px;margin:40px auto 0;color:var(--muted);font-size:12px;text-align:center; }}
</style>
</head>
<body>
  <header>
    <h1>정부기관 보도자료 모니터</h1>
    <div class="updated">{TODAY_STR} 기준 · 오늘 등록된 보도자료 총 {total}건</div>
  </header>
  {warning_banner}
  {sections_html}
  <footer>매일 자동 수집 (GitHub Actions) · 기관명을 클릭하면 전체 보도자료 목록으로 이동합니다</footer>
</body>
</html>
"""


def main() -> None:
    all_items = []
    fetch_status = {}

    fetchers = {
        "과학기술정보통신부": fetch_msit,
        "기후에너지환경부": fetch_mcee,
        "산업통상부": fetch_motir,
    }

    for source, fetcher in fetchers.items():
        try:
            items, ok = fetcher()
            fetch_status[source] = ok
            print(f"[INFO] {source}: {len(items)}건 수집 (fetch_ok={ok})", file=sys.stderr)
            all_items.extend(items)
        except Exception as e:
            fetch_status[source] = False
            print(f"[ERROR] {source} failed with exception: {e}", file=sys.stderr)

    today_items = [it for it in all_items if it["date"] == TODAY]

    grouped: dict = {}
    for it in today_items:
        grouped.setdefault(it["source"], []).append(it)

    html = render_html(grouped, fetch_status)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    for source in SOURCE_BOARD_URL:
        status = "OK" if fetch_status.get(source) else "FAIL"
        print(f"[INFO] {source}: 오늘 {len(grouped.get(source, []))}건 [{status}]", file=sys.stderr)
    print(f"[INFO] index.html 생성 완료 (총 {len(today_items)}건, TODAY={TODAY})", file=sys.stderr)


if __name__ == "__main__":
    main()
