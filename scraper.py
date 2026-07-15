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

import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape, unescape
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
TODAY_LABEL = datetime.now(KST).strftime("%Y년 %m월 %d일")
GENERATED_AT_LABEL = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

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

GOV_AGENCIES = ["과학기술정보통신부", "기후에너지환경부", "산업통상부", "국가인공지능전략위원회"]
PUBLIC_ENTERPRISES = ["한국전력공사", "한국수자원공사", "정보통신산업진흥원", "한국지능정보사회진흥원"]

CATEGORIES = [
    ("gov", "정부기관", GOV_AGENCIES),
    ("public", "공기업", PUBLIC_ENTERPRISES),
]

BOARD_URL = {
    "과학기술정보통신부": "https://www.msit.go.kr/bbs/list.do?sCode=user&mPid=208&mId=307",
    "기후에너지환경부": "https://mcee.go.kr/home/web/index.do?menuId=10598",
    "산업통상부": "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c",
    "국가인공지능전략위원회": "https://www.aikorea.go.kr/web/board/brdList.do?menu_cd=000012",
    "한국전력공사": "https://www.kepco.co.kr/home/media/newsroom/pr/boardList.do",
    "한국수자원공사": "https://www.kwater.or.kr/news/repoList.do?brdId=KO26&s_mid=36",
    "정보통신산업진흥원": "https://www.nipa.kr/home/4-4-1",
    "한국지능정보사회진흥원": "https://nia.or.kr/site/nia_kor/ex/bbs/List.do?cbIdx=90549",
}

# --- "지자체 관련 기사" (AI데이터센터/AIDC + 지역명) 배너용 설정 ---------------------

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
NAVER_NEWS_QUERIES = ["AI데이터센터", "AIDC", "AI팩토리"]
NAVER_DISPLAY = 100
NAVER_MAX_PAGES = 3  # 쿼리당 최대 100 x 3 = 300건 후보 확보
AIDC_TOP_N = 20

# --- "국내 기업 관련 기사" (AI데이터센터/AIDC + 국내 기업명) 배너용 설정 -----------------

COMPANY_NAMES_PATH = os.path.join("data", "company_names.txt")
COMPANY_TOP_N = 20


def load_company_names():
    if not os.path.exists(COMPANY_NAMES_PATH):
        log(f"[WARN] {COMPANY_NAMES_PATH} not found - 국내 기업 목록 없이 진행합니다")
        return []
    with open(COMPANY_NAMES_PATH, encoding="utf-8") as f:
        names = [line.strip() for line in f if line.strip()]
    # 짧은 이름이 긴 이름의 부분 문자열인 경우(예: "이노텍" vs "LG이노텍")
    # 더 구체적인(긴) 이름이 먼저 매칭되도록 길이 내림차순 정렬
    names.sort(key=len, reverse=True)
    return names


COMPANY_NAMES = load_company_names()


_company_names_norm_cache = None


def find_company_in_text(text: str):
    """대소문자·띄어쓰기 구분 없이 국내 기업명을 찾는다."""
    global _company_names_norm_cache
    if _company_names_norm_cache is None:
        _company_names_norm_cache = [(normalize_for_match(n), n) for n in COMPANY_NAMES]
    norm_text = normalize_for_match(text)
    for norm_name, name in _company_names_norm_cache:
        if norm_name in norm_text:
            return name
    return None


# --- "해외 기업 관련 기사" (AI데이터센터/AIDC/AI팩토리 + 해외 기업명, 네이버 검색) --------

OVERSEAS_NAVER_QUERIES = ["AI데이터센터", "AI DATA CENTER", "AIDC", "AI팩토리", "AI FACTORY"]
OVERSEAS_TOP_N = 20

# 키워드 정규화 매칭용(띄어쓰기/대소문자 무시)
OVERSEAS_KEYWORDS_NORM = ["ai데이터센터", "aidatacenter", "aidc", "ai팩토리", "aifactory"]

OVERSEAS_COMPANY_NAMES_PATH = os.path.join("data", "company_names_overseas.txt")


