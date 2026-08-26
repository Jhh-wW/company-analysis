# 기업분석 비공개 베타

회사 이름과 선택적인 주소 힌트를 입력하면 공시·회사 공식 웹에서 핵심 근거를 모으고
외부 자료로 과장 여부를 내부 점검해, 기업이 취업준비생에게 알려야 할 회사 공통
사실을 설명하는 분석 보고서를 만드는 FastAPI 웹서비스입니다. 지원 직무·채용공고·지원자 경험은 받지
않으며 자기소개서·면접 답안이나 일반 준비 질문을 만들지 않습니다.

기본 배포 설정은 **관리자 전용 베타**입니다. 보고서 품질을 확인한 뒤에만 허용 사용자를
단계적으로 엽니다.

## 지금 상태 (2026-08-26)

| | |
|---|---|
| 배포 | ✅ 운영 중 — https://company-analysis-beta.onrender.com (Render · Docker · Standard) |
| 접근 | 관리자 로그인 필요 (`BETA_ADMIN_ONLY=1`) |
| 조사 엔진 | **엔진 v2**(`company-report-v2-composer`)가 본선 |
| 자동 시험 | 4,181건 통과 / 0 실패 |
| 배포 방식 | ⛔ **수동 배포만** — 커밋을 올려도 자동으로 반영되지 않는다 (`render.yaml`의 `autoDeployTrigger: off`) |

배포본이 최신인지 확인하는 방법:

```bash
curl -s https://company-analysis-beta.onrender.com/healthz
# {"status":"ok","commit":"<7자리>"} 가 `git rev-parse --short=7 HEAD` 와 같으면 최신
```

## 작동 흐름

```text
회사 이름·주소 입력
        ↓
회사 식별과 분석 대상 판정
        ↓
DART·회사 공식 웹 기반 공식 근거 수집
        ↓
사실 원장 생성·단일 소유·과거/현재/미래·경쟁사 양쪽 근거 검사
        ↓
정본 출고 게이트 → 웹·PDF·Notion 동등 출력
```

## 기본 안전 설정

- `BETA_ADMIN_ONLY=1`: 관리자 이메일로 로그인한 사람만 접속
- `PIPELINE`: **코드 기본값은 `demo`**(외부 AI 조사 비용 0). 운영 배포에서만
  `render.yaml`이 `real`로 올린다 — 즉 «로컬에서 실수로 돈이 나가는 일»이 없다
- ⚠️ **자동 시험(GitHub Actions)이 지금은 안 돕니다.**
  워크플로가 `push: branches: [master]`에만 반응하는데 **작업·배포 브랜치는 `engine-v2`**입니다
  (2026-08-27 실측: `engine-v2`에서 quality-gate 실행 **0회**).
  실제 안전장치는 **로컬 전체 시험 + 사람이 누르는 수동 배포** 두 가지입니다.
  → 이 구멍을 어떻게 할지는 [20장 §5](docs/실행계획_엔진v2/20_저장소_정리_2026-08-27.md) 참고
