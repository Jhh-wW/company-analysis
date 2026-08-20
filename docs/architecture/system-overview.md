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
       `-- real: analysis_engine + 공식 자료 수집 + 생성 모델
  -> canonical_report: 1~8장 비공개 초안
  -> company_comparison: 양사 공식 원문이 맞는 9장
  -> report_summary: 1~9장 fact_id 최소 부분집합 요약
  -> report_standard: 전체 fail-closed 출고 게이트
  -> storage: 보고서·FactRecord·Source·캐시 저장
  -> web / PDF / Notion: 같은 승인 정본을 채널별로 렌더
```

필수 장 하나라도 부족하거나 원문·법인·시점·상태·숫자·중복·비교 조건 중 하나라도
맞지 않으면 부분 결과를 공개하지 않고 `GATE_STOPPED`한다.

## 신뢰 경계

| 경계 | 신뢰하는 것 | 반드시 다시 검사하는 것 |
|---|---|---|
| 외부 입력 | 없음 | 길이, CSRF, 접근권한, 회사 범위, 후보 선택 서명 |
| 수집기 | 허용된 adapter 코드 | URL·host·문서 ID·날짜·원문 hash |
| Source 등록 | 서버 비밀 HMAC으로 잠긴 수집 payload | 저장 복원 후 `provenance_seal`, 공식 도메인 독립 증명 |
| 생성 모델 | 문장 후보만 | 원문 범위, FactRecord 결속, 시간·인과·수치·중복 |
| 캐시 | 성능 보조 | 현재 schema token과 현재 전체 출고 게이트 |
| PDF 준비 | 렌더 후보만 | 실제 bytes hash, 전 페이지 재렌더, glyph 가시성 |
| PDF 출고 | 동일 PDF hash의 승인 | 5역할 참여자 원장, 3인 독립 승인, DB release 무결성 |

`PROVENANCE_SEAL_SECRET`은 모든 운영 worker와 재시작에서 같은 32바이트 이상 값을
사용한다. 값이 유실되면 과거 Source를 새로 신뢰하지 않고 출고를 차단한다. 보고서
역직렬화나 검증 단계에서 입력값을 다시 seal해서는 안 된다.

## 영속 데이터

Render에서는 `/var/data` 하나를 영속 루트로 사용한다.

- `storage.db`: 보고서, 세션, 공유, 예산, PDF 승인·출고 원장
- `observability/runs.jsonl`: 실행·비용·게이트 결과
- `cache/`: 공식 API·도메인 분석의 재생성 가능한 캐시
- `backups/`: SQLite online backup과 SHA-256

DB 백업만으로는 배포를 복구할 수 없다. OAuth 비밀, `PROVENANCE_SEAL_SECRET`,
`PDF_RELEASE_PARTICIPANTS`, 공개 origin 설정은 별도 비밀 저장소에 함께 보관한다.

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

세부 기능 소유권은 [기능별 책임 지도](./feature-map.md), 실행·시험·출고 인계는
[검수 안내](../REVIEW_GUIDE.md)에서 확인한다.