def load_overseas_company_names():
    if not os.path.exists(OVERSEAS_COMPANY_NAMES_PATH):
        log(f"[WARN] {OVERSEAS_COMPANY_NAMES_PATH} not found - 해외 기업 목록 없이 진행합니다")
        return []
    with open(OVERSEAS_COMPANY_NAMES_PATH, encoding="utf-8") as f:
        names = [line.strip() for line in f if line.strip()]
    # 짧은 이름이 긴 이름의 부분 문자열인 경우를 대비해 긴 이름을 먼저 매칭
    names.sort(key=len, reverse=True)
    return names


OVERSEAS_COMPANY_NAMES = load_overseas_company_names()


_overseas_company_names_norm_cache = None


def find_overseas_company_in_text(text: str):
    """대소문자·띄어쓰기 구분 없이 해외 기업명을 찾는다."""
    global _overseas_company_names_norm_cache
    if _overseas_company_names_norm_cache is None:
        _overseas_company_names_norm_cache = [(normalize_for_match(n), n) for n in OVERSEAS_COMPANY_NAMES]
    norm_text = normalize_for_match(text)
    for norm_name, name in _overseas_company_names_norm_cache:
        if norm_name in norm_text:
            return name
    return None


def fetch_overseas_company_news():
    """'AI데이터센터'/'AI DATA CENTER'/'AIDC'/'AI팩토리'/'AI FACTORY' + 해외 기업명이
    함께 언급된 기사 상위 20건 (네이버 뉴스 검색). 반환값: (items, ok)."""
    seen_links = set()
    candidates = []
    any_ok = False

    for query in OVERSEAS_NAVER_QUERIES:
        raw_items = fetch_naver_news_raw(query)
        if raw_items is None:
            continue
        any_ok = True

        for it in raw_items:
            link = it.get("originallink") or it.get("link") or ""
            if not link or link in seen_links:
                continue
            seen_links.add(link)

            title = clean_naver_text(it.get("title", ""))
            desc = clean_naver_text(it.get("description", ""))
            if not title:
                continue

            combined_norm = normalize_for_match(title + " " + desc)
            if not any(k in combined_norm for k in OVERSEAS_KEYWORDS_NORM):
                continue

            company = find_overseas_company_in_text(title) or find_overseas_company_in_text(desc)
            if not company:
                continue

            pub_dt = None
            try:
                pub_dt = parsedate_to_datetime(it.get("pubDate", ""))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=KST)
                pub_dt = pub_dt.astimezone(KST)
            except (TypeError, ValueError):
                pub_dt = None

            display_link = it.get("link") or link
            candidates.append({
                "title": title,
                "summary": desc,
                "link": display_link,
                "press": press_name_from_link(link),
                "pub_dt": pub_dt,
                "company": company,
            })

    if not any_ok:
        log("[SUMMARY] OVERSEAS: FETCH FAILED")
        return [], False

    candidates.sort(key=lambda x: x["pub_dt"] or datetime.min.replace(tzinfo=KST), reverse=True)
    top = candidates[:OVERSEAS_TOP_N]
    log(f"[SUMMARY] OVERSEAS: {len(candidates)}건 매칭, 상위 {len(top)}건 표시")
    return top, True


