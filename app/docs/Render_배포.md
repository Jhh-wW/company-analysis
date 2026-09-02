# Render 운영 배포

이 문서는 `app/`을 Render에 배포하고 복구하는 운영 절차다. 서비스의 내용·출고
정본은 [런타임 출고 계약](../../docs/출력물%20기준/90_공통_규칙/런타임_출고_계약.md),
환경변수 형식은 [`.env.example`](../.env.example), 전체 실행법은
[`app/README.md`](../README.md)를 우선한다.

## 배포 원칙

- 비공개 저장소의 기본 브랜치와 통과한 GitHub Actions `quality-gate`에서만 배포한다.
- 기존 무료 확인판은 `PIPELINE=demo`, `BETA_ADMIN_ONLY=1`로 시작했다. 현재 Blueprint는 실제
  회사 결과를 비교하기 위한 관리자 전용 실분석 운영판을 준비한다.
- Uvicorn worker와 Render instance는 각각 `1`로 유지한다.
- ⛔ `maxShutdownDelaySeconds`를 `render.yaml`에 **넣지 마라** (2026-08-29 실측).
  Render 가 Blueprint 동기화를 거부한다 — `max shutdown delay is not supported for
  services with a disk`. 디스크는 SQLite·보고서·감사 기록을 보존하므로 뺄 수 없다.
- ⚠️ 그래서 종료 유예는 Render 기본 **30초**다. 그런데 종료 시간은 서로 겹치지 않는다 —
  Uvicorn이 먼저 HTTP task를 최대 20초 정리한 **뒤** lifespan의 앱 조사 정리(최대 240초)와
  취소 유예(1초)를 실행한다. 즉 앱이 기대하는 261초가 플랫폼이 주는 30초를 **넘는다.**
  배포·재시작 중이던 조사는 비용·중단 표식을 저장하기 전에 잘릴 수 있다.
  이건 새로 생긴 문제가 아니라 `master` 배포본이 내내 있던 상태다.
  근본 해결은 `docs/architecture/deployment-contract.md`의 종료·교체 순서를 따른다.