- Render 인스턴스와 SQLite 쓰기 프로세스를 각각 1개로 고정
- API 키·로그인 비밀값은 파일이 아닌 Render 환경변수로만 설정
- 새 분석 입력은 회사 이름과 주소만 받으며 직무·공고·이미지 원본을 받지 않음
- 출고 정본 스키마는 두 가지다 — **엔진 v2(현재 본선)는 `company-report-v2-composer`**,
  v1 경로는 `company-report-v4-canonical`. 캐시 토큰은 같은 버전끼리만 재사용한다
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
│   ├── tools/               # 인증된 백업·정기 작업 호출과 복구 도구
│   ├── docs/                # 로그인·배포·운영 안내
│   ├── Dockerfile
│   └── requirements.txt
├── analysis_engine/         # 실제 조사 엔진과 데모용 최소 자료
│   ├── src/
│   │   ├── features/        # 식별·판정·개인정보·최종검증 기능
│   │   └── core/            # DART·네이버·실행경로·사용량 기반
│   ├── data/pilot/          # 비용 없이 재생하는 데모 자료
│   └── tools/               # 실제 조사 엔진 진입점
├── deploy/                  # 클라우드 중립 릴리스 패키지와 배포 검증
├── ops/                     # 배포·운영 절차 문서
├── scripts/                 # 배포 보조 스크립트
├── docs/
│   ├── 출력물 기준/         # 목차별 작성·근거·PDF·런타임 출고 정본 (최상위 정본)
│   ├── 실행계획_엔진v2/     # 엔진 v2 전환 기록·인수인계 (가장 최근 작업)
│   ├── architecture/        # 기능 지도
│   ├── adr/                 # 구조를 선택한 이유
│   ├── evidence/            # 목차 설계의 조사 근거 (문서 50개 분석)
│   ├── research/            # 설계 당시 조사 자료 (현재 규범이 아님)
│   ├── 골든샘플/            # 품질 하한 비교용 기준 산출물
│   ├── 관리대시보드/        # 운영 대시보드 설계
│   └── reviews/             # 날짜별 검토 스냅샷 (현재 판정이 아님)
├── render.yaml              # Render 관리자 베타 설정
├── pytest.ini               # 시험 수집 규칙
├── CONTRIBUTING.md          # 기여·검증 방법
├── SECURITY.md              # 취약점 신고 절차
├── .gitignore               # 비밀값·DB·로그·작업 산출물 제외 (허용 목록 방식)
└── .dockerignore            # 실행에 필요한 파일만 이미지에 포함
```

`analysis_engine`은 현재 실제 조사 모드가 사용하는 필수 엔진입니다. 구조를 정리할
때도 삭제하거나 데모 자료로 대체하지 않습니다.

⚠️ **정기 작업(cron)은 아직 선언돼 있지 않습니다.** `render.yaml`에는 웹 서비스 1개만
있습니다(2026-08-27 실측: `type: cron` 0건). 외부 SQLite 백업·주간 관리자 XLSX·휴지통
정리는 **코드와 인증 경로는 준비돼 있으나 스케줄이 아직 안 붙었습니다.**
Render 배포 자체는 운영 중이고, 외부 S3 백업 연결도 아직 돌려 보지 않았습니다.

### 이 작업 폴더에만 있는 로컬 검수 자료

아래 폴더는 제품 소스가 아니라 조사·브라우저 검수의 로컬 증거다. 코드를 읽을 때는
`app/`, `analysis_engine/`, `docs/`를 먼저 보고, 아래 자료는 근거 재확인이 필요할 때만
연다.

- `research/`: 50개 참고 보고서 원문 코퍼스
- `tmp/`: 이번 canonical 표본을 대조한 공시·IR·회사 웹 원문 스냅샷
- `.playwright-mcp/`: 과거 브라우저·접근성 검수 기록
- `.local-artifacts/`: 재생성 가능한 시험 결과·캐시·검수용 가상환경을 모은 숨김 폴더
- `**/.pytest_tmp_*`: 과거 pytest가 만든 비추적 임시 폴더. 제품 소스가 아님
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
# ★ 가상환경은 «저장소 루트»에 만든다 (app/ 안이 아니다)
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r app\requirements.txt -r .github\requirements-ci.txt

cd app
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

시험은 **네 묶음**으로 나뉘어 있습니다 (2026-08-27 실측, 합계 **4,575건**).

```powershell
# ① 웹서비스 — 가장 큼. ★ 반드시 app 폴더에서 돌린다
cd app
..\.venv\Scripts\python -m pytest -q -p no:cacheprovider -m "not local_integration"

# ②③④ 나머지는 저장소 루트에서
cd ..
.\.venv\Scripts\python -m pytest analysis_engine/src -q   # 조사 엔진
.\.venv\Scripts\python -m pytest deploy -q                # 배포 계약
.\.venv\Scripts\python -m pytest ops -q                   # 운영 절차
```

| 묶음 | 건수 | 소요 |
|---|---:|---|
| `app/` (웹서비스) | 4,181 | 약 6분 |
| `analysis_engine/src` | 173 | |
| `deploy` | 100 | |
| `ops` | 121 | |
| **합계** | **4,575** | |

**★ `app/`에서 돌려야 하는 이유** — `test_logic.py`라는 같은 이름의 파일이 두 폴더에
있고 둘 다 `__init__.py`가 없어, 저장소 루트에서 돌리면 수집 단계에서 중단됩니다
(`import file mismatch`). 두 파일은
`analysis_engine/.../provider_diagnostics/tests/`와 `app/src/features/report_summary/tests/`입니다.

**★ `-m "not local_integration"`** — 저장소에 넣지 않는 대용량 로컬 자료가 필요한 시험 4건을
제외합니다. 새로 클론한 환경에서는 이 마커 없이 돌리면 실패할 수 있습니다.

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