# 국내 지역명(시/도 정식명칭·약칭 + 주요 시/군/구). 기사 "제목"에 이 중 하나라도
# 포함되면 지자체 관련 기사로 인정한다. 다소 방대한 목록이라 완벽하지 않을 수 있음
# (누락된 지역명이 있으면 추가하면 됨).
REGION_NAMES = [
    # 17개 시/도 정식명칭
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시",
    "울산광역시", "세종특별자치시", "경기도", "강원특별자치도", "충청북도", "충청남도",
    "전북특별자치도", "전라남도", "경상북도", "경상남도", "제주특별자치도",
    # 시/도 약칭
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
    # 서울 자치구
    "강남구", "강동구", "강북구", "강서구", "관악구", "광진구", "구로구", "금천구",
    "노원구", "도봉구", "동대문구", "동작구", "마포구", "서대문구", "서초구", "성동구",
    "성북구", "송파구", "양천구", "영등포구", "용산구", "은평구", "종로구", "중랑구",
    # 부산/대구/인천/광주/대전/울산 구·군 (일부는 여러 시에 중복 존재)
    "금정구", "동래구", "부산진구", "북구", "사상구", "사하구", "수영구", "연제구",
    "영도구", "해운대구", "기장군", "달서구", "수성구", "달성군", "군위군",
    "계양구", "남동구", "미추홀구", "부평구", "연수구", "강화군", "옹진군",
    "광산구", "대덕구", "유성구", "울주군", "동구", "서구", "남구", "중구",
    # 경기도
    "수원시", "성남시", "의정부시", "안양시", "부천시", "광명시", "평택시", "동두천시",
    "안산시", "고양시", "과천시", "구리시", "남양주시", "오산시", "시흥시", "군포시",
    "의왕시", "하남시", "용인시", "파주시", "이천시", "안성시", "김포시", "화성시",
    "광주시", "양주시", "포천시", "여주시", "연천군", "가평군", "양평군",
    # 강원특별자치도
    "춘천시", "원주시", "강릉시", "동해시", "태백시", "속초시", "삼척시", "홍천군",
    "횡성군", "영월군", "평창군", "정선군", "철원군", "화천군", "양구군", "인제군",
    "고성군", "양양군",
    # 충청북도
    "청주시", "충주시", "제천시", "보은군", "옥천군", "영동군", "증평군", "진천군",
    "괴산군", "음성군", "단양군",
    # 충청남도
    "천안시", "공주시", "보령시", "아산시", "서산시", "논산시", "계룡시", "당진시",
    "금산군", "부여군", "서천군", "청양군", "홍성군", "예산군", "태안군",
    # 전북특별자치도
    "전주시", "군산시", "익산시", "정읍시", "남원시", "김제시", "완주군", "진안군",
    "무주군", "장수군", "임실군", "순창군", "고창군", "부안군",
    # 전라남도
    "목포시", "여수시", "순천시", "나주시", "광양시", "담양군", "곡성군", "구례군",
    "고흥군", "보성군", "화순군", "장흥군", "강진군", "해남군", "영암군", "무안군",
    "함평군", "영광군", "장성군", "완도군", "진도군", "신안군",
    # 경상북도
    "포항시", "경주시", "김천시", "안동시", "구미시", "영주시", "영천시", "상주시",
    "문경시", "경산시", "의성군", "청송군", "영양군", "영덕군", "청도군", "고령군",
    "성주군", "칠곡군", "예천군", "봉화군", "울진군", "울릉군",
    # 경상남도
    "창원시", "진주시", "통영시", "사천시", "김해시", "밀양시", "거제시", "양산시",
    "의령군", "함안군", "창녕군", "고성군", "남해군", "하동군", "산청군", "함양군",
    "거창군", "합천군",
    # 제주특별자치도
    "제주시", "서귀포시",
    # 흔히 쓰이는 별칭/특수 지명
    "새만금",
]