- 관리자 실분석 운영판은 `standard` web plan과 `/var/data` 1GB 영속 디스크를 사용한다.
  실제 DART 118,747사 후보 색인이 Starter의 512MB를 넘어 `/confirm` 중 인스턴스가
  재시작된 운영 측정에 따른 최소 사양이다. 플랜·디스크 비용은 바뀔 수 있으므로 배포 직전
  [Render 요금 페이지](https://render.com/pricing)와 Dashboard의 예상 청구액을 확인한다.
- 비밀값과 사용자 식별자는 Git, 채팅, 티켓, 화면 캡처에 남기지 않는다.
- 현재 `render.yaml`은 web service 1개만 만들고 `autoDeployTrigger: off`로 둔다. 커밋이나 CI
  통과만으로 배포되지 않는다. 비용·환경변수·비밀값을 확인한 뒤 Dashboard에서 수동
  배포하고, Blueprint의 Auto Sync도 비활성인지 확인한다.

## 기존 무료 demo 범위

기존 무료 배포의 runtime contract는 `render-admin-demo-no-forwarded-v1`이다. 이것은
관리자만 접근하는 demo 확인용이며 정식 공개 운영 승인이 아니다.

- Free instance, `PIPELINE=demo`, `BETA_ADMIN_ONLY=1`, instance와 worker 각각 1개
- 경로·쿼리 없는 고정 HTTPS `PUBLIC_ORIGIN`
- `GOOGLE_REDIRECT_URI`는 정확히 `<PUBLIC_ORIGIN>/auth/callback`
- `FORWARDED_ALLOW_IPS`는 빈 값이며 Uvicorn proxy headers를 신뢰하지 않음
- 공유 링크, real provider, Notion, S3 외부 백업, backup/maintenance cron은 보류

기존 일반 공개 배포의 forwarded evidence gate, release policy·공급망 verifier, 외부 백업
adapter HOLD는 그대로다. 이 demo를 그 승인 증거로 재사용하지 않는다.

## 관리자 실분석 운영판 범위

현재 `render.yaml`의 runtime contract는 `render-admin-real-no-forwarded-v1`이다. 외부 조사
provider를 실제로 호출하고 영속 디스크에 결과를 보존하지만, 품질 개선을 위한 관리자 전용
운영 파일럿이다.

- `PIPELINE=real`, `BETA_ADMIN_ONLY=1`, instance/worker 각각 1개
- Render `standard` web plan과 `/var/data` 1GB 영속 디스크
- 경로·쿼리 없는 고정 HTTPS `PUBLIC_ORIGIN`
- `GOOGLE_REDIRECT_URI`는 정확히 `<PUBLIC_ORIGIN>/auth/callback`
- 빈 `FORWARDED_ALLOW_IPS`; Render edge의 forwarded headers를 신뢰하지 않음
- 관리자 본인의 로그인과 실제 분석만 허용; MEMBER 초대와 LINK 공유는 차단
- `autoDeployTrigger: off`; 환경 확인 뒤 Dashboard에서 수동 배포

이 운영판을 "일반 공개 완료"로 부르지 않는다. 외부 사용자의 로그인·공유를 여는 일반 공개
계약은 trusted ingress/canary verifier가 없어 BLOCKED이며, 독립 외부 백업도 adapter가 없어
BLOCKED다. 영속 디스크는 재시작·재배포의 데이터 보존 수단이지 독립 백업이 아니다.

## 포트폴리오 링크 계약(render-portfolio-link-v1) 범위

`render-portfolio-link-v1`은 관리자 실분석 운영판과 같은 안전 조건(고정
`PUBLIC_ORIGIN`, 빈 `FORWARDED_ALLOW_IPS`, `BETA_ADMIN_ONLY=1`, 같은
`--no-proxy-headers` 실행 명령) 위에서 **초대 명단 회원에게만** 링크·초대·QR 입구를
여는 계약이다. `app/src/web/routers/admin.py`·`app/src/web/routers/analysis.py`는
바뀌지 않는다 — 두 계약이 갈리는 지점은 아래 표뿐이다.

| 동작 | `render-admin-real-no-forwarded-v1`(현재) | `render-portfolio-link-v1` |
|---|---|---|
| 관리자 로그인·실제 분석 | 허용 | 허용 |
| `/admin/link/new`(LINK 발급) | 차단(404) | 허용 |
| `/admin/invite`(친구 초대) | 차단(409) | 허용 |
| `/k/` 링크 입구 | 로그인 화면으로 이동 | 열림 |
| 명단 밖 구글 로그인 | `/auth/not-admin` | `/auth/not-admin`(그대로) |
| 명단 «회원»(allowlist 활성) 구글 로그인 | `/auth/not-admin` | 홈·조사 경로 통과, `/admin`은 그대로 차단 |
| Host 고정·CSRF Origin 고정 | 켜짐 | 켜짐(그대로) |
| `ENGINE_V2` | 배포자가 값 선택 | `1` 필수 |
| `REPORT_RELEASE_MODE` | 배포자가 값 선택 | 필수(SHADOW/ENFORCE_NO_PARTIAL/FULL 중 하나) |

★ 「명단 회원이 로그인 벽을 통과한다」는 `BETA_ADMIN_ONLY=1`인 모든 배포에 계약과
무관하게 같이 적용된다 — 로그인 벽은 «누가 통과하는가»의 축이고, runtime contract는
«어느 forwarded-header 신뢰 모델을 쓰는가»의 축이라 서로 다른 문제이기 때문이다.
다만 LINK 발급·초대·`/k/` 입구는 이 계약에서만 열린다(위 표).

옛 운영판에서 전환하는 절차:

1. `render.yaml`의 `DEPLOYMENT_RUNTIME_CONTRACT` 값만
   `render-admin-real-no-forwarded-v1`에서 `render-portfolio-link-v1`로 바꾼다.
   플랜·디스크·`FORWARDED_ALLOW_IPS`·실행 명령 등 다른 값은 그대로 둔다.
2. `ENGINE_V2=1`, `REPORT_RELEASE_MODE`가 SHADOW/ENFORCE_NO_PARTIAL/FULL 중 하나로
   설정돼 있는지 확인한다 — 이 계약은 시작 검증에서 이 둘을 필수로 본다.
3. Render Dashboard의 일반 `Deploy latest commit`이 아니라, 이 서비스를 관리하는
   Blueprint에서 **Manual Sync / Deploy Blueprint**를 실행한다. 그래야 `render.yaml`의
   설정 변경과 코드가 함께 반영된다.
4. 배포 뒤 `/healthz`·`/readyz`, 관리자 로그인, 명단 밖 구글 로그인이 여전히
   `/auth/not-admin`으로 가는지, LINK 발급·초대·`/k/` 입구가 실제로 열리는지를
   확인한다.
5. 되돌릴 때는 1번 값을 `render-admin-real-no-forwarded-v1`로 되돌리고 같은 Manual
   Sync를 한 번 더 실행한다.

## 활성 제품 계약

신규 분석 입력은 **회사명 필수 + 주소 힌트 선택**이다. 주소를 비워도 회사 후보 확인과
조사가 진행되어야 한다. 채용공고, 공고 이미지, OCR, 직무·개인 맞춤 입력은 신규 흐름의
입력이 아니다.

화면, PDF, Notion은 서로 다른 보고서를 만들지 않는다. `report_standard`를 통과한 같은
정본 보고서를 렌더링하며, **PDF가 사용자 다운로드 정본**이다. 같은 보고서·PDF·모든
페이지 PNG hash의 필수 자동검사를 전부 통과한 자동출고 레코드를 웹·PDF·Notion이
공유한다. 하나라도 실패하면 세 채널 전체를 `GATE_STOPPED`한다.

## 무료 demo를 처음 배포한 순서

1. GitHub Actions `quality-gate`가 초록색인지 확인한다.
2. Render에서 저장소 루트의 `render.yaml`로 Blueprint를 만든다.
3. `ADMIN_EMAILS`, Google OAuth client ID·secret·redirect URI만 Render 화면에 직접 입력한다.
4. Blueprint가 Render의 HTTPS 주소를 `PUBLIC_ORIGIN`으로 자동 고정한 뒤, 같은 주소의
   `/auth/callback`을 Google OAuth 승인 URI와 `GOOGLE_REDIRECT_URI`에 반영한다.
5. `/healthz`(프로세스 생존)와 `/readyz`(SQLite·로그인 설정 준비), 관리자 로그인,
   비관리자 차단을 확인한다.
6. `PIPELINE=demo`에서 회사명만 입력한 경우와 주소 힌트를 함께 입력한 경우를 각각 시험한다.
7. PDF 준비 → 필수 자동검사 → hash 결속 자동출고 → 다운로드와 수동 승인 410을 시험한다.

Notion, 공유 링크, real provider, 외부 백업과 cron 시험은 무료 demo 완료 뒤의 별도
작업이었다.

## 관리자 실분석 운영판 배포 순서

1. GitHub Actions `quality-gate`와 관리자 실분석 계약의 환경 검증 시험이 모두 통과했는지
   확인한다.
2. [Render 요금 페이지](https://render.com/pricing)와 Dashboard에서 `standard` web plan,
   1GB 영속 디스크의 현재 청구 조건을 확인하고 비용을 승인한다.
3. 기존 Blueprint에 `render.yaml` 변경을 반영하되 바로 자동 배포하지 않는다.
4. 기존 OAuth 4개 값(`ADMIN_EMAILS`, Google client ID·secret·redirect URI)을 유지하고,
   `ANTHROPIC_API_KEY`, `DART_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`을 Render의
   비밀 환경변수로 직접 입력한다. 값을 저장소·문서·채팅에 붙이지 않는다.
5. `PROVENANCE_SEAL_SECRET`이 32바이트 이상이며 기존 실행 기록을 유지해야 할 때 같은 값으로
   보존되는지 확인한다. Blueprint가 새 값을 다시 만들도록 기존 값을 삭제하지 않는다.
6. `PIPELINE=real`, `BETA_ADMIN_ONLY=1`, `ENGINE_V2=1`,
   `REPORT_RELEASE_MODE=SHADOW`,
   `DEPLOYMENT_RUNTIME_CONTRACT=render-admin-real-no-forwarded-v1`, 빈
   `FORWARDED_ALLOW_IPS`, `GRACEFUL_SHUTDOWN_SECONDS=20`, Render 종료 유예 300초,
   `/var/data` 1GB disk mount를 확인한다.
7. `PUBLIC_ORIGIN`과 Google OAuth 승인 URI, `GOOGLE_REDIRECT_URI`가 모두 정확히 같은
   `<HTTPS origin>/auth/callback`을 가리키는지 확인한다.
8. Render Dashboard의 일반 `Deploy latest commit`이 아니라, 이 서비스를 관리하는
   Blueprint에서 **Manual Sync / Deploy Blueprint**를 한 번 실행한다. 그래야 `render.yaml`의
   설정 변경과 코드가 «함께» 반영된다. `/healthz`, `/readyz`,
   관리자 로그인, 비관리자 차단, MEMBER/LINK 생성 차단을 먼저 확인한다.
9. 작은 회사 1건을 실제 조사해 DART·뉴스·홈페이지 수집, 화면/PDF 정본 게이트, 비용 기록,
   재시작 뒤 결과 보존을 확인한 다음 회사 수를 늘린다.

외부 사용자, LINK 공유, MEMBER 초대, Notion, S3 외부 백업과 cron은 이 순서에 포함하지
않는다. 특히 `BACKUP_S3_BUCKET`을 미리 넣으면 준비되지 않은 adapter 때문에 시작 검증이
의도적으로 실패한다.

## 필수·조건부 환경변수

| 이름 | 배포 값과 보관 원칙 |
|---|---|
| `PIPELINE` | 관리자 실분석 운영판은 `real` |
| `BETA_ADMIN_ONLY` | `1`. 일반 사용자·MEMBER·LINK를 열지 않음 |
| `DEPLOYMENT_RUNTIME_CONTRACT` | `render-admin-real-no-forwarded-v1` |
| `ADMIN_EMAILS` | 관리자 Google 이메일. 여러 명이면 쉼표로 구분 |
| `GOOGLE_CLIENT_ID` | 배포용 Google OAuth 클라이언트 ID |
| `GOOGLE_CLIENT_SECRET` | 비밀 관리자에 보관하는 OAuth 비밀 |
| `PUBLIC_ORIGIN` | Blueprint가 `RENDER_EXTERNAL_URL`을 self-reference해 고정하는 HTTPS origin |
| `GOOGLE_REDIRECT_URI` | 정확히 `<PUBLIC_ORIGIN>/auth/callback` |
| `FORWARDED_ALLOW_IPS` | 빈 값. 관리자 no-forwarded 계약에서는 proxy headers를 신뢰하지 않음 |
| `PDF_RELEASE_PARTICIPANTS` | 구형 수동 승인 감사자료를 해석해야 할 때만 복구하는 과거 역할 JSON. 신규 출고 권한 아님 |
| `PROVENANCE_SEAL_SECRET` | 모든 재배포·worker·복구에서 동일하게 유지할 32바이트 이상의 무작위 비밀 |
| `ANTHROPIC_API_KEY` | `PIPELINE=real` 필수. 생성 모델 provider 비밀 |
| `DART_API_KEY` | `PIPELINE=real` 필수. 전자공시 provider 비밀 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | `PIPELINE=real` 필수. 뉴스 검색 provider 비밀 |
| `NOTION_TOKEN` / `NOTION_PARENT_PAGE_ID` | 자동출고 완료 보고서를 Notion으로 보낼 때만 |
| `BACKUP_DATA_BOUNDARY_ID` | DB/sidecar 저장소의 불변 경계 식별자. 실제 값은 환경에만 주입 |
| `BACKUP_DATA_AUTHORITY_ID` | DB/sidecar 쓰기 주체의 불변 식별자. 실제 값은 환경에만 주입 |
| `BACKUP_MANIFEST_MIN_RETENTION_DAYS` | 독립 manifest 최소 보존일. DB 백업 보존일 이상으로 명시 |
| `ENGINE_V2` | **`1`. 배포하면 composer(v2) 경로로 간다.** 정확히 `1`일 때만 v2다. → 아래 «엔진 v2 스위치» 참고 |
| `REPORT_RELEASE_MODE` | **`SHADOW`.** 실제 모델 제한 실측 전에는 새 품질 판정을 기록만 하고 출고를 바꾸지 않음. `ENFORCE_NO_PARTIAL`·`FULL` 전환은 별도 승인 |

### 엔진 v2 스위치 (`ENGINE_V2`)

**지금 배포하면 v2 보고서가 나간다.** `render.yaml`의 `envVars`에
`ENGINE_V2: "1"`이 들어 있다. 코드는 `os.environ.get("ENGINE_V2") == "1"`
하나로만 갈린다(`app/src/features/pipeline/real.py`).

- **켜져 있는 곳은 `render.yaml` 한 곳뿐이다.** `app/Dockerfile`의 기본값에는
  일부러 넣지 않았다. 이미지 기본값은 `PIPELINE=demo`처럼 «돈이 들지 않는
  안전한 상태»만 담고, 실제로 v2를 켤지는 배포 manifest가 한 곳에서 정한다.
  덕분에 로컬 smoke 컨테이너(`scripts/deploy/smoke-container.ps1`)와
  `deploy/compose.yaml`·`deploy/kubernetes/`는 v1 그대로 남는다.
- **값을 잘못 적으면 시작이 거부된다.** 코드는 값이 «정확히 `1`»일 때만
  v2로 가므로, `true`·`yes`·`on`·앞뒤 공백이 붙은 `" 1 "`은 원래
  «조용히» v1로 떨어졌다. 화면에는 평소대로 보고서가 떠서 사람이
  눈치챌 방법이 없었다.
  지금은 시작 검증(`deploy/validate_environment.py`)이 `1`과 `0` 외의
  값을 거부한다 — 컨테이너가 아예 안 뜬다. **값을 아예 안 넣는 것은
  오류가 아니다**(v1을 쓰겠다는 정상적인 선택이다).
  지키는 시험: `deploy/tests/test_deployment_contract.py`의
  `test_engine_v2_rejects_values_that_silently_fall_back_to_v1`
  (값의 글자·자료형은 같은 파일의
  `test_render_blueprint_turns_engine_v2_on_while_image_default_stays_v1`).
- **되돌리는 방법**: `render.yaml`에서 `ENGINE_V2` 한 쌍을 지우고 재배포한다.
  값을 `0`으로 바꿔도 v1로 가지만, 위 시험이 실패해 의도를 되묻게 된다.

  ```yaml
      - key: ENGINE_V2
        value: "1"
  ```

- **켜면 달라지는 것**: 보고서 본문을 composer가 새로 쓰고, 2·4·7장에 도식이
  실린다. v2 전용 1층 캐시는 배포 revision·생성기 코드 지문·최신 사업연도뿐
  아니라 실제 DART 접수번호와 정규화한 재무 응답 지문까지 모두 같을 때만
  재사용한다. 코드·사업연도·공시·재무값 중 하나라도 바뀌면 옛 결과를 내지 않고
  새로 조사하며, 모두 같으면 캐시 적중으로 본조사 비용을 다시 쓰지 않는다.
- **품질 운영모드는 따로 명시한다.** 실제 분석에서 v2를 켰는데
  `REPORT_RELEASE_MODE`가 빠지면 앱은 AI를 부르기 전에 시작을 거부한다. 현재
  `render.yaml`은 안전한 관찰 모드인 `SHADOW`를 명시하며, 시작 검증은 정확한
  `SHADOW`·`ENFORCE_NO_PARTIAL`·`FULL` 외의 오타와 공백을 거부한다. 실제 모델을
  부르는 제한 실측 없이 `FULL`로 바꾸지 않는다. `SHADOW`는 새 품질 규칙의
  제품 적용이 끝났다는 뜻이 아니다.
- **로컬에서 켜 보기** — 두 실행기가 `ENGINE_V2`를 자식에게 넘긴다.
  - `app/실시간성능시험켜기.ps1 -EngineV2` — 로그인 게이트를 «끈» 채
    조사 흐름만 본다. 관리자 화면은 못 본다.
  - `app/배포리허설켜기.ps1` — 배포와 «같은 조건»(관리자 게이트 켬)으로
    켠다. v2가 기본이고 `-DisableEngineV2`로 끌 수 있다.
    준비물과 절차는 `app/docs/배포리허설_사용법.md`를 따른다.

`AUTH_COOKIE_INSECURE`와 로컬 관리자 capability는 로컬 전용이다. Render에는 설정하지 않는다.
실제 값의 형식은 `app/.env.example`의 설명을 따르되 실제 사람의 `sub`나 비밀값을 파일에
복사하지 않는다. Notion·외부 백업처럼 보류한 변수는 미리 넣지 않는다.

### provenance 비밀 준비

첫 관리자 demo에서 Blueprint가 `PROVENANCE_SEAL_SECRET`을 자동 생성했다. 실분석 운영판으로
올릴 때 기존 실행 기록을 유지해야 하면 같은 값을 보존한다. Render 비밀 관리 화면에서
값의 존재를 확인하고 승인된 비밀 관리자에는 소유자·마지막 검증일·복구 담당자를 기록한다.
채팅·문서·저장소에는 값을 복사하지 않는다.

## 주소와 Google OAuth 연결

1. 첫 관리자 demo에서는 Render가 발급한 고정 `https://...onrender.com` 주소를 확정한다.
2. Google Cloud의 승인된 리디렉션 URI에
   `https://<service-host>/auth/callback`을 등록한다.
3. 자동 고정된 `PUBLIC_ORIGIN`을 확인하고 `GOOGLE_REDIRECT_URI`만 같은 host 기준으로
   입력한 뒤 재배포한다.
4. OAuth 동의 화면이 Testing 상태라면 운영 관리자를 테스트 사용자에 넣는다.
5. `/healthz`와 `/readyz` 응답, HTTPS, 로그인 callback, 관리자 허용·비관리자 거절을
   확인한다.

서비스 이름이 바뀌면 self-reference가 만든 주소를 확인하고 `GOOGLE_REDIRECT_URI`와 Google
등록 URI를 함께 바꾼다. 사용자 도메인은 첫 배포 뒤 정식 공개 운영 계약에서 별도로 연다.

## 데모에서 먼저 확인할 것

`PIPELINE=demo`와 `BETA_ADMIN_ONLY=1`에서는 저장된 데모 자료만 사용하므로 외부 조사
API 비용이 발생하지 않는다. 다음을 확인한다.

- 회사명만 입력해 후보 확인부터 결과까지 완료
- 선택 주소 힌트를 넣었을 때 동일 회사로 식별
- 비관리자 접근 차단과 공유 링크 발급 불가
- 정본 화면과 PDF 내용 일치
- 자동검사 실패 시 웹·PDF 출고 차단, 수동 승인 GET/POST 410

`BETA_ADMIN_ONLY=0`은 운영 승인과 공개 전 체크리스트를 마친 뒤에만 사용한다.

## 자동출고와 Notion 전송

활성 출고 순서는 다음 하나뿐이다.

```text
report_standard 통과
  → report/PDF/모든 페이지 PNG 해시 고정
  → 사실·인용·수치·구조·금지 문구·PDF 렌더·채널 동등성 자동검사
  → 검사 전후 해시 재대조와 자동출고 레코드 확정
  → PDF 다운로드 허용
  → 설정된 경우 같은 정본을 Notion으로 전송
```

- 검사 하나라도 실패하거나 검사 뒤 해시가 바뀌면 같은 작업을 재승인하지 않고 전체를 `GATE_STOPPED`한다.
- 구형 참여자·세 승인 원장은 감사자료로 보존하지만 신규 출고 권한이 아니다.
- `/review/pdf/*` GET/POST는 410이며 Notion은 별도 내용을 생성하는 채널이 아니다.

## 실제 조사로 전환

현재 `render.yaml`은 관리자 실분석 운영판을 위해 `PIPELINE=real`로 준비되어 있다. 실제
배포 버튼을 누르기 전 사용자가 Render 플랜·디스크 비용과 외부 provider 호출 비용을
승인하고, 네 provider 비밀과 애플리케이션 예산 한도를 확인해야 한다. 작은 회사 입력 한
건으로 DART·뉴스·홈페이지 수집, 정본 게이트, 자동출고 흐름을 끝까지 시험한 뒤 범위를
늘린다.

실제 조사도 입력 계약은 회사명과 선택 주소뿐이다. 개인정보나 채용공고 원문을 넣지
않는다. Google Places 후보 검색은 결과 보관·표시 약관 검토가 완료될 때까지 활성화하지
않는다. 이 단계에서는 `BETA_ADMIN_ONLY=1`을 풀거나 MEMBER/LINK를 열지 않는다.

## 데이터 백업과 비밀 복구

현재 `render.yaml`은 `/var/data` 1GB 영속 디스크를 붙여 SQLite·보고서·감사 기록을
재시작과 재배포 뒤에도 보존한다. 다만 S3 외부 백업이나 backup/maintenance cron은 아직
없으며 관련 환경변수를 설정하지 않는다.

현재 Docker의 SQLite는 WAL-reset 수정 전 계열이면 `DELETE` journal로 내려가며, 모든
쓰기 연결은 `synchronous=EXTRA`를 강제한다. 이는 유료 비용·승인 출고 원장의 마지막
commit이 전원 중단 직후 되돌아가지 않도록 rollback-journal 삭제 디렉터리까지 동기화하는
계약이다. 성능을 이유로 FULL/NORMAL/OFF로 낮추지 않는다. 실행 중 DB 파일이나 identity가
사라지면 빈 DB 자동 생성 대신 서비스가 닫힌다. 검증된 완전 DB를 새 경로/새 디스크에
준비해 전환하며 운영 파일을 실행 중 삭제·이동하지 않는다.

최초 승인 PDF 원본에는 `REPORT_ARTIFACT_CAPACITY_BYTES=536870912`(512MiB)를
따로 배정한다. 이 숫자는 운영 PDF 평균 크기를 실측해 산정한 보존량이 아니다. 1GB 한
장에 SQLite·감사 기록·캐시도 함께 있으므로 우선 절반을 다른 운영 자료의 여유로 남기는
보수적 배포 상한이다. 원본 저장소는 용량이 부족해도 **자동으로 과거 원본을 지우지
않는다.** 상한을 넘으면 이미 승인된 원본은 그대로 두고 **새 보고서 출고를 닫는다**
(fail-closed: 안전을 확인하지 못하면 공개하지 않는 동작). 관리자는 디스크 용량과 원본
증가량을 확인한 뒤 보존·이관 정책을 별도로 승인해야 하며, 설정값을 새 결과에 맞춰
늘리는 것을 장애 해결로 간주하면 안 된다.

출고·보존 정리·로컬 복구 세대 생성은 같은 artifact root 파일 잠금을 사용한다. 다른
thread/process가 잠금을 놓지 않으면 10초 뒤 `ArtifactRootBusy` 또는 백업 실패로
fail-closed하며 요청을 무기한 붙잡지 않는다. 이 실패를 보고 lock 파일이나 PDF를 손으로
지우지 말고, 점유 프로세스와 진행 중 작업을 먼저 확인한 뒤 안전하게 재기동한다.

로컬 도구는 이제 SQLite snapshot과 그 snapshot이 참조하는 최초 승인 PDF bytes를 한
복구 세대 manifest로 묶고 누락·변조·경로 공격을 dry-run에서 차단한다. 그러나 **이 복구
세대를 독립 외부 저장소에 올리고 다시 복구한 운영 증거는 아직 없다.** 아래 외부 백업
설계도 현재는 BLOCKED다. 따라서 지금 단계에서 이 원본을 영구 보관 또는 재해 복구
완료라고 부르지 않는다.

즉, **최초 승인 PDF 원본의 외부 백업은 아직 확인하지 못했다.**

Render 영속 디스크와 플랫폼 snapshot은 같은 플랫폼 경계 안의 보존 수단이며 독립 외부
백업 완료를 뜻하지 않는다. 아래 구조는 운영 adapter를 구현한 뒤의 후속 설계다. Render
cron job은 다른 서비스의 영속 디스크를 읽을 수 없으므로 웹 프로세스가 다음 구조로 매일
외부 백업한다.

```text
Render cron
  → POST https://<웹 주소>/internal/backup/run (Bearer 비밀)
  → 웹 프로세스가 DB snapshot+그 DB가 참조하는 PDF bytes를 한 recovery generation으로 봉인
  → 비공개 S3 호환 bucket에 generation manifest와 DB/PDF object set 전부 업로드
  → 세대 전부를 다시 내려받아 hash·길이·경로·SQLite integrity·외래키·완전성 검사
  → 다른 권한·보존 경계의 서명 append-only manifest에 세대 ID와 모든 원격 객체 지문 append
  → manifest chain/head·서명·세대 object set 전체 결속 read-back 재검증
  → 위 단계가 모두 끝난 뒤에만 성공 반환 및 과거 백업 정리
```

향후 추가할 백업 cron의 기준 시각은 매일 `19:00 UTC`, 한국 시각 다음 날 `04:00`이다.
원격 object key 형식과 서명 wire schema는 recovery generation의 가변 PDF object 목록을
결속하도록 새로 정해야 한다. 과거 `storage-backup-...sqlite3`와 `.sha256` 두 객체 계약은
새 보고서 복구에 불완전하므로 재사용하지 않는다. 성공 응답은 세대 전체 재검증과 독립
manifest append/read-back을 모두 마친 뒤에만 반환되며 manifest backup ID·sequence·record
hash를 포함해야 한다.

**현재 외부 백업 배포는 BLOCKED다.** 저장소에는 production-ready
recovery-generation object set을 지원하는 `BackupManifestAppender` 구현, signer, 독립
sink, 앱 시작 시 provider 설치 호출, 최신 checkpoint 공급 경로가 없다. 현재
`BackupManifestRequest` v2와 시험 mechanics는 DB 두 객체만 표현하므로 운영에 사용할 수
없다. S3 변수 세트만 채우면 되는 상태가 아니며, appender 누락 시
백업 경로와 cron은 성공을 반환하지 않는다. `deploy/validate_environment.py`도
`BACKUP_S3_BUCKET`이 설정된 배포를 현 상태에서 fail-closed한다. 웹 demo만 운영하려면
외부 백업 bucket을 활성화하지 않고 이 차단을 배포 예외로 오인하지 않는다.

따라서 현재 사람이 누를 수 있는 배포 버튼은 **웹 서비스와 같은 Render 디스크에 자료를
남기는 것까지만** 수행한다. 배포만 누르면 독립 재해 복구가 끝나는 상태가 아니다. 아래
adapter 구현과 계정·bucket·자격증명·cron·외부 경보 설정 및 실제 복구 훈련은 별도 사람
작업으로 남아 있다.

### 외부 adapter 구현 후 최초 한 번 설정

1. recovery-generation bucket 관리 주체와 다른 권한·보존 경계에 WORM/object-lock 또는 동등한
   append-only manifest sink를 구성하고 최소 35일 보존과 조건부 append(CAS)를 검증한다.
2. 서명 키는 조직 비밀 관리자에서 signer에 주입하고 key ID·회전·과거 서명 검증 정책을
   정한다. 실제 키 값이나 adapter 전용 변수명은 저장소 문서에 기록하지 않는다.
3. `BackupManifestAppender` 운영 구현을 만들고 앱 bootstrap에서
   `install_manifest_appender_provider(...)`를 한 번 호출한다. append 뒤 전체 chain/head를
   다시 읽어 검증하는 공격·재시작·동시 append 시험이 통과해야 한다.
4. S3 또는 S3 호환 공급자에 **비공개 bucket**을 만들고 공개 접근을 차단한다.
5. `company-analysis/` prefix에 `PutObject`, `GetObject`, `DeleteObject`, `ListBucket`만
   가능한 백업 전용 자격증명을 만든다. 다른 bucket 권한은 주지 않는다.
6. 웹 서비스 Environment에 아래 값을 실제 값 없이 등록한 뒤 비밀 관리자/플랫폼에서
   주입한다.
   - `BACKUP_S3_BUCKET`, `BACKUP_S3_REGION`
   - AWS S3가 아니면 공급자의 HTTPS `BACKUP_S3_ENDPOINT_URL`
   - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
   - `BACKUP_DATA_BOUNDARY_ID`, `BACKUP_DATA_AUTHORITY_ID`
   - `BACKUP_MANIFEST_MIN_RETENTION_DAYS`(DB 보존 기간 이상)
   - 기본 암호화는 `AES256`; KMS를 쓰면 `BACKUP_S3_SERVER_SIDE_ENCRYPTION=aws:kms`와
     `BACKUP_S3_KMS_KEY_ID`
7. cron의 `BACKUP_TRIGGER_URL`을 실제 공개 웹 주소의
   `https://<주소>/internal/backup/run`으로 넣는다. `BACKUP_TRIGGER_SECRET`은 Blueprint가
   웹에 자동 생성하고 cron이 같은 값을 참조하므로 사람이 복사하지 않는다.
8. 기존 Blueprint에 서비스를 추가하는 경우 새 `sync: false` 값은 자동 반영되지 않을 수
   있다. 웹의 bucket·자격증명과 cron URL이 실제 대시보드에 있는지 직접 확인한다.
9. Render 알림에서 `company-analysis-backup`의 실패 알림을 운영 수신처로 켠다. 별도로
   외부 저장소 또는 감시 도구에서 최신 복구 세대 전체와 독립 manifest checkpoint가 24시간 넘게
   함께 생성되지 않으면 알리도록 설정한다. 이 감시는 Render 장애로 cron 자체가 실행되지
   않는 경우까지 잡기 위해 Render 밖에 둔다.

### 주간 XLSX와 일일 정리(후속 운영)

Render cron은 웹 서비스의 영속 디스크를 직접 읽지 않는다. 백업과 같은 호출 구조로
`POST https://<웹 주소>/internal/maintenance/run`을 요청하며, 작업 종류는 고정 헤더로
전달한다. 웹 서비스만 SQLite를 열고 기존 claim과 append-only 성공·실패 사건을 남긴다.

- `company-analysis-weekly-xlsx`: 매주 일요일 `19:10 UTC`, 한국 시각 월요일 `04:10`.
  직전 완료 월~일의 관리자 전용 XLSX를 만들며 이메일·AI 호출은 없다.
- `company-analysis-daily-cleanup`: 매일 `19:20 UTC`, 한국 시각 다음 날 `04:20`.
  30일 경과 휴지통 보고서와 이전 KST 날짜에 멈춘 정기 작업만 정리하며 AI 호출은 없다.

두 cron의 `MAINTENANCE_TRIGGER_URL`을 실제 공개 웹 주소의
`https://<주소>/internal/maintenance/run`으로 설정한다. `MAINTENANCE_TRIGGER_SECRET`은
Blueprint가 웹에 자동 생성하고 두 cron이 같은 값을 참조하므로 사람이 복사하지 않는다.
동일 주·동일 날짜가 다시 호출되면 SQLite claim 때문에 새 작업을 만들지 않는다. 내부
실패 내용은 HTTP 응답에 노출하지 않고 대시보드 작업 사건에 실패로 남는다.

후속 Blueprint에 세 cron을 반영한 뒤 실제 운영 전에는 다음을 확인한다.

1. `BACKUP_TRIGGER_URL`과 두 cron의 `MAINTENANCE_TRIGGER_URL`이 정확한 HTTPS 내부 경로인지 확인한다.
2. 세 cron 실패 알림의 운영 수신처를 설정한다.
3. 각 cron을 한 번 수동 실행해 백업 재검증, XLSX 다운로드, 정리 작업 성공 사건을 확인한다.
4. 다음 예정 시각에 중복 파일·중복 정리 없이 한 번만 실행됐는지 확인한다.

S3 호환 공급자가 path-style 주소만 지원하면 `BACKUP_S3_ADDRESSING_STYLE=path`로 바꾼다.
기본 `auto`와 AWS 기본 endpoint에는 `BACKUP_S3_ENDPOINT_URL`을 넣지 않는다. URL에 접근키를
붙이지 않는다.

### 첫 실행과 다음 날 확인

위 BLOCKED 항목을 모두 닫은 뒤에만 Render 대시보드에서 cron을 한 번 수동 실행한다. 로그의
`외부 백업 완료`와 manifest backup ID·sequence·record hash가 함께 있는지 확인한다. 외부
bucket에서 한 복구 세대의 object set 전체를 내려받아 원래 디렉터리 구조로 둔 뒤 아래
로컬 dry-run을 수행해야 한다.

```console
python tools/backup_sqlite.py verify <내려받은-rg-세대-디렉터리>
```

이 명령은 DB와 PDF의 완전성까지 확인하지만, 같은 저장 경계의 로컬 manifest이므로 복구
진본성 증거로는 충분하지 않다.
S3와 다른 통제 경로에서 최신 sequence checkpoint를 가져와 승인된 adapter wrapper가
`ops/release_readiness.py`의 manifest gate와 임시 복구를 실행해야 한다. 직접 ops CLI는
sink/signer가 없으면 실패하는 것이 정상이다. 정확한 인자와 검증 절차는
[배포 운영 런북](../../ops/배포_운영_런북.md)의 `백업 무결성과 독립 서명 manifest`를
따른다. 다음 날에도 새 recovery generation과 checkpoint가 함께 생겼는지 확인하고 같은 gate를
통과시킨다. 하나라도 없거나 검증이 실패하면 성공으로 보지 않는다.

`python tools/backup_sqlite.py restore`와 공개 `restore_backup()`은 로컬 manifest만으로
운영 복구하는 우회를 막기 위해 현재 항상 실패하고 대상 DB·PDF를 만들지 않는다. 실제 새
DB와 artifact staging root 게시 및 `STORAGE_DB_PATH` 전환은 승인된 manifest adapter
wrapper가 구현된 뒤에만 한다.

외부 저장소가 아직 준비되지 않은 비상 상황에서만 Render Shell에서 아래 명령으로 일관성
있는 임시 백업을 만들 수 있다. 같은 디스크에만 남겨 두면 디스크 장애를 견디지 못한다.

```console
python tools/backup_sqlite.py backup
```

복구 세대에는 환경 비밀이 들어 있지 않으므로 다음 **비밀 복구 묶음**을 조직 비밀 관리자에 별도로
보관한다.

- Google OAuth ID·비밀과 승인 URI
- `SHARE_PUBLIC_BASE_URL`
- 구형 감사자료 해석이 필요하면 당시 `PDF_RELEASE_PARTICIPANTS`
- `PROVENANCE_SEAL_SECRET`
- real provider 키와 Notion 설정(사용하는 경우)
- 각 값의 소유자, 회전일, 마지막 복구 시험일

복구 영향은 다음과 같다.

- `PROVENANCE_SEAL_SECRET`을 잃거나 다른 값으로 복구하면 기존 provenance seal과 캐시를
  신뢰할 수 없어 기존 보고서 출고가 차단된다. 임시 새 키로 우회하지 말고 원래 값을 복구한다.
- `PDF_RELEASE_PARTICIPANTS`는 신규 자동출고에 사용하지 않는다. 구형 감사자료의 역할
  해석이 필요할 때만 당시 값을 복구한다.
- `SHARE_PUBLIC_BASE_URL`은 현재 검증 도메인에서 다시 정할 수 있지만, 누락·오설정
  동안 공개 공유 URL 발급을 중단하고 기존 링크의 host 영향을 점검한다.
- OAuth, provider, Notion 비밀이 유출되었거나 복구 여부가 불명확하면 먼저 회전하고
  callback·권한·전송을 다시 시험한다.

## 관리자 실분석 운영판 체크리스트

- [ ] GitHub Actions `quality-gate`와 관리자 실분석 환경 검증 통과
- [ ] `standard` web plan과 1GB 영속 디스크의 현재 Dashboard 청구 조건 승인
- [ ] `PIPELINE=real`, `BETA_ADMIN_ONLY=1`, instance/worker 각각 1개
- [ ] `render-admin-real-no-forwarded-v1`, 고정 `PUBLIC_ORIGIN`, 빈 `FORWARDED_ALLOW_IPS`
- [ ] 관리자·Google OAuth 4개 값과 실제 분석 provider 4개 비밀 등록
- [ ] `PROVENANCE_SEAL_SECRET` 32바이트 이상이며 재배포·복구용 동일 값 보존
- [ ] `autoDeployTrigger: off`와 Blueprint Auto Sync 비활성 확인 뒤 수동 배포
- [ ] `/healthz`, `/readyz`, 관리자 로그인·비관리자 차단 확인
- [ ] MEMBER 초대와 LINK 공유 생성 차단 확인
- [ ] 작은 회사 1건의 실제 조사·정본 게이트·PDF·비용 기록 통과
- [ ] 재시작 뒤 SQLite·보고서·감사 기록이 `/var/data`에서 유지됨을 확인
- [ ] S3/cron 변수 미설정 및 독립 외부 백업 BLOCKED 상태를 운영자에게 고지

이 체크리스트를 완료하면 관리자가 여러 회사의 실제 결과를 비교할 수 있다. 일반
사용자에게 공개하거나 독립 재해복구를 완료했다는 뜻은 아니다.

## 정식 일반 공개 운영 전 체크리스트

아래 항목은 관리자 demo 첫 배포를 끝내기 위한 조건이 아니라, 보류 기능과 현재 HOLD를
해결한 뒤 일반 사용자에게 공개하기 위한 조건이다. 현재 forwarded evidence verifier와
독립 외부 백업 adapter가 없으므로 이 단계는 BLOCKED다.

- [ ] GitHub Actions `quality-gate`와 Docker `/readyz` 통과
- [ ] `PIPELINE=demo`, `BETA_ADMIN_ONLY=1`에서 첫 검증
- [ ] 회사명 단독 입력과 선택 주소 입력 모두 통과
- [ ] 관리자·비관리자·공유 링크 권한 확인
- [ ] 필수 자동검사 전 항목 통과 시 같은 hash로 웹·PDF·Notion 자동출고
- [ ] 검사 실패·검사 후 hash 변경 시 세 채널 전체 `GATE_STOPPED`
- [ ] 수동 승인 GET/POST가 410이고 구형 기록으로 우회 불가
- [ ] 실패 고객 청구 0원과 실패 provider 내부 AI 원가 보존
- [ ] SQLite 백업과 SHA-256 검증 완료
- [ ] production manifest appender·독립 sink/signer·최신 checkpoint 경로 구성 증거 확보
- [ ] S3 외부 백업을 수동 실행하고 exact object 결속 manifest gate·임시 복구 통과
- [ ] 35일·35개 보관 정책과 24시간 미생성 외부 알림 수신처 확인
- [ ] 비밀 복구 묶음의 소유자와 복구 시험일 확인
- [ ] 정식 베타 전환 시 worker `1`, instance `1`, 영속 디스크 `/var/data` 유지
- [ ] real 전환 시 provider 비용과 예산 한도 별도 승인

공식 참고: [Render Blueprint](https://render.com/docs/blueprint-spec),
[Render 요금](https://render.com/pricing),
[Render 무료 웹서비스](https://render.com/docs/free),
[Render 영속 디스크](https://render.com/docs/disks),
[Render cron job](https://render.com/docs/cronjobs),
[Render SSH](https://render.com/docs/ssh),
[Render 상태 확인](https://render.com/docs/health-checks),
[Google OAuth 웹 서버](https://developers.google.com/identity/protocols/oauth2/web-server)
