# 클라우드 중립 배포 계약

이 디렉터리는 특정 사업자의 배포 기능을 호출하지 않는다. 하나의 OCI 이미지가 Docker
Compose와 Kubernetes에서 같은 비-root 계정, 상태 확인 경로, 영속 경로를 사용한다.

## 고정 계약

- 프로세스 UID/GID는 `1000:1000`이며 root 실행과 권한 상승을 거절한다.
- `/healthz`는 liveness, `/readyz`는 SQLite·로그인 설정 readiness다.
- SQLite 관측 정본, 전환 전 JSONL 호환 사본, tldextract 캐시는 모두 `/var/data`
  영속 볼륨 아래에 둔다. 새 비용 원장 전환 뒤 관측·비용 검증은 SQLite만 읽는다.
- 앱의 작업 드레인 상한은 240초다. Uvicorn은 HTTP task를 먼저 기다린 뒤
  lifespan 종료를 부르므로 이 두 시간은 겹치지 않고 더해진다. 모든 배포에서
  Uvicorn HTTP 정리는 20초로 고정하므로 앱이 기대하는 종료 시간은
  20초·240초·취소 1초를 더한 261초다. Compose와 Kubernetes는 330초를 주므로 그 안에
  끝난다. Render는 영속 디스크가 붙은 서비스에 `maxShutdownDelaySeconds`를 쓸 수
  없어 종료 유예가 플랫폼 기본 30초이며, 배포·재시작 중이던 조사는 정리를 끝내기
  전에 잘릴 수 있다. 디스크를 떼면 이 제약도 사라지지만 SQLite·보고서·감사 기록을
  잃으므로 디스크 쪽을 남긴다.
- 애플리케이션·Uvicorn 로그는 stdout/stderr로만 보낸다. Compose의 로컬 로그 회전은
  10MB × 5개이며, 운영 플랫폼에서는 표준 출력 수집기를 연결한다.
- 컨테이너 루트 파일시스템은 읽기 전용으로 쓸 수 있다. 쓰기 경로는 `/var/data`와
  메모리·임시 PDF 작업용 `/tmp`뿐이다.
- SQLite 단일 writer 계약 때문에 replica와 worker는 각각 1개다. Kubernetes 갱신 전략은
  `Recreate`다.

## Render 배포 계약 세 가지

Render에는 forwarded client IP를 신뢰하지 않는 좁은 계약이 세 개 있다. 셋 다
`BETA_ADMIN_ONLY=1`, web service/instance/worker 각각 1개, 고정 `PUBLIC_ORIGIN`, 빈
`FORWARDED_ALLOW_IPS`를 강제한다. 갈리는 것은 초대 링크 발급·회원 초대·`/k/` 입구를
여는지다. 앞의 두 계약은 이 셋을 모두 닫아 관리자 본인의 로그인·분석만 허용하고,
세 번째 계약만 초대받은 사람이 링크·QR로 보고서를 여는 입구를 연다.

| 동작 | `render-admin-demo-no-forwarded-v1` | `render-admin-real-no-forwarded-v1` | `render-portfolio-link-v1` |
|---|---|---|---|
| `PIPELINE` | `demo`(외부 provider 호출 없음) | `real` | `real` 필수 |
| 관리자 로그인·분석 | 허용 | 허용 | 허용 |
| `/admin/links/new`(초대 링크 발급) | 차단(404) | 차단(404) | 허용 |
| `/admin/invite`(회원 초대) | 차단(409) | 차단(409) | 허용 |
| `/admin/members/{email}/limit`(회원 한도) | 차단(409) | 차단(409) | 허용 |
| `/k/` 링크 입구 | 로그인 화면으로 이동 | 로그인 화면으로 이동 | 열림 |
| `ENGINE_V2` | 배포자가 값 선택 | 배포자가 값 선택 | `1` 필수 |
| `REPORT_RELEASE_MODE` | 배포자가 값 선택 | 배포자가 값 선택 | `SHADOW`·`ENFORCE_NO_PARTIAL`·`FULL` 중 하나 필수 |
| 고정 HTTPS origin·forwarded 비신뢰 | 강제 | 강제 | 강제 |