# 언론사 도메인 -> 표시용 이름 (Naver API가 언론사명을 직접 주지 않아 링크로 추정)
PRESS_DOMAIN_MAP = {
    "yna.co.kr": "연합뉴스", "yonhapnews.co.kr": "연합뉴스",
    "chosun.com": "조선일보", "donga.com": "동아일보", "joongang.co.kr": "중앙일보",
    "hani.co.kr": "한겨레", "khan.co.kr": "경향신문", "mk.co.kr": "매일경제",
    "hankyung.com": "한국경제", "sedaily.com": "서울경제", "edaily.co.kr": "이데일리",
    "news1.kr": "뉴스1", "newsis.com": "뉴시스", "ytn.co.kr": "YTN",
    "mbn.co.kr": "MBN", "sbs.co.kr": "SBS", "imbc.com": "MBC", "kbs.co.kr": "KBS",
    "yonhap.co.kr": "연합뉴스", "hankookilbo.com": "한국일보", "seoul.co.kr": "서울신문",
    "fnnews.com": "파이낸셜뉴스", "asiae.co.kr": "아시아경제", "etnews.com": "전자신문",
    "zdnet.co.kr": "지디넷코리아", "dt.co.kr": "디지털타임스", "moneys.co.kr": "머니S",
    "newsway.co.kr": "뉴스웨이", "heraldcorp.com": "헤럴드경제",
    "reuters.com": "Reuters", "bloomberg.com": "Bloomberg", "techcrunch.com": "TechCrunch",
    "theverge.com": "The Verge", "datacenterdynamics.com": "Data Center Dynamics",
    "cnbc.com": "CNBC", "wsj.com": "WSJ", "ft.com": "Financial Times",
    "theregister.com": "The Register", "venturebeat.com": "VentureBeat",
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
    """카드형 목록(div.media-list-item). 상세는 JS(fn_Detail)로 폼 제출하는 방식이라
    실제 상세 URL을 GET 파라미터로 구성해서 링크를 만든다."""
    url = BOARD_URL["한국전력공사"]
    resp = fetch(url, "KEPCO")
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.select("div.media-list-item")
    log(f"[INFO] KEPCO: {len(cards)} candidate rows found")

    pattern = re.compile(r"fn_Detail\('(\d+)','(\d+)'\)")
    items = []
    seen = set()
    for card in cards:
        a = card.find("a", href=True)
        if a is None:
            continue
        m = pattern.search(a["href"])
        if not m:
            continue
        board_mng_no, board_no = m.groups()
        key = (board_mng_no, board_no)
        if key in seen:
            continue
        seen.add(key)

        title_el = card.find("strong", class_="tit")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        date_el = card.find("span", class_="date")
        date_text = date_el.get_text(strip=True) if date_el else ""

        detail_url = (
            f"https://www.kepco.co.kr/home/media/newsroom/pr/boardView.do"
            f"?boardMngNo={board_mng_no}&boardNo={board_no}"
        )
        items.append({
            "title": title,
            "link": detail_url,
            "date": parse_date_flexible(date_text),
            "date_raw": date_text,
        })
    log(f"[INFO] KEPCO: {len(items)} items parsed")
    return items or None


def fetch_aikorea():
    url = BOARD_URL["국가인공지능전략위원회"]
    resp = fetch(url, "AIKOREA")
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    # 'table-body' 아이디를 가진 tbody를 먼저 찾고, 없으면 클래스로 테이블을 찾음
    tbody = soup.find("tbody", id="table-body")
    if not tbody:
        table = soup.find("table", class_="board_list")
        if table:
            tbody = table.find("tbody")
    
    rows = tbody.find_all("tr") if tbody else []
    log(f"[INFO] AIKOREA: {len(rows)} candidate rows found")

    pattern = re.compile(r"goToDetail\('?(\d+)'?\)")
    items = []
    seen = set()
    for row in rows:
        if row.find("td", class_="board_null"):
            continue  # "등록된 게시물이 없습니다" 행 건너뛰기
            
        subject_td = row.find("td", class_="subject")
        if not subject_td:
            continue
            
        a = subject_td.find("a", href=True)
        if not a:
            continue
            
        # href 안의 javascript:goToDetail(숫자) 에서 숫자 추출
        m = pattern.search(a["href"])
        if not m:
            continue
            
        num = m.group(1)
        if num in seen:
            continue
        seen.add(num)

        title = a.get_text(strip=True)
        if not title:
            continue

        date_td = row.find("td", class_="date")
        date_text = extract_date_text(date_td.get_text(" ", strip=True)) if date_td else ""

        # 실제 상세페이지 URL 조합
        detail_url = f"https://www.aikorea.go.kr/web/board/brdDetail.do?menu_cd=000012&num={num}"
        
        items.append({
            "title": title,
            "link": detail_url,
            "date": parse_date_flexible(date_text),
            "date_raw": date_text,
        })
        
    log(f"[INFO] AIKOREA: {len(items)} items parsed")
    return items or None


def fetch_kwater():
    return fetch_generic_board(BOARD_URL["한국수자원공사"], "K-water")


def fetch_nipa():
    return fetch_generic_board(BOARD_URL["정보통신산업진흥원"], "NIPA")


def fetch_nia():
    """div.board_type01 안의 li 목록. 상세는 JS(doBbsFView)로 폼 제출하는 방식이라
    실제 View.do 상세 URL을 GET 파라미터로 구성해서 링크를 만든다."""
    url = BOARD_URL["한국지능정보사회진흥원"]
    resp = fetch(url, "NIA")
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    container = soup.find("div", class_="board_type01")
    rows = container.find_all("li") if container else []
    log(f"[INFO] NIA: {len(rows)} candidate rows found")

    pattern = re.compile(r"doBbsFView\('(\d+)','(\d+)','(\d+)','(\d+)'\)")
    items = []
    seen = set()
    for row in rows:
        a = row.find("a", href=True)
        if a is None:
            continue
        m = pattern.search(a.get("onclick", ""))
        if not m:
            continue
        cb_idx, bc_idx, _gbn, parent_seq = m.groups()
        key = (cb_idx, bc_idx)
        if key in seen:
            continue
        seen.add(key)

        subject = row.find("span", class_="subject")
        title = ""
        if subject:
            title = "".join(
                c for c in subject.contents if isinstance(c, NavigableString)
            ).strip()
        if not title:
            continue

        date_text = ""
        src = row.find("span", class_="src")
        if src:
            date_text = extract_date_text(src.get_text(" ", strip=True))

        detail_url = (
            f"https://nia.or.kr/site/nia_kor/ex/bbs/View.do"
            f"?cbIdx={cb_idx}&bcIdx={bc_idx}&parentSeq={parent_seq}"
        )
        items.append({
            "title": title,
            "link": detail_url,
            "date": parse_date_flexible(date_text),
            "date_raw": date_text,
        })
    log(f"[INFO] NIA: {len(items)} items parsed")
    return items or None


_TAG_RE = re.compile(r"<[^>]+>")


def clean_naver_text(text: str) -> str:
    return unescape(_TAG_RE.sub("", text or "")).strip()


def normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def find_region_in_title(title: str):
    for name in REGION_NAMES:
        if name in title:
            return name
    return None


def press_name_from_link(link: str) -> str:
    try:
        host = urlparse(link).netloc
    except ValueError:
        return ""
    host = re.sub(r"^www\.", "", host)
    for domain, name in PRESS_DOMAIN_MAP.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host


def fetch_naver_news_raw(query: str):
    """네이버 뉴스 검색 API 호출. 실패하면 None, 성공하면 items 리스트(빈 리스트 가능)."""
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        log("[ERROR] NAVER: NAVER_CLIENT_ID/NAVER_CLIENT_SECRET 환경변수가 설정되지 않았습니다")
        return None

    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }

    all_items = []
    for page in range(NAVER_MAX_PAGES):
        start = page * NAVER_DISPLAY + 1
        params = {"query": query, "display": NAVER_DISPLAY, "start": start, "sort": "date"}

        data = None
        last_err = None
        for attempt in range(1, RETRIES + 1):
            try:
                resp = requests.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                break
            except requests.RequestException as e:
                last_err = e
                log(f"[WARN] NAVER[{query}] page{page + 1} attempt {attempt} failed: {e}")
                if attempt < RETRIES:
                    time.sleep(RETRY_WAIT_SECONDS)

        if data is None:
            log(f"[ERROR] NAVER[{query}] page{page + 1}: all attempts failed ({last_err})")
            return all_items if all_items else None

        items = data.get("items", [])
        all_items.extend(items)
        if len(items) < NAVER_DISPLAY:
            break

    log(f"[INFO] NAVER[{query}]: {len(all_items)} raw items fetched")
    return all_items


