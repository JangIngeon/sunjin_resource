# 정부기관 AI 데이터센터 보도자료 모니터

과기정통부 · 산업통상부 · 기후에너지환경부 보도자료를 매일 자동으로 수집해서
"데이터센터/AI" 관련 소식만 걸러 보여주는 정적 웹페이지입니다.
GitHub Actions가 매일 새벽 실행되어 `index.html`을 새로 만들고 자동으로 커밋·푸시합니다.

## 사용하는 소스
- 과학기술정보통신부: 공식 RSS (`msit.go.kr/user/rss/rss.do?bbsSeqNo=94`)
- 기후에너지환경부: 공식 RSS (`me.go.kr/home/web/board/rss.do?menuId=286&boardMasterId=1`)
- 산업통상부: 보도·참고자료 게시판 스크래핑 (`motir.go.kr/kor/article/ATCL3f49a5a8c`)

## 설정 방법 (최초 1회)

### 1. GitHub에 새 저장소 만들기
1. github.com에서 새 저장소 생성 (Public 권장 — Pages 무료 사용을 위해)
2. 이 폴더의 모든 파일을 그대로 저장소에 push
   ```bash
   git init
   git add .
   git commit -m "init: gov datacenter tracker"
   git branch -M main
   git remote add origin https://github.com/{본인아이디}/{저장소이름}.git
   git push -u origin main
   ```

### 2. Actions 쓰기 권한 켜기
저장소 → **Settings → Actions → General → Workflow permissions**에서
**"Read and write permissions"** 선택 후 저장.
(이게 꺼져 있으면 Actions가 커밋을 못 올립니다.)

### 3. GitHub Pages 켜기
저장소 → **Settings → Pages**
- Source: `Deploy from a branch`
- Branch: `main` / `/(root)`
- 저장하면 몇 분 뒤 `https://{본인아이디}.github.io/{저장소이름}/` 로 접속 가능

### 4. 첫 실행
저장소 → **Actions** 탭 → `Daily Government Press Release Update` 선택 →
**Run workflow** 버튼으로 한 번 수동 실행.
성공하면 `index.html`이 실제 데이터로 교체되고, 그 이후부터는
매일 한국시간 오전 8시에 자동으로 실행됩니다.

## 동작 원리
- `scripts/scraper.py`가 3개 기관에서 최신 글을 가져와 제목/요약에
  `KEYWORDS`(데이터센터, 인공지능, AI, GPU, AX, 클라우드)가 포함된 것만 남깁니다.
- `data/history.json`에 이미 본 글의 ID를 저장해 중복 노출을 막습니다.
  (내일 실행에서는 오늘 이미 본 글은 "신규"로 다시 뜨지 않습니다.)
- 매 실행마다 `index.html`을 새로 생성하고, 변경이 있을 때만 커밋합니다.

## 커스터마이징
- **키워드 변경**: `scripts/scraper.py` 상단의 `KEYWORDS` 리스트 수정
- **실행 시각 변경**: `.github/workflows/update.yml`의 `cron` 값 수정
  (cron은 UTC 기준이며, KST = UTC+9)
- **기관 추가**: `fetch_msit`, `fetch_mcee`, `fetch_motir`와 같은 형태로
  함수를 추가하고 `main()`의 `for fetcher in (...)` 튜플에 넣으면 됩니다.

## 알아둘 점
- 정부기관 사이트의 HTML 구조가 개편되면 스크래핑 정규식이 깨질 수 있습니다.
  이 경우 Actions 탭의 실행 로그(`[WARN]`, `[ERROR]` 메시지)에서 어느 소스가
  실패했는지 확인할 수 있습니다. 다른 소스는 정상적으로 계속 동작합니다.
- 산업통상부는 RSS가 없어 목록 페이지를 직접 파싱합니다. 페이지 구조가
  바뀌면 `fetch_motir()`의 정규식을 손봐야 할 수 있습니다.
