# 기여 안내

## 먼저 읽을 문서

1. [문서 지도](docs/README.md)
2. [시스템 개요](docs/architecture/system-overview.md)
3. [출력물 기준](docs/출력물%20기준/README.md)
4. [런타임 출고 계약](docs/출력물%20기준/90_공통_규칙/런타임_출고_계약.md)
5. [검수 안내](docs/REVIEW_GUIDE.md)

규칙이 충돌하면 `docs/출력물 기준/`을 우선한다. README와 날짜가 붙은 조사·감사
문서는 설명 또는 역사 기록이며 정본을 바꾸지 않는다.

## 로컬 준비

Python 3.13을 사용한다. `app/`과 `analysis_engine/`의 형제 폴더 관계를 유지한다.

```powershell
cd app
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt -r ..\.github\requirements-ci.txt
.\로컬데모켜기.ps1
```

로컬 데모는 외부 유료 API를 호출하지 않는다. 실제 조사 모드는 사용자 승인과 별도
비밀 설정 없이 실행하지 않는다. `.env`, DB, 로그, 다운로드 원문과 검수 산출물을
커밋하지 않는다.

## 변경 원칙

- 신규 보고서는 `company-report-v4-canonical` 필수 1~8장과 조건이 맞을 때의 9장만 공개한다.
- 필수 1~8장 근거가 부족하면 문장·빈 장으로 대체하지 않고 `GATE_STOPPED`한다. 9장 비교만 성립하지 않으면 표준 부족 사유를 가진 `Grade.PARTIAL` 기본 보고서로 출고한다.
- 수집, 작성, 캐시, 웹, PDF, Notion 중 어느 경로도 공통 출고 게이트를 우회하지 않는다.
- `FactRecord` 또는 `Source` 필드를 바꾸면 저장 왕복과 모든 공개 렌더러 시험을 함께
  갱신한다.
- 시간 상태, 원수치, 법인 범위, 공식 출처, 비교 조건을 문자열 추측으로 승격하지 않는다.
- 구형 호환 폴더는 비활성처럼 보여도 동적 import와 fixture를 확인한 뒤 제거한다.
- 관련 없는 작업트리 변경을 되돌리거나 대량 정리하지 않는다.

## 시험

테스트 임시 파일은 저장소 안의 명시적 basetemp에 둔다.

```powershell
cd app
.\.venv\Scripts\python -m pytest -q src/features/report_standard/tests src/features/provenance/tests `
  src/features/company_performance/tests src/features/company_comparison/tests `
  src/features/pipeline/tests src/features/storage/tests `
  -m "not local_integration" `
  --basetemp=.pytest_tmp_core_review

.\.venv\Scripts\python -m pytest -q -m "not local_integration" `
  --basetemp=.pytest_tmp_full_review
cd ..
$env:TLDEXTRACT_CACHE="$PWD\app\.cache\tldextract"
.\app\.venv\Scripts\python -m pytest -q analysis_engine/src `
  --basetemp=app/.pytest_tmp_engine_review
git diff --check
```

저장소 밖의 과거 데모 15건·DART 전체 목록·검증된 공시 원문까지 준비한 환경에서만
`python -m pytest -m local_integration`을 별도로 실행한다. 이 marker를 명시적으로
선택했는데 자료가 없으면 통합 시험은 실패한다.

PDF 변경은 실제 canonical 보고서를 생성하고 모든 페이지 PNG를 직접 확인한다. PDF
승인·다운로드 변경은 rollback, race, DB tamper, 실제 PDF/PNG 재해시 회귀도 실행한다.

## 변경 요청에 포함할 것

- 바꾼 제품 계약과 영향 받는 경로
- 추가하거나 갱신한 정상·실패 회귀
- 실행한 정확한 명령과 결과
- 스키마·캐시·DB migration 영향
- 환경변수·배포·백업 영향
- 화면 또는 PDF 변경이면 검수 증거
- 남아 있는 한계와 rollback 방법