def fetch_aidc_news():
    """'AI데이터센터'/'AIDC' + 지자체 지역명이 제목에 함께 언급된 기사 상위 20건.
    반환값: (items, ok). ok=False면 API 호출 자체가 실패한 것(진짜로 0건인 것과 구분)."""
    seen_links = set()
    candidates = []
    any_ok = False

    for query in NAVER_NEWS_QUERIES:
        raw_items = fetch_naver_news_raw(query)
        if raw_items is None:
            continue
        any_ok = True

        for it in raw_items:
            link = it.get("originallink") or it.get("link") or ""
            if not link or link in seen_links:
                continue
            seen_links.add(link)

            title = clean_naver_text(it.get("title", ""))
            desc = clean_naver_text(it.get("description", ""))
            if not title:
                continue

            combined_norm = normalize_for_match(title + " " + desc)
            if ("ai데이터센터" not in combined_norm
                    and "aidc" not in combined_norm
                    and "ai팩토리" not in combined_norm):
                continue

            region = find_region_in_title(title)
            if not region:
                continue

            pub_dt = None
            try:
                pub_dt = parsedate_to_datetime(it.get("pubDate", ""))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=KST)
                pub_dt = pub_dt.astimezone(KST)
            except (TypeError, ValueError):
                pub_dt = None

            display_link = it.get("link") or link
            candidates.append({
                "title": title,
                "summary": desc,
                "link": display_link,
                "press": press_name_from_link(link),
                "pub_dt": pub_dt,
                "region": region,
            })

    if not any_ok:
        log("[SUMMARY] AIDC: FETCH FAILED")
        return [], False

    candidates.sort(key=lambda x: x["pub_dt"] or datetime.min.replace(tzinfo=KST), reverse=True)
    top = candidates[:AIDC_TOP_N]
    log(f"[SUMMARY] AIDC: {len(candidates)}건 매칭, 상위 {len(top)}건 표시")
    return top, True


