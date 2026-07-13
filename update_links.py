import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# 1. 오늘 날짜 정의 (사이트마다 포맷이 다를 수 있음)
today_str_dash = datetime.today().strftime('%Y-%m-%d') # 2026-07-13
today_str_dot = datetime.today().strftime('%Y.%m.%d')  # 2026.07.13

urls = {
    "과학기술정보통신부": "https://www.msit.go.kr/bbs/list.do?sCode=user&mPid=208&mId=307",
    "중소기업중앙회(MCEE)": "https://www.mcee.go.kr/home/web/index.do?menuId=10598",
    "광물자원연구소(MOTIR)": "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c"
}

collected_data = {}

# 각 사이트별 크롤링 로직 (사이트 구조에 맞게 세부 조정 필요)
for name, url in urls.items():
    collected_data[name] = []
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # [예시 로직] 실제 사이트의 table tr 이나 list item 구조를 분석해야 합니다.
            # 날짜를 확인하고 오늘 날짜와 일치하면 리스트에 추가
            # items = soup.select('table.list tbody tr') ...
            
            # 임시 가상 데이터 (매칭 성공 시)
            # collected_data[name].append({"title": "오늘 올라온 공지사항 제목", "link": "상세페이지URL"})
            pass
    except Exception as e:
        print(f"{name} 크롤링 실패: {e}")

# 2. HTML 파일 생성
html_content = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>당일 등록 게시물 모아보기</title>
    <style>
        body {{ font-family: sans-serif; line-height: 1.6; padding: 20px; max-width: 800px; margin: 0 auto; background: #f9f9f9; }}
        h1 {{ color: #333; border-bottom: 2px solid #333; padding-bottom: 10px; }}
        .site-section {{ background: white; padding: 15px; margin-bottom: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
        .site-title {{ font-size: 1.2em; color: #0066cc; margin-top: 0; }}
        ul {{ padding-left: 20px; }}
        li {{ margin-bottom: 8px; }}
        a {{ color: #333; text-decoration: none; }}
        a:hover {{ text-decoration: underline; color: #0066cc; }}
        .no-data {{ color: #999; font-style: italic; }}
        .time {{ font-size: 0.85em; color: #666; text-align: right; }}
    </style>
</head>
<body>
    <h1>📅 당일 등록 게시물 요약</h1>
    <p class="time">최근 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
"""

for name, items in collected_data.items():
    html_content += f"""
    <div class="site-section">
        <div class="site-title">{name}</div>
        <ul>
    """
    if not items:
        html_content += '<li class="no-data">오늘 등록된 새 게시물이 없습니다.</li>'
    else:
        for item in items:
            html_content += f'<li><a href="{item["link"]}" target="_blank">{item["title"]}</a></li>'
            
    html_content += """
        </ul>
    </div>
    """

html_content += """
</body>
</html>
"""

# index.html로 저장
with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_content)
