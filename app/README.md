# app — 운영 웹서비스

`app/`은 로그인, 접근 제한, 화면, 분석 실행, 보고서 저장과 내보내기를 담당하는
FastAPI 서비스입니다. 실제 조사 엔진은 `../analysis_engine/`에 있으며
`src/features/pipeline/real.py`가 연결합니다.

## 경계

```text
app/
├── src/
│   ├── features/          # 기능별 로직·상수·전용 시험
│   ├── core/              # 여러 기능이 실제로 공유하는 형식·경로
│   └── web/               # HTTP·화면·라우팅·기능 조립
├── tools/                 # 인증된 백업·정기 작업 호출과 복구
├── docs/                  # 배포와 외부 서비스 설정
├── Dockerfile
└── requirements.txt
```

`src/features/` 아래 기능들은 다섯 묶음으로 볼 수 있습니다.

- **접근·비용**: `auth`, `sharelink`, `budget`, `cost_tracking`, `report_access`
- **회사 확정·자료 수집**: `business_candidate`, `homepage`, `filingclean`,
  `revenuemix`, `company_performance`, `company_specificity`, `company_use`
- **보고서 작성·검증**: `pipeline`, `composer`, `spanselect`, `writer`,
  `chapter_evidence`, `company_comparison`, `report_summary`, `grading`,
  `provenance`, `report_standard`, `report_quality`, `final_gate_diagnostic`
- **출력·전달**: `export_pdf`, `export_notion`, `report_delivery`, `feedback_report`
- **저장·운영**: `storage`, `backup`, `report_recovery`, `admin_dashboard`,
  `observability`, `provider_health`, `pilot_evaluation`, `release_acceptance`

`newspick`과 `posting_image`, 채용 결합 필드는 구형 호환 코드입니다. 신규 조사·캐시
적중·화면·PDF·Notion 내용을 구성할 수 없습니다.

각 폴더의 입력·출력과 담당 범위는
[기능별 책임 지도](../docs/architecture/feature-map.md)에 정리되어 있습니다.
신규 보고서의 고정 목차와 출고 조건은
[런타임 출고 계약](../docs/출력물%20기준/90_공통_규칙/런타임_출고_계약.md)이 우선합니다.

## 접근 계층

| 갈래 | 통과 조건 | 새 조사 |
|---|---|---|
| 관리자 | `ADMIN_EMAILS`의 구글 계정 | 허용. `/admin` 접근도 이 갈래만 |
| 회원 | 관리자가 초대 명단에 넣은 구글 계정 | 허용. 사람마다 하루 성공 건수·비용 한도 |
| 초대 링크 | 관리자가 발급한 링크 주소·QR | 로그인 없이 허용. 링크마다 하루·누적 한도와 만료일 |
| 그 밖 | 로그인도 링크도 없음 | 막힘. 미리 만들어 둔 보고서 열람만 |

`BETA_ADMIN_ONLY=1`이면 로그인 벽이 켜집니다. **구글 로그인만으로는 통과하지
못합니다** — 초대 명단에 활성 상태로 있어야 합니다. 링크 발급·초대·`/k/` 입구가
열리는지는 `DEPLOYMENT_RUNTIME_CONTRACT` 값이 정합니다.

관리자 화면은 오늘 상태·초대 링크·회원·보고서·비용·운영 여섯 묶음이며 PC와 폰이 같은
여섯을 봅니다. 링크 철회·회원 한도 변경처럼 되돌리기 어려운 동작은 이유를 적고 한 번 더
확인해야 실행되고, 옛 주소는 지우지 않고 새 화면으로 넘깁니다.

## PDF 출고 흐름

`report_standard`를 통과한 보고서는 사실·인용·수치·구조·금지 문구, PDF 전 페이지
렌더, 웹·PDF·Notion 동등성 자동검사를 거칩니다. 같은 보고서·PDF·모든 페이지 PNG
해시와 검사 버전에 결속된 전 항목이 통과해야 자동출고합니다.