def fetch_listed_company_news():
    """'AI데이터센터'/'AIDC' + 국내 기업명이 함께 언급된 기사 상위 20건.
    반환값: (items, ok). ok=False면 API 호출 자체가 실패한 것(진짜로 0건인 것과 구분)."""
    seen_links = set()
    candidates = []
    any_ok = False

    for query in NAVER_NEWS_QUERIES:
        raw_items = fetch_naver_news_raw(query)
        if raw_items is None:
            continue
        any_ok = True

        for it in raw_items:
            link = it.get("originallink") or it.get("link") or ""
            if not link or link in seen_links:
                continue
            seen_links.add(link)

            title = clean_naver_text(it.get("title", ""))
            desc = clean_naver_text(it.get("description", ""))
            if not title:
                continue

            combined_norm = normalize_for_match(title + " " + desc)
            if ("ai데이터센터" not in combined_norm
                    and "aidc" not in combined_norm
                    and "ai팩토리" not in combined_norm):
                continue

            company = find_company_in_text(title) or find_company_in_text(desc)
            if not company:
                continue

            pub_dt = None
            try:
                pub_dt = parsedate_to_datetime(it.get("pubDate", ""))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=KST)
                pub_dt = pub_dt.astimezone(KST)
            except (TypeError, ValueError):
                pub_dt = None

            display_link = it.get("link") or link
            candidates.append({
                "title": title,
                "summary": desc,
                "link": display_link,
                "press": press_name_from_link(link),
                "pub_dt": pub_dt,
                "company": company,
            })

    if not any_ok:
        log("[SUMMARY] LISTED: FETCH FAILED")
        return [], False

    candidates.sort(key=lambda x: x["pub_dt"] or datetime.min.replace(tzinfo=KST), reverse=True)
    top = candidates[:COMPANY_TOP_N]
    log(f"[SUMMARY] LISTED: {len(candidates)}건 매칭, 상위 {len(top)}건 표시")
    return top, True


FETCHERS = {
    "과학기술정보통신부": fetch_msit,
    "기후에너지환경부": fetch_mcee,
    "산업통상부": fetch_motir,
    "국가인공지능전략위원회": fetch_aikorea,
    "한국전력공사": fetch_kepco,
    "한국수자원공사": fetch_kwater,
    "정보통신산업진흥원": fetch_nipa,
    "한국지능정보사회진흥원": fetch_nia,
}


