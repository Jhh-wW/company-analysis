# analysis_engine — 실제 조사 엔진

`analysis_engine/`은 `app/`의 `PIPELINE=real`이 사용하는 필수 런타임 의존성이다.
독립 설치 패키지나 별도 웹서비스가 아니며, 신규 사용자의 제품 진입점은
[`app/README.md`](../app/README.md)의 FastAPI 실행 절차다.

## 최소 진입점

| 경로 | 역할 | 주의 |
|---|---|---|
| `app/src/features/pipeline/real.py` | 활성 v3 회사분석 pipeline과 엔진 연결 | 실제 provider 호출과 비용이 발생할 수 있음 |
| `analysis_engine/tools/run_pilot.py` | `real.py`가 격리 namespace로 읽는 기존 조사 함수와 연구용 일괄 실행기 | 단독 CLI는 과거 파일럿 흐름이며 현재 출력 정본이 아님 |
| `analysis_engine/tools/survey_audit_reports.py` | 공시 원문 조사·감사 보조 | DART 호출과 로컬 산출물 생성 가능 |
| `analysis_engine/tools/build_goldenset_answer.py` | 골든셋 조사 보조 | DART 호출과 로컬 산출물 생성 가능 |

신규 보고서의 정본 조립과 출고 게이트는 `app/src/features/pipeline/`,
`report_standard`, `export_pdf`가 소유한다. 이 폴더의 과거
`posting_gate`·`privacy_filter`·`fingerprint` 또는 파일럿의 채용공고 단계는 호환·연구
코드이며 신규 회사명+선택 주소 흐름의 제품 계약이 아니다.

## 동적 import 계약

`app/src/features/pipeline/real.py::_engine()`은 요청마다 다음 경로를 `sys.path`에
추가하고 `tools/run_pilot.py`를 독립 module namespace로 읽는다.

```text
analysis_engine/
├── src/
├── tools/
│   └── run_pilot.py
└── data/pilot/
```

이 방식은 데모 모드가 무거운 실제 조사 의존성 없이 시작되게 하고, 기존 파일럿의
요청별 전역 비용 상태가 다음 요청에 누적되지 않게 한다. 대신 디렉터리 이름·상대 위치와
`src`/`tools`의 import 이름이 런타임 계약이다. `analysis_engine`을 이동·삭제하거나
일부 파일만 복사하면 로컬에서는 데모가 떠도 `PIPELINE=real`이 실패할 수 있다.

배포 이미지는 `app/Dockerfile`과 루트 `.dockerignore` allowlist를 통해
`analysis_engine/src/`, 세 도구, `data/pilot/`을 명시적으로 포함한다. 새 런타임
파일을 추가하면 두 파일과 container smoke를 함께 검토한다.

## 환경과 실행 자료

- Python·라이브러리 의존성은 `app/requirements.txt`에서 함께 설치한다. 시험까지 돌리려면
  `.github/requirements-ci.txt`도 같이 설치한다.
- 기본 개발 실행은 `analysis_engine/.env`를 읽을 수 있지만 값이나 파일 내용을
  출력해서는 안 된다.
- `ANALYSIS_ENGINE_DISABLE_DOTENV=1`이면 `.env` 존재 여부를 확인하기 전 즉시
  반환하고, 실행기가 allowlist로 전달한 환경변수만 사용한다.
- `APP_DATA_ROOT`가 없으면 로컬 `analysis_engine/data`·`logs`를, 배포에서는
  `/var/data` 아래 엔진 영역을 사용한다.
- `.env`, 원문, 로그, 사용량 원장과 생성 산출물은 Git에 커밋하지 않는다.

도구를 직접 실행하면 외부 API와 비용을 사용할 수 있다. 사용자의 명시적 비용 승인,
격리된 출력 경로, 필요한 비밀 환경변수를 확인하기 전에는 실행하지 않는다. 안전한
웹 미리보기와 유료 실행 전환은 `app/README.md`의 전용 실행기를 사용한다.

## 시험

저장소 루트의 가상환경으로 엔진 단위 시험을 실행한다.

```powershell
$env:TLDEXTRACT_CACHE = "$PWD\.cache\tldextract"
.\.venv\Scripts\python -m pytest analysis_engine/src -q `
  --basetemp=.pytest_tmp_engine_readme
```

동적 연결과 활성 v3 계약은 app 시험도 함께 확인한다. 두 묶음을 한 pytest 세션에
합치면 같은 이름의 시험 파일이 겹쳐 수집이 중단되므로 따로 실행한다.

```powershell
cd app
$env:TLDEXTRACT_CACHE = "$PWD\.cache\tldextract"
..\.venv\Scripts\python -m pytest src tools/tests -q `
  -m "not local_integration" `
  --basetemp=.pytest_tmp_engine_integration
```

특히 `src/features/pipeline/tests/test_real_contract.py`와 real pipeline 관련 시험은
동적 엔진 파일, 금지된 구형 입력, provider 경계의 회귀를 감시한다. GitHub Actions
`quality-gate`는 app·엔진·배포·운영 네 시험 묶음이 통과한 뒤 Docker 이미지를 만들고
컨테이너의 `/readyz`를 확인한다.
