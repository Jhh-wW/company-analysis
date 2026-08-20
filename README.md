# 기업분석 비공개 베타

회사 이름과 선택적인 주소 힌트를 입력하면 공시·회사 공식 웹에서 핵심 근거를 모으고
외부 자료로 과장 여부를 내부 점검해, 기업이 취업준비생에게 알려야 할 회사 공통
사실을 설명하는 분석 보고서를 만드는 FastAPI 웹서비스입니다. 지원 직무·채용공고·지원자 경험은 받지
않으며 자기소개서·면접 답안이나 일반 준비 질문을 만들지 않습니다.

기본 배포 설정은 **관리자 전용 데모**입니다. 보고서 품질을 확인한 뒤에만 실제
조사 모드와 허용 사용자를 단계적으로 엽니다.

## 작동 흐름

```text
회사 이름·주소 입력
        ↓
회사 식별과 분석 대상 판정
        ↓
DART·회사 공식 웹 수집 + 외부 자료 내부 교차검증
        ↓
사실 원장 생성·단일 소유·과거/현재/미래·경쟁사 양쪽 근거 검사
        ↓
정본 출고 게이트 → 웹·PDF·Notion 동등 출력
```

## 기본 안전 설정

- `BETA_ADMIN_ONLY=1`: 관리자 이메일로 로그인한 사람만 접속
- `PIPELINE=demo`: 첫 배포에서는 외부 AI 조사 비용 차단
- GitHub Actions의 시험과 Docker 상태 확인이 통과된 커밋만 Render가 배포
- Render 인스턴스와 SQLite 쓰기 프로세스를 각각 1개로 고정
- API 키·로그인 비밀값은 파일이 아닌 Render 환경변수로만 설정
- 새 분석 입력은 회사 이름과 주소만 받으며 직무·공고·이미지 원본을 받지 않음
- `Report.schema_version=company-report-v3-canonical`만 신규 출고 정본으로 인정하고 같은 버전의 캐시 토큰만 재사용
- PDF는 사용자 다운로드 정본이며 구조 검사와 모든 페이지 시각 QA를 통과해야 함

## 저장소 구조

```text
company-analysis-beta/
├── .github/                 # 자동 시험·Docker 확인·의존성 업데이트
├── app/                     # 사용자가 접하는 운영 웹서비스
│   ├── src/
│   │   ├── features/        # 기능별 로직·상수·시험
│   │   ├── core/            # 여러 기능이 함께 쓰는 얇은 기반
│   │   └── web/             # FastAPI 조립점·화면·통합 시험
│   ├── tools/               # SQLite 백업·복구 도구
│   ├── docs/                # 로그인·배포·운영 안내
│   ├── Dockerfile
│   └── requirements.txt
├── analysis_engine/         # 실제 조사 엔진과 데모용 최소 자료
│   ├── src/
│   │   ├── features/        # 식별·판정·개인정보·최종검증 기능
│   │   └── core/            # DART·네이버·실행경로·사용량 기반
│   ├── data/pilot/          # 비용 없이 재생하는 데모 자료
│   └── tools/               # 실제 조사 엔진 진입점
├── docs/
│   ├── 출력물 기준/         # 목차별 작성·근거·PDF·런타임 출고 정본
│   ├── architecture/        # 기능 지도
│   └── adr/                 # 구조를 선택한 이유
├── render.yaml              # Render 관리자 베타 설정
├── .gitignore               # 비밀값·DB·로그·작업 산출물 제외
└── .dockerignore            # 실행에 필요한 파일만 이미지에 포함
```

`analysis_engine`은 현재 실제 조사 모드가 사용하는 필수 엔진입니다. 구조를 정리할
때도 삭제하거나 데모 자료로 대체하지 않습니다.

### 이 작업 폴더에만 있는 로컬 검수 자료

아래 폴더는 제품 소스가 아니라 조사·브라우저 검수의 로컬 증거다. 코드를 읽을 때는
`app/`, `analysis_engine/`, `docs/`를 먼저 보고, 아래 자료는 근거 재확인이 필요할 때만
연다.

