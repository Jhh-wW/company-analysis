# 시스템 개요

이 문서는 실행 위치와 신뢰 경계를 빠르게 파악하기 위한 개발자용 지도다. 보고서의
내용 규칙은 [출력물 기준](../출력물%20기준/README.md), 프로그램이 강제할 최소 조건은
[런타임 출고 계약](../출력물%20기준/90_공통_규칙/런타임_출고_계약.md)이 우선한다.

## 배포와 폴더 불변 조건

```text
render.yaml
  -> 저장소 루트를 Docker build context로 사용
  -> app/Dockerfile
  -> /srv/app 에서 python -m uvicorn src.web.main:app
       |-- /srv/app/src                 웹·파이프라인·저장·출력
       `-- /srv/analysis_engine         실제 조사 저수준 엔진
```

- `app/`과 `analysis_engine/`은 반드시 같은 부모 아래의 형제 폴더여야 한다.
- Docker build context를 `app/`으로 좁히면 실제 조사 엔진 복사가 깨진다.
- Uvicorn 작업 디렉터리는 `app/`이다. 저장소 루트에서 같은 import 명령을 실행한다고
  가정하지 않는다.
- `analysis_engine/tools/run_pilot.py`는 이름과 달리 CLI 전용 파일이 아니다.
  `pipeline/real.py`가 현재 이 파일을 동적으로 읽어 웹 실조사 adapter로 사용한다.
- SQLite와 인메모리 작업 상태 때문에 현재 운영 계약은 worker 1개, instance 1개다.

## 요청부터 공개까지

```text
회사명 + 선택 주소 힌트
  -> web: 인증·CSRF·공유 범위·비용·동시 실행 제한
  -> business_candidate: 후보 제시, 사람이 법인 확정
  -> pipeline
       |-- demo: 코드 내장 canonical 표본
       `-- real: analysis_engine + 공식 공시·IR·홈페이지 수집 + 생성 모델(뉴스 검색·AI 선별 생략)
  -> canonical_report: 1~8장 비공개 초안
  -> company_comparison: 양사 공식 원문이 맞을 때만 조건부 9장
  -> report_summary: Writer 검수 완료 FactRecord에서 3~5개 문장을 글자 변경 없이 선택(요약 AI·Reviewer 0회, 상태 verified_fact_reuse)
  -> report_standard: 전체 fail-closed 출고 게이트
  -> storage: 보고서·FactRecord·Source·캐시 저장
  -> web / PDF / Notion: 같은 해시 결속 자동출고 정본을 채널별로 렌더
```

필수 1~8장이나 원문·법인·시점·상태·숫자·중복 조건이 하나라도 맞지 않으면 결과를
공개하지 않고 `GATE_STOPPED`한다. 동일 조건 비교가 성립하지 않으면 9장을 생략하고
표준 부족 사유를 가진 `Grade.PARTIAL` 기본 보고서를 같은 출고 게이트로 검사한다.

## 내부 정기 작업 경계

```text
Render cron
  -> python -m tools.trigger_backup 또는 tools.trigger_maintenance
  -> 정확한 HTTPS URL + 작업별 Bearer 비밀, redirect 거부
  -> /internal/backup/run 또는 /internal/maintenance/run
  -> 웹 프로세스가 가진 SQLite 연결로 백업·주간 XLSX·휴지통 정리 실행
  -> 기간별 claim과 완료·실패 사건 저장
```

cron 컨테이너는 웹 영속 디스크와 S3 자격증명을 직접 읽지 않는다. 백업 호출 비밀과
관리 정기작업 비밀은 분리하며, 주간 XLSX와 정리 작업은 AI·DART·Naver·Anthropic을
호출하지 않는다. 이 경계는 로컬 코드·회귀 시험까지만 검증됐고 실제 Render·S3 연결은
배포 전 보류 상태다.

## 신뢰 경계

