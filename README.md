# 정부기관 오늘의 보도자료

과학기술정보통신부, 기후에너지환경부, 산업통상부 보도자료 게시판에서
오늘 등록된 글의 제목과 링크만 모아 보여주는 페이지입니다.

- `scraper.py`: 세 기관 게시판에서 오늘 날짜 글을 수집해 `public/index.html`을 생성합니다.
- `.github/workflows/update.yml`: 하루 여러 차례(00/03/06/09/12/15시 UTC, 즉 KST 09/12/15/18/21/24시) 자동 실행되어
  `public/index.html`을 갱신하고 커밋 후 GitHub Pages로 배포합니다. Actions 탭에서 수동 실행(`workflow_dispatch`)도 가능합니다.

## 배포 주소

리포지토리 Settings → Pages에서 처음 한 번 "Source: GitHub Actions"로 표시되면,
`https://<계정>.github.io/<리포지토리>/` 주소로 접속할 수 있습니다.
