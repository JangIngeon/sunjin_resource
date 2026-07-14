import requests
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

urls = {
    "kepco.html": "https://www.kepco.co.kr/home/media/newsroom/pr/boardList.do",
    "nia.html": "https://nia.or.kr/site/nia_kor/ex/bbs/List.do?cbIdx=90549",
}
for filename, url in urls.items():
    r = requests.get(url, headers=headers, timeout=20)
    r.encoding = r.apparent_encoding
    with open(filename, "w", encoding="utf-8") as f:
        f.write(r.text)
    print(filename, len(r.text), "chars saved")