| 경계 | 신뢰하는 것 | 반드시 다시 검사하는 것 |
|---|---|---|
| 외부 입력 | 없음 | 길이, CSRF, 접근권한, 회사 범위, 후보 선택 서명 |
| 수집기 | 허용된 adapter 코드 | URL·host·문서 ID·날짜·원문 hash |
| Source 등록 | 서버 비밀 HMAC으로 잠긴 수집 payload | 저장 복원 후 `provenance_seal`, 공식 도메인 독립 증명 |
| 생성 모델 | 문장 후보만 | 원문 범위, FactRecord 결속, 시간·인과·수치·중복 |
| 캐시 | 성능 보조 | 현재 schema token과 현재 전체 출고 게이트 |
| PDF 준비 | 렌더 후보만 | 실제 bytes hash, 전 페이지 재렌더, glyph 가시성 |
| 자동출고 | 동일 report/PDF/page hash의 필수 검사 | 사실·인용·수치·구조·금지 문구·PDF 렌더·채널 동등성, DB release 무결성 |

`PROVENANCE_SEAL_SECRET`은 모든 운영 worker와 재시작에서 같은 32바이트 이상 값을
사용한다. 값이 유실되면 과거 Source를 새로 신뢰하지 않고 출고를 차단한다. 보고서
역직렬화나 검증 단계에서 입력값을 다시 seal해서는 안 된다.

## 영속 데이터

Render에서는 `/var/data` 하나를 영속 루트로 사용한다.

- `storage.db`: 보고서, 세션, 공유, 예산, 자동출고·내부 AI 원가·고객 청구 원장. 구형 PDF 수동 승인 원장은 감사자료로 보존
- `observability/runs.jsonl`: 실행·비용·게이트 결과
- `cache/`: 공식 API·도메인 분석의 재생성 가능한 캐시
- `backups/`: SQLite online backup과 SHA-256

SQLite는 런타임 버전을 확인해 WAL-reset 결함 수정판(3.51.3 이상 또는 공식
3.44.6·3.50.7 역이식 계열)에서만 WAL을 쓴다. 영향을 받는 버전에서는 데이터 안전을
우선해 `DELETE` rollback journal로 자동 하향한다. 패치판으로 전환한 뒤에는 WAL 복귀와
동시 요청 부하를 다시 검증한다.

DB 백업만으로는 배포를 복구할 수 없다. OAuth 비밀, `PROVENANCE_SEAL_SECRET`,
공개 origin 설정은 별도 비밀 저장소에 함께 보관한다. 구형
`PDF_RELEASE_PARTICIPANTS`는 감사기록 해석용일 뿐 신규 출고 권한이 아니다.

내부 AI 변동원가는 성공·실패와 무관하게 단계별 실제 model/token/cache/batch 사용량으로 기록한다. 고객 청구는 같은 자동출고 레코드가 생긴 완료 보고서에만 별도로 결정하며, 서버비는 월 고정비 표에 분리한다.

## 현재 구조상 주의점

- `pipeline/real.py`, `pipeline/demo.py`, `web/job_runtime.py`, `web/routers/analysis.py`는
  호환 로직까지 함께 가진 큰 조립 파일이다. 작은 기능 변경은 먼저 기능 폴더에 두고
  이 파일에는 순서 연결만 추가한다.
- `analysis_engine`은 아직 독립 설치 패키지가 아니라 `sys.path`를 조정해 읽는다.
  패키지화 전에는 폴더·진입점 이동을 단독 리팩터링으로 처리하지 않는다.
- DOCX·채용공고·OCR 관련 일부 코드는 구형 호환 시험에 남아 있지만 신규 공개 경로는
  사용하지 않는다. import와 fixture를 분리하기 전 폴더만 삭제하지 않는다.
- 루트 `.dockerignore`는 allowlist 방식이다. 새 런타임 파일을 추가하면 Docker 이미지에
  실제 포함되는지 CI smoke test로 확인한다.
- 내부 cron 도구와 라우트는 exact HTTPS·분리된 비밀·redirect 거부를 유지해야 한다.
  작업 추가 시 기간별 claim과 실패 사건을 같은 기능 폴더에서 시험한다.

세부 기능 소유권은 [기능별 책임 지도](./feature-map.md), 실행·시험·출고 인계는
[검수 안내](../REVIEW_GUIDE.md)에서 확인한다.