★ 「초대 명단에 있는 회원이 로그인 벽을 통과한다」는 `BETA_ADMIN_ONLY=1`인 모든 배포에
계약과 무관하게 적용된다. 로그인 벽은 «누가 통과하는가»의 축이고 runtime contract는
«어느 forwarded-header 신뢰 모델을 쓰는가»의 축이라 서로 다른 문제이기 때문이다. 초대 링크
발급·회원 초대·`/k/` 입구만 계약에 따라 갈린다.

### 무료 관리자 demo

`render-admin-demo-no-forwarded-v1`은 기존 무료 동작 확인판이다.

- `PIPELINE=demo`이며 외부 조사 provider를 호출하지 않는다.
- 무료 인스턴스의 `/var/data`는 영속 저장소가 아니다. 잠듦·재시작·재배포 때 SQLite와
  실행 이력이 사라질 수 있다.
- 초대 링크, 회원 초대, 실제 provider, Notion, S3 외부 백업과 cron을 활성화하지 않는다.

### 실제 분석 운영판 두 계약의 공통 요구

`render-admin-real-no-forwarded-v1`과 `render-portfolio-link-v1`은 둘 다 실제 provider를
부르는 유료 운영판이며 아래 요구를 그대로 공유한다. demo가 아니다.

- Render `standard` web plan과 `/var/data`에 붙는 1GB 영속 디스크를 사용한다. 실제
  DART 118,747사 후보 색인이 Starter의 512MB를 넘어 `/confirm` 중 인스턴스가
  재시작된 운영 측정에 따른 최소 사양이다. 적용 직전 [Render 요금 페이지](https://render.com/pricing)와
  Dashboard의 예상 청구액을 다시 확인한다. 플랜·요금 숫자는 이 문서에 고정하지 않는다.
- `PIPELINE=real`, `BETA_ADMIN_ONLY=1`, instance/worker 각각 1개를 유지한다. SQLite 단일
  writer 계약 때문에 scale-out하지 않는다.
- `ADMIN_EMAILS`, Google OAuth 3개 값과 함께 `ANTHROPIC_API_KEY`, `DART_API_KEY`,
  `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 네 provider 비밀을 Render 환경변수로만 주입한다.
  실제 값은 저장소·문서·로그에 남기지 않는다.
- `PROVENANCE_SEAL_SECRET`은 32바이트 이상이어야 하며 재배포 뒤에도 같은 값을 보존한다.
- `PUBLIC_ORIGIN`은 Blueprint가 web service의 `RENDER_EXTERNAL_URL`을 self-reference해
  고정한다. `GOOGLE_REDIRECT_URI`는 정확히 `<PUBLIC_ORIGIN>/auth/callback`이어야 한다.
- `autoDeployTrigger: off`이므로 코드 push나 Blueprint 변경만으로 운영판이 배포되지 않는다.
  비용·비밀값·환경 검증 뒤 Render Dashboard에서 수동 배포한다.
- 영속 디스크는 재시작·재배포 뒤 데이터를 보존하지만 독립 외부 백업은 아니다. 현재 S3
  외부 백업 adapter와 cron은 BLOCKED이므로 관련 변수를 설정하지 않는다.

### 초대 링크 공개판 — 현재 `render.yaml` 값

현재 `render.yaml`의 `DEPLOYMENT_RUNTIME_CONTRACT`는 `render-portfolio-link-v1`이다. 위
공통 요구를 그대로 지키면서 초대 링크 발급·회원 초대·`/k/` 입구만 추가로 연다.

- `PIPELINE=real`, `BETA_ADMIN_ONLY=1`, 빈 `FORWARDED_ALLOW_IPS`를 이 계약에서도 강제한다.
- `ENGINE_V2`가 정확히 `"1"`이어야 하고 `REPORT_RELEASE_MODE`가 세 값 중 하나로 명시돼야
  한다. 둘 중 하나라도 빠지면 시작 검증이 컨테이너를 거절한다. 손님이 실제로 보는 화면이라
  조용히 옛 엔진이나 미정 상태로 열리지 않게 한다.
- `PUBLIC_ORIGIN`은 Render가 주는 외부 URL과 정확히 같아야 하고 `GOOGLE_REDIRECT_URI`는
  그 origin의 `/auth/callback`이어야 한다.

세 좁은 계약은 forwarded client IP를 사용하지 않기 때문에 아래 일반 공개 reverse proxy
gate의 승인 증거를 요구하지 않는다. 조건 하나라도 달라지면 예외가 아니며 fail-closed한다.
실제 분석 운영판 두 계약도 일반 공개 승인이나 독립 백업 완료를 뜻하지 않는다. 영속 디스크
동작과 제한은 [Render 영속 디스크 문서](https://render.com/docs/disks)를 따른다.

### 선언하지 않는 것이 꺼짐인 스위치

아래 환경변수는 `render.yaml`에 **키를 적지 않는 것**이 「꺼짐」이다. 값을 `"0"`으로
적어 두는 것도 금지는 아니지만, 키가 있으면 나중에 누가 `"1"`로 바꾸기가 너무 쉬워진다.
켤 때는 정확히 `"1"`이어야 하며, 프로세스가 시작할 때 한 번 읽고 그대로 굳는다.

| 환경변수 | 켜면 달라지는 것 | 되돌리는 법 |
|---|---|---|
| `TYPED_DART_COLLECTOR` | DART 수집을 typed 수집기로 바꾼다. 실제 문서로 검증된 적이 없다. | 키를 지우고 재배포 |
| `REVENUE_TABLE_V2` | 매출 구성표를 「제목 목록」이 아니라 「표 모양」으로 찾는다. 표가 나오는 회사가 늘어난다. | 키를 지우고 재배포 |

`REVENUE_TABLE_V2`를 끄면 파서는 표제 목록(`제품별 매출액`·`지역별 매출액` 등)으로만
표를 찾는 옛 경로로 돌아간다. 코드를 되감을 필요가 없고, 이미 만들어진 보고서도
바뀌지 않는다. 표는 AI 프롬프트에 들어가지 않으므로 켜고 꺼도 조사 비용은 그대로다.

## 공개 reverse proxy gate

로컬 Compose는 `DEPLOYMENT_EXPOSURE=local`, `DEPLOYMENT_PLATFORM=local`,
`DEPLOYMENT_RUNTIME_CONTRACT=local-web-v1`과 loopback forwarded 신뢰만 허용한다.
공개 배포는 `public`을 명시해야 하며 다음 증거가 없으면 entrypoint가 웹 시작을 차단한다.
환경의 SHA-256은 canary artifact 식별자일 뿐 신뢰 증명이 아니다. 서명 artifact 원문과
고정 policy를 검증할 운영 adapter가 아직 없으므로
`PRODUCTION_FORWARDED_EVIDENCE_VERIFIER_AVAILABLE=False`이고 위 세 no-forwarded 계약을
제외한 일반 public 설정은 값을 전부 채워도 BLOCKED다.

- 공통: 실제 공개 HTTPS origin에서 CSRF 보호 POST 성공·타 origin 거부 canary와,
  서로 다른 외부 주소가 앱에서 서로 다른 client IP로 관측되는 canary
- Render: 직접 origin 접근 차단과 edge가 외부 X-Forwarded-For를 제거·재작성한 증거.
  다만 현재 공식 고정 ingress peer CIDR 계약이 없으므로 Render public forwarded trust는
  명시적으로 unsupported/BLOCKED다. outbound IP를 inbound proxy peer로 간주해서는 안 된다.
- Kubernetes: Uvicorn 신뢰 CIDR과 정확히 같은 ingress CIDR, 그 CIDR/포트만 허용하는
  NetworkPolicy의 적용 증거

Kubernetes는 향후 signed canary verifier가 설치되면 후보가 될 수 있지만 지금은 역시
BLOCKED다. `FORWARDED_ALLOW_IPS=*`, loopback·예약 주소, IPv4 `/24`보다 넓거나 IPv6 `/64`보다
넓은 범위는 공개 모드에서 거부한다. `kubernetes/base.yaml`의 TEST-NET 값과 빈 증거는
의도적인 fail-closed placeholder이므로 실제 ingress 값과 증거로 교체해야 한다.

초대 링크의 요청 제한은 요청자를 식별하지 않고 링크 하나마다 60초에 60회다. 요청자 IP를
수집·집계하지 않으므로 forwarded 신뢰 여부가 이 한도의 정확도를 바꾸지 않는다. 대신
같은 링크를 여러 사람이 동시에 열면 그 60회를 함께 쓴다.

이 Render 판정은 [Render 환경변수 문서](https://render.com/docs/environment-variables)의
Docker/All runtimes 범위와 [Uvicorn proxy 설정](https://www.uvicorn.org/settings/)의
기본 loopback·명시 IP/IP Network 신뢰 계약을 따른다. Python native 전용 기본값이나
outbound 주소를 Docker 컨테이너가 실제로 보는 ingress peer 주소로 추정하지 않는다.

`RENDER_SERVICE_TYPE=web`, `RENDER_EXTERNAL_URL` 또는 Render hostname marker가 보이면
`DEPLOYMENT_PLATFORM=render`와 `DEPLOYMENT_EXPOSURE=public`을 강제한다. 모든 Render
runtime에 공통인 `RENDER=true` 단독은 web 충분조건이 아니며 cron/background worker를
web으로 승격하지 않는다. 그 상태의 검증되지 않은 generic command는 그대로 BLOCKED하고,
알려진 trigger module만 trigger 전용 검증을 유지한다. `KUBERNETES_SERVICE_HOST`/`PORT`,
`KUBERNETES_PORT` 또는 projected
service-account marker가 보이면 같은 방식으로 `kubernetes`/`public`을 강제한다. 따라서
플랫폼 안에서 `local`이라고 자기선언해 public gate를 우회할 수 없다. 다만 marker는
플랫폼이 이름을 바꾸거나 주입을 생략하면 완전하지 않으며, 공개성의 충분한 증명이나 승인
근거가 아닌 방어심층 신호일 뿐이다.

Render의 명시적 runtime contract, `RENDER=true`, 실제 web marker,
`render`/`public` 선언이 모두 일치하는데 Kubernetes marker도 보이는 경우에만 그 흔적을
Render 내부 substrate로 취급한다. 하나라도 빠지거나 Kubernetes contract와 충돌하면 기존처럼
fail-closed한다. 이 예외는 플랫폼 판정에만 적용되며 세 no-forwarded 계약의 고정 HTTPS
origin, 기본 실행 명령, forwarded header 비신뢰 검증을 생략하지 않는다.

entrypoint는 사용자 CMD의 문자열보다 manifest가 직접 고정한 runtime contract와 플랫폼
marker를 먼저 판정한다. Compose는 `local-web-v1`, 무료 관리자 demo는
`render-admin-demo-no-forwarded-v1`, 관리자 실제 분석 운영판은
`render-admin-real-no-forwarded-v1`, 초대 링크 공개판은 `render-portfolio-link-v1`,
일반 Render web은 `render-public-web-v1`, Kubernetes Deployment는
`kubernetes-public-web-v1`을 사용한다.
따라서 CMD가 `src.web.main:app`을 포함하지 않거나 trigger처럼 꾸며져도 contract에 맞는
검증을 거친다. 알 수 없는 contract와 contract 없는 generic command는 exit 78로 닫힌다.
세 no-forwarded 예외가 아닌 public contract는 독립 canary verifier 부재
상태에서 계속 BLOCKED다.

Kubernetes base는 `enableServiceLinks=false`와
`automountServiceAccountToken=false`이므로 서비스 환경변수와 service-account 파일 marker가
정상적으로 전혀 없을 수 있다. 이 때문에 Deployment container의 직접 `env`가
`kubernetes-public-web-v1`을 고정하며, 이 값은 ConfigMap/Secret의 `envFrom`보다 우선한다.
이 직접 contract를 제거하고 marker까지 비우면 generic readiness는 unsupported/BLOCKED다.
반대로 marker가 보이는 것만으로 공개 승인을 만들지는 않는다.

Compose는 `127.0.0.1:${HOST_PORT}:10000` loopback bind만 release 계약으로 고정한다.
일반 `docker run -p 0.0.0.0:...` 또는 다른 orchestrator의 공개 port는 marker로 확실히
감지할 수 없고, 이미지 자체에는 위 runtime contract의 공개 승인 기본값도 없다. 별도 공개
플랫폼 contract 없이는 public readiness를 얻지 못하며 BLOCKED다.

PDF는 고정된 `reportlab`, `pypdf`, `pdfplumber`, `pypdfium2` 패키지와 이미지에 포함된
Freesentation 글꼴을 쓴다. LibreOffice나 브라우저 런타임은 필요하지 않다. PDF 렌더의
일시 파일을 위해 `/tmp` 256MiB 이상, 실제 서비스에는 메모리 2GiB 상한을 권장한다.

## 로컬 빌드와 smoke

아래 명령은 push·배포를 하지 않는다. 빌드는 저장소 allowlist context만 이미지에 넣고,
smoke는 `PIPELINE=demo`, `--network none`으로 실행해 유료·외부 API 호출을 구조적으로 막는다.

```powershell
./scripts/deploy/build-image.ps1
./scripts/deploy/smoke-container.ps1 -SkipBuild
```

Docker Compose를 쓸 때는 `runtime-config.example`을 `runtime-config`로 복사하고 실제 값은
로컬 파일이나 비밀 관리 도구에서 주입한다. `runtime-config*` 실파일은 Git과 이미지
context에서 제외된다.

```powershell
Copy-Item deploy/runtime-config.example deploy/runtime-config
docker compose -f deploy/compose.yaml config
docker compose -f deploy/compose.yaml up --build
```

## 이미지 공급망 release gate

`build-image.ps1`은 로컬 smoke용 단일 플랫폼 이미지만 만들며 공개 배포 승인을 만들지
않는다. 공개 릴리스는 최종 multi-arch OCI index digest를 대상으로 아래 증거를 별도
JSON 묶음으로 보관하고 `validate-release-evidence.ps1`을 통과해야 한다.

- linux/amd64와 linux/arm64 child digest를 포함한 final index digest
- Trivy, Docker Scout 또는 Grype의 final index 대상 raw/검증 보고서. reachable
  high/critical은 0이어야 하며, 남은 finding은 고정 policy의 승인자와 `not_affected`
  VEX artifact가 정확히 대응해야 한다.
- 같은 index digest에 결속된 SPDX/CycloneDX SBOM과 `mode=max` provenance
- policy에 고정된 builder와 Ed25519 공개 키 SPKI SHA-256으로 검증한 서명 bundle

CLI 호출자가 policy 경로·hash를 같이 골라 self-pin할 수 없도록 validator는 오직 보호된
repository의 `deploy/release-policy.json`과 `deploy/release-policy.sha256`만 읽는다. 현재는
실제 policy JSON이 없고 pin 파일도 `BLOCKED`이며, raw scanner/SBOM/provenance/서명 형식을
검증하는 concrete `ReleaseArtifactVerifier`도 없다. 따라서 fixture의 구조 helper가
통과해도 공개 main은 항상 exit 78이다. 실제 policy와 pin은 보호 branch의 코드 리뷰로
동시에 설치하고, 운영 verifier를 시작 조립부에서 주입해야 한다.

main은 evidence JSON만 신뢰하지 않는다. scan report, SBOM, provenance, signature bundle과
필요 시 VEX의 실제 파일 bytes SHA-256을 재계산하고, 독립 verifier가 파싱한 final digest,
finding 목록, builder/source revision, canonical unsigned payload hash, 공개 키 fingerprint를
evidence/policy와 다시 대조한다. artifact나 parser가 없거나 결과가 다르면 BLOCKED다.

```powershell
./scripts/deploy/validate-release-evidence.ps1 `
  -Evidence <검증결과.json> -ScanReport <scanner_raw.json> -Sbom <sbom.json> `
  -Provenance <provenance.json> -SignatureBundle <signature.bundle> [-Vex <vex.json>]
```

현재 저장소에는 실제 policy·verifier·scanner·SBOM·provenance·서명 검증 결과가 없으므로
정식 공개 release 판정은 정직하게 BLOCKED다. 무료 demo·관리자 실제 분석 운영판·초대 링크
공개판 어느 쪽도 정식 공개 release 승인으로 해석하지 않는다. validator와 fixture 시험은 Docker나
외부 registry를 호출하지 않는다.

기본 `BETA_ADMIN_ONLY=1`이면 `ADMIN_EMAILS`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`가 모두 있어야 시작한다. `PIPELINE=real`은
추가로 `PROVENANCE_SEAL_SECRET`(UTF-8 32바이트 이상), `ANTHROPIC_API_KEY`,
`DART_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`을 요구한다. 검증 오류에는 변수명만
나오고 값은 출력되지 않는다.

`BACKUP_S3_BUCKET`을 설정하면 외부 백업 구성으로 간주한다. 이 경우
`BACKUP_TRIGGER_SECRET`, S3 전용 자격증명, `BACKUP_DATA_BOUNDARY_ID`,
`BACKUP_DATA_AUTHORITY_ID`, `BACKUP_MANIFEST_MIN_RETENTION_DAYS`가 모두 필요하다.
현재 저장소에는 production-ready `BackupManifestAppender` 구현과 앱 시작 시 provider
설치 호출이 없으므로 환경 검증은 구성된 외부 백업 배포를 의도적으로 차단한다. DB와
그 snapshot이 참조하는 불변 PDF를 한 recovery generation object set으로 저장하는 bucket,
그 bucket과 다른 권한·보존 경계의 append-only sink, signer, 최신 checkpoint 통제 경로를 구현하고
`install_manifest_appender_provider(...)`로 주입한 코드가 포함되기 전에는 이 차단을
환경 표식만으로 해제하지 않는다.

기존 manifest v1/HMAC 원장은 운영 v2로 자동 승격하지 않는다. 운영 전환 시 v2 레코드를
새 attested COMPLIANCE WORM sink에서 다시 발급하고, 별도 권한의 signed latest checkpoint에
결속해야 한다. 현재 production sink/signer/checkpoint adapter가 모두 없으므로 외부 백업과
복구 운영 판정은 BLOCKED다.

## Kubernetes

`kubernetes/base.yaml`에는 비밀값이 없다. 배포 전에 다음 두 항목을 플랫폼 안에서
준비한다.

1. Deployment의 예시 이미지 주소를 digest 또는 불변 태그로 바꾼다.
2. `company-analysis-runtime` Secret을 외부 비밀 관리 도구로 만든다. 저장소에 Secret
   manifest나 평문 값을 추가하지 않는다.

스토리지 클래스는 환경마다 다르므로 PVC에 고정하지 않았다. 볼륨이 UID/GID 1000으로
쓸 수 있는지, 플랫폼이 `fsGroup`을 지원하는지 먼저 확인한다. 배포 후 `/healthz`와
`/readyz`를 별도로 관측한다. 플랫폼 외부 백업은 `/var/data/storage.db`와 그 DB가
참조하는 `/var/data/report-artifacts`의 exact bytes를 같은 generation으로 결속해야 한다.
`storage.db` 한 파일만 백업 완료로 처리하지 않는다.