검사 하나라도 실패하거나 검사 뒤 hash가 바뀌면 부분 화면 없이 전체를
`GATE_STOPPED`합니다. 수동 `/review/pdf/*` GET/POST는 `410`이며 구형 세 사람 승인 DB는
감사자료로만 보존됩니다.

`analysis_engine/data/pilot/`의 시범 자료는 판정 회귀 시험이 읽는 고정 표본입니다.
자동검사 보정용이며 서비스 건별 출고 승인이 아닙니다. 사람 품질판정은
`tools/manage_pilot_quality.py`가 별도 파일에 기록하고, 런타임 출고 코드는 그 파일을
읽지 않습니다. 입력 계약은 `tools/manage_pilot_quality.py --help`가 안내합니다.

내부 AI 원가는 성공·실패와 무관하게 실제 사용량을 기록하고, 고객 청구는 자동출고 뒤
별도 결정합니다. 판매가는 아직 확정되지 않아 화면이나 결제 기능에 넣지 않습니다.

## 실행 모드

| 설정 | 용도 | 외부 조사 비용 |
|---|---|---|
| `PIPELINE=demo` | 저장된 데모 자료 재생 | 없음 |
| `PIPELINE=real` | DART·공식 IR·회사 홈페이지·생성 AI를 사용하는 실제 조사 | 발생 가능 |

**코드 기본값은 `PIPELINE=demo`·`BETA_ADMIN_ONLY=1`**입니다 — 로컬에서 실수로 비용이
나가지 않게 하기 위해서입니다. 운영 배포(Render)만 `render.yaml`에서 `real`로 올립니다.

자동 배포는 꺼져 있어(`render.yaml`의 `autoDeployTrigger: off`) 커밋을 올려도 사람이
Manual Deploy를 누르기 전에는 반영되지 않습니다.

## 로컬 데모

Python 3.13 가상환경과 의존성을 처음 한 번만 준비합니다. 가상환경은 **저장소 루트**에
만듭니다.

```powershell
py -3.13 -m venv ..\.venv
..\.venv\Scripts\python -m pip install -r requirements.txt -r ..\.github\requirements-ci.txt
.\로컬데모켜기.ps1
```

다른 포트를 쓰려면 `.\로컬데모켜기.ps1 -Port 8010`처럼 실행합니다. 실행 창에
`/auth/local-demo/start?token=...` 형태의 **이 실행 전용 관리자 로그인 주소**가
표시됩니다. 그 주소를 같은 컴퓨터의 브라우저에서 열면 token이 없는 깨끗한 로컬
주소로 즉시 바뀌고 관리자 화면까지 들어갈 수 있습니다. 로그아웃한 뒤에는 같은 실행
창의 주소를 다시 열 수 있습니다. 일반 우측 상단 로그인에는 로컬 관리자 입구가
표시되지 않습니다. 주소는 화면 공유·문서·메신저·로그에 남기지 마세요.

이 실행기는 `PIPELINE=demo`와 loopback 접속을 강제하며 Google OAuth·유료 API·Notion
설정을 서버에 전달하지 않습니다. 실행할 때마다 32바이트 임의 capability를 새로 만들고
HTTP access log를 끄므로 로그인 URL이 서버 요청 로그에 남지 않습니다. SQLite와 실행
이력, 도메인 캐시는 모두 `app/.local_demo/` 아래에 새로 저장되어 기존 사용자 자료와
섞이지 않습니다. 현재 터미널의 비밀값을 읽거나 바꾸지 않습니다. `.local_demo/`를 지우면
이 전용 데모 기록만 초기화되며, 실행 중에는 파일을 지우지 마세요.

구글 로그인을 실제로 확인하려면 [구글 로그인 설정](docs/구글로그인_설정.md)을 따릅니다.
그 절차는 이 실행기가 아니라 `서버켜기.ps1` 또는 직접 실행한 uvicorn을 씁니다.

## 로컬 실시간 성능시험

저장된 샘플이 아닌 임의 회사로 실제 조사 흐름을 재려면 전용 실행기를 씁니다.
기본 실행은 화면과 설정만 확인하는 **미리보기**이며 외부 호출은 0건입니다.