- `research/`: 50개 참고 보고서 원문 코퍼스
- `tmp/`: 이번 canonical 표본을 대조한 공시·IR·회사 웹 원문 스냅샷
- `.playwright-mcp/`: 과거 브라우저·접근성 검수 기록
- `app/.local_*`: 로컬 데모·평가 실행 기록과 DB

이들은 Git 비추적 자료이며 자동으로 삭제하지 않는다. `analysis_engine/.env`는 실제
비밀 설정일 수 있으므로 검수자가 열거나 복사하지 않는다.

이 저장소는 기능 단위 구조를 사용합니다. 기능의 로직·상수·시험을 같은 폴더에
두고, `web`과 `pipeline`이 기능들을 조립합니다.

- [기능별 책임 지도](docs/architecture/feature-map.md)
- [시스템·배포·신뢰 경계](docs/architecture/system-overview.md)
- [회사분석 보고서 구조](docs/REPORT_STRUCTURE.md)
- [출력물 기준](docs/출력물%20기준/README.md)
- [런타임 출고 계약](docs/출력물%20기준/90_공통_규칙/런타임_출고_계약.md)
- [ADR 0001 — 기능 중심 구조를 점진적으로 적용한다](docs/adr/0001-feature-oriented-structure.md)
- [웹서비스 코드 안내](app/README.md)
- [전체 문서 지도](docs/README.md)
- [개발·기획 검수 안내](docs/REVIEW_GUIDE.md)
- [기여·검증 방법](CONTRIBUTING.md)

## 로컬 데모 실행

Python 3.13을 사용합니다. 처음 한 번만 환경을 준비한 뒤 로컬 데모 전용 실행기를
사용합니다.

```powershell
cd app
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\로컬데모켜기.ps1
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다. 다른 포트는
`.\로컬데모켜기.ps1 -Port 8010`처럼 지정할 수 있습니다. 이 실행기는 외부 API를
끄고, 서버를 내 컴퓨터에서만 열며, 데모 기록을 `app/.local_demo/`에 격리합니다.
관리자 기능은 실행 창에 표시된 **로컬 전용 관리 주소**를 같은 컴퓨터의 브라우저에서
여세요. 일반 로그인 화면에는 로컬 관리자 입구가 없습니다. 로그아웃한 뒤 다시
들어갈 때도 같은 실행 창의 주소를 다시 열면 됩니다. 이 주소는 화면 공유·문서·메신저·
로그에 남기지 말고, 실행을 종료하면 폐기하세요.

## 시험

```powershell
# app 폴더에서 웹서비스와 백업 도구 시험
$env:TLDEXTRACT_CACHE="$PWD\.cache\tldextract"
.\.venv\Scripts\python -m pytest src tools/tests -q `
  --basetemp=.pytest_tmp_readme_app

# 저장소 루트에서 조사 엔진 시험
cd ..
.\app\.venv\Scripts\python -m pytest analysis_engine/src -q `
  --basetemp=app/.pytest_tmp_readme_engine
```

GitHub Actions는 위 시험에 더해 실제 Docker 이미지를 만들고 `/healthz` 응답까지
확인합니다. 신규 보고서 변경은 정본 목차, 서비스 범위, 사실 단일 소유,
`#과거·#현재·#미래`, 경쟁사 양쪽 근거와 PDF 구조·시각 QA 회귀 조건도 통과해야
출고 가능한 변경으로 인정합니다.

## 배포·운영 문서

- [Render 배포 순서](app/docs/Render_배포.md)
- [장기 휴면 백업·복구](app/docs/장기_휴면_백업.md)
- [Google 로그인 설정](app/docs/구글로그인_설정.md)
- [Notion 전송 설정](app/docs/노션_설정.md)

## Git에 넣지 않는 자료

실제 `.env`, API 키, OAuth 비밀값, SQLite DB, 실행 로그, 백업, 이미지·문서 산출물은
Git에서 관리하지 않습니다. 새 비밀값은 코드나 문서에 쓰지 않고 환경변수 또는
비밀번호 관리자에 보관합니다.
