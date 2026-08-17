# 기업분석 비공개 베타

회사 이름과 채용공고를 입력하면 공시·뉴스·회사 홈페이지에서 근거를 모아
기업분석 보고서를 만드는 웹서비스입니다.

첫 공개는 다음 안전 설정으로 시작합니다.

- 관리자 이메일로 로그인한 사람만 접속
- `PIPELINE=demo`로 실제 AI 조사 비용 차단
- Render 1개 인스턴스와 SQLite 영속 디스크 사용
- 보고서 품질 확인 후에만 실제 조사 모드로 전환

## 주요 폴더

- `app/src`: 웹사이트와 보고서 기능
- `app/tools`: SQLite 백업·복구 도구
- `prototype_v1/src`: 실제 조사 엔진의 기능 모듈
- `prototype_v1/data/pilot`: 첫 배포 데모가 읽는 최소 자료
- `render.yaml`, `app/Dockerfile`: Render 배포 설정

기획 과정, 옛 실험, 검수 캡처, 로컬 DB·로그·비밀키는 Git에서 관리하지 않습니다.

## 로컬 실행과 시험

```powershell
cd app
pip install -r requirements.txt
python -m uvicorn src.web.main:app --port 8000
python -m pytest src -q
```

실제 개인 이메일과 API 키는 코드에 넣지 않고 환경변수로만 설정합니다.

## 배포·운영 안내

- `app/docs/Render_배포.md`
- `app/docs/장기_휴면_백업.md`
- `app/docs/구글로그인_설정.md`
- `app/docs/노션_설정.md`