```powershell
.\실시간성능시험켜기.ps1 -Port 8020
```

실제 provider를 호출해 비용이 발생해도 된다고 사용자가 명시적으로 승인한 시험에서만,
현재 PowerShell 프로세스에 아래 네 비밀 환경변수를 미리 설정한 뒤 스위치를 붙입니다.
실행기는 `.env`를 자동으로 읽지 않으며 값은 출력하거나 파일에 저장하지 않습니다.

- `DART_API_KEY`
- `ANTHROPIC_API_KEY`
- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`

```powershell
.\실시간성능시험켜기.ps1 -Port 8020 -EnablePaidProviders
```

Google Places 주소 후보 검색은 결과 보관·표시 약관 검토가 끝날 때까지 이 실행기에서
항상 잠겨 있습니다. 부모 PowerShell에 Google key나 동의 환경변수가 있어도 자식에
전달하지 않습니다. 브라우저에서도 외부 호출·비용 안내 체크박스를 다시 선택해야 첫
요청이 진행됩니다.

기본 예상비용 운영 기준은 1건 1,200원, 한국시간 하루 2,200원이며
`-PerRunExpectedCostCapKrw`, `-DailyExpectedCostCapKrw`로 더 낮게 조정할 수 있습니다.
이는 provider 호출 전에 예상예약 합계를 차단하는 기준이지 청구액 hard cap은 아닙니다.
실제 단가·토큰 사용량에 따라 최종 비용은 기준을 넘을 수 있습니다. 하루 2,200원
기본값은 결과를 살피며 1건씩 확인하는 안전 시험용이라 일괄 실행은 이 기본값에서
차단됩니다.

매 실행은 `app/.local_evaluation_runs/YYYYMMDD_HHMMSS_<임의값>/`에 DB·이력·도메인
캐시를 새로 격리합니다. 이 폴더에는 회사 입력과 수집 원문이 남을 수 있으므로 결과를
확인한 뒤 해당 실행 폴더만 삭제하세요. 종료와 함께 지우려면 `-DeleteDataOnExit`를
붙입니다. 실제 사용자 DB와 로컬 데모 폴더는 사용하지 않습니다.

안전 경계는 [OWASP Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)의 최소 권한·비밀 로그 금지,
[The Twelve-Factor App: Config](https://12factor.net/config)의 코드/설정 분리,
[FastAPI 서버 워커 문서](https://fastapi.tiangolo.com/deployment/server-workers/)와
[Uvicorn 설정 문서](https://www.uvicorn.org/settings/)의 명시적 host·workers 설정을
따릅니다.

## 시험

```powershell
$env:TLDEXTRACT_CACHE="$PWD\.cache\tldextract"
..\.venv\Scripts\python -m pytest src tools/tests -q `
  -m "not local_integration" `
  --basetemp=.pytest_tmp_app_readme
```

조사 엔진·배포 계약·운영 절차 시험과 Docker 실행 검사는 루트의 GitHub Actions가
함께 돌립니다. 명령은 [검수 안내](../docs/REVIEW_GUIDE.md) §5에 모아 두었습니다.

정본 변경은 `Report.schema_version=company-report-v4-canonical`, 필수 1~8장·조건부 9장,
사실 원장과 단일 소유, 4·5·6장 시간 상태, 공개 9장의 양사 근거, 구형 섹션·직무 맞춤
차단과 PDF QA를 함께 검증해야 합니다.

## 운영 문서

- [Render 배포](docs/Render_배포.md)
- [장기 휴면 백업·복구](docs/장기_휴면_백업.md)
- [Google 로그인](docs/구글로그인_설정.md)
- [Notion 전송](docs/노션_설정.md)
- [배포 리허설 사용법](docs/배포리허설_사용법.md)
- [릴리스 수락시험](docs/릴리스_수락시험.md)

비밀값과 실제 사용자 데이터는 이 폴더에 저장해 Git으로 관리하지 않습니다.