def render_html(today_items: dict, recent_items: dict, fetch_failed: set,
                 aidc_items: list, aidc_ok: bool,
                 listed_items: list, listed_ok: bool,
                 overseas_items: list, overseas_ok: bool) -> str:
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

    def news_li(item: dict, tag_key: str) -> str:
        pub_label = item["pub_dt"].strftime("%Y-%m-%d %H:%M") if item.get("pub_dt") else "-"
        return f"""
      <li class="news-item">
        <a class="news-title" href="{escape(item['link'])}" target="_blank" rel="noopener">{escape(item['title'])}</a>
        <p class="news-summary">{escape(item['summary'])}</p>
        <p class="news-meta">
          <span class="press">{escape(item['press'])}</span> ·
          <span class="pubdate">{escape(pub_label)}</span> ·
          <span class="region-tag">{escape(item[tag_key])}</span>
        </p>
      </li>"""

    def aidc_panel() -> str:
        if not aidc_ok:
            body = '<p class="msg fail">뉴스 검색에 실패하여 확인하지 못했습니다. 다음 자동 실행에서 다시 시도합니다.</p>'
        elif aidc_items:
            body = "<ul class=\"news-list\">" + "".join(news_li(it, "region") for it in aidc_items) + "</ul>"
        else:
            body = '<p class="msg empty">조건에 맞는 기사가 없습니다</p>'
        return f"""
    <section class="agency">
      <h2>AI데이터센터(AIDC) · 지자체 언급 기사</h2>
      <p class="section-desc">네이버 뉴스에서 "AI데이터센터"/"AIDC"와 국내 지역명이 함께 언급된 기사 중 최신 {AIDC_TOP_N}건</p>
      {body}
    </section>"""

    def listed_panel() -> str:
        if not listed_ok:
            body = '<p class="msg fail">뉴스 검색에 실패하여 확인하지 못했습니다. 다음 자동 실행에서 다시 시도합니다.</p>'
        elif listed_items:
            body = "<ul class=\"news-list\">" + "".join(news_li(it, "company") for it in listed_items) + "</ul>"
        else:
            body = '<p class="msg empty">조건에 맞는 기사가 없습니다</p>'
        return f"""
    <section class="agency">
      <h2>AI데이터센터(AIDC) · 국내 기업 언급 기사</h2>
      <p class="section-desc">네이버 뉴스에서 "AI데이터센터"/"AIDC"와 국내 기업명이 함께 언급된 기사 중 최신 {COMPANY_TOP_N}건</p>
      {body}
    </section>"""

    def overseas_panel() -> str:
        if not overseas_ok:
            body = '<p class="msg fail">뉴스 검색에 실패하여 확인하지 못했습니다. 다음 자동 실행에서 다시 시도합니다.</p>'
        elif overseas_items:
            body = "<ul class=\"news-list\">" + "".join(news_li(it, "company") for it in overseas_items) + "</ul>"
        else:
            body = '<p class="msg empty">조건에 맞는 기사가 없습니다</p>'
        return f"""
    <section class="agency">
      <h2>AI데이터센터(AIDC) · 해외 기업 언급 기사</h2>
      <p class="section-desc">네이버 뉴스에서 "AI데이터센터"/"AI DATA CENTER"/"AIDC"/"AI팩토리"/"AI FACTORY"와 해외 기업명이 함께 언급된 기사 중 최신 {OVERSEAS_TOP_N}건</p>
      {body}
    </section>"""

    def highlights_html() -> str:
        rows = []
        for org in GOV_AGENCIES:
            for it in today_items.get(org, []):
                rows.append(("정부기관", "cat-gov", org, it))
        for org in PUBLIC_ENTERPRISES:
            for it in today_items.get(org, []):
                rows.append(("공기업", "cat-public", org, it))

        if not rows:
            body = '<p class="msg empty">오늘 등록된 자료가 아직 없습니다.</p>'
        else:
            lis = []
            for cat_label, cat_class, sub_label, it in rows:
                lis.append(f"""
      <li class="highlight-item">
        <span class="cat-badge {cat_class}">{escape(cat_label)}</span>
        <a href="{escape(it['link'])}" target="_blank" rel="noopener">{escape(it['title'])}</a>
        <span class="highlight-sub">{escape(sub_label)}</span>
      </li>""")
            body = "<ul class=\"highlight-list\">" + "".join(lis) + "</ul>"

        return f"""
  <section class="highlights">
    <h2>오늘 올라온 자료들</h2>
    {body}
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

    tab_buttons.append(
        '<button class="tab-btn" data-tab="local" onclick="showTab(\'local\')">지자체 관련 기사</button>'
    )
    tab_panels.append(f'<div id="tab-local" class="tab-panel">{aidc_panel()}\n    </div>')

    tab_buttons.append(
        '<button class="tab-btn" data-tab="listed" onclick="showTab(\'listed\')">국내 기업 관련 기사</button>'
    )
    tab_panels.append(f'<div id="tab-listed" class="tab-panel">{listed_panel()}\n    </div>')

    tab_buttons.append(
        '<button class="tab-btn" data-tab="overseas" onclick="showTab(\'overseas\')">해외 기업 관련 기사</button>'
    )
    tab_panels.append(f'<div id="tab-overseas" class="tab-panel">{overseas_panel()}\n    </div>')

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
          max-width: 1080px; margin: 0 auto; padding: 32px 16px 60px;
          background: #f7f7f5; color: #222; }}
  header h1 {{ font-size: 22px; margin-bottom: 4px; }}
  header p {{ color: #666; font-size: 14px; margin-top: 0; }}
  .layout {{ display: flex; align-items: flex-start; gap: 20px; margin-top: 20px; }}
  .sidebar {{ width: 260px; flex-shrink: 0; position: sticky; top: 20px; height: max-content; }}
  .main {{ flex: 1; min-width: 0; }}
  section.highlights {{
    background: #fff;
    border: 1px solid #e3e2dc;
    border-radius: 10px;
    padding: 4px 16px 16px;
    max-height: calc(100vh - 40px);
    overflow-y: auto;
  }}
  ul.highlight-list {{ display: block; }}
  li.highlight-item {{ display: block; padding: 10px 0; border-top: 1px solid #eee; }}
  li.highlight-item:first-child {{ border-top: none; }}
  li.highlight-item a {{ display: block; color: #222; text-decoration: none; font-size: 13px;
                          line-height: 1.4; margin: 4px 0 2px; }}
  li.highlight-item a:hover {{ text-decoration: underline; color: #185fa5; }}
  @media (max-width: 760px) {{
    .layout {{ flex-direction: column; }}
    .sidebar {{ display: none; }}
  }}
  .cat-badge {{ font-size: 11px; font-weight: 700; color: #fff; padding: 2px 8px;
                border-radius: 10px; white-space: nowrap; }}
  .cat-badge.cat-gov {{ background: #185fa5; }}
  .cat-badge.cat-public {{ background: #1f8a4c; }}
  .cat-badge.cat-local {{ background: #c2703d; }}
  .cat-badge.cat-listed {{ background: #7b4fa6; }}
  .cat-badge.cat-overseas {{ background: #0f8a8a; }}
  .highlight-sub {{ font-size: 12px; color: #888; }}
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
  .section-desc {{ font-size: 12px; color: #888; margin: -6px 0 12px; }}
  ul.news-list {{ display: block; }}
  li.news-item {{ display: block; padding: 14px 0; }}
  li.news-item .news-title {{ display: block; font-size: 15px; font-weight: 600; color: #185fa5; }}
  li.news-item .news-title:hover {{ text-decoration: underline; }}
  li.news-item .news-summary {{ font-size: 13px; color: #555; margin: 6px 0; line-height: 1.5; }}
  li.news-item .news-meta {{ font-size: 12px; color: #888; margin: 0; }}
  li.news-item .region-tag {{ color: #185fa5; font-weight: 600; }}
  footer {{ color: #999; font-size: 12px; text-align: center; margin-top: 32px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #17181a; color: #e8e8e6; }}
    .tab-btn {{ background: #232527; border-color: #33353a; color: #ccc; }}
    section.agency {{ background: #232527; border-color: #33353a; }}
    li {{ border-top-color: #33353a; }}
    li a {{ color: #e8e8e6; }}
    li .date {{ color: #999; }}
    .msg.empty {{ color: #9a9a9a; }}
    li.news-item .news-summary {{ color: #aaa; }}
    li.news-item .news-meta {{ color: #999; }}
    section.highlights {{ background: #232527; border-color: #33353a; }}
    li.highlight-item {{ border-top-color: #33353a; }}
    li.highlight-item a {{ color: #e8e8e6; }}
    .highlight-sub {{ color: #999; }}
  }}
</style>
</head>
<body>
  <header>
    <h1>정부기관·공기업 오늘의 보도자료</h1>
    <p>{TODAY_LABEL} 기준 · 마지막 업데이트: {GENERATED_AT_LABEL} (KST) · 매일 자동 업데이트(하루 6회)</p>
  </header>
  <div class="layout">
    <aside class="sidebar">
      {highlights_html()}
    </aside>
    <div class="main">
      <div class="tab-banner">
        {tab_buttons_html}
      </div>
      {tab_panels_html}
    </div>
  </div>
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

    aidc_items, aidc_ok = fetch_aidc_news()
    listed_items, listed_ok = fetch_listed_company_news()
    overseas_items, overseas_ok = fetch_overseas_company_news()

    html = render_html(today_items, recent_items, fetch_failed,
                        aidc_items, aidc_ok, listed_items, listed_ok,
                        overseas_items, overseas_ok)

    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    log("[INFO] public/index.html written")


if __name__ == "__main__":
    main()
