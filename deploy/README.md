# 클라우드 중립 배포 계약

이 디렉터리는 특정 사업자의 배포 기능을 호출하지 않는다. 하나의 OCI 이미지가 Docker
Compose와 Kubernetes에서 같은 비-root 계정, 상태 확인 경로, 영속 경로를 사용한다.

## 고정 계약

- 프로세스 UID/GID는 `1000:1000`이며 root 실행과 권한 상승을 거절한다.
- `/healthz`는 liveness, `/readyz`는 SQLite·로그인 설정 readiness다.
- SQLite, 관측 기록, tldextract 캐시는 모두 `/var/data` 영속 볼륨 아래에 둔다.
- 앱의 작업 드레인 상한은 240초다. Uvicorn은 300초, 플랫폼은 330초를 기다린 뒤 종료한다.
- 애플리케이션·Uvicorn 로그는 stdout/stderr로만 보낸다. Compose의 로컬 로그 회전은
  10MB × 5개이며, 운영 플랫폼에서는 표준 출력 수집기를 연결한다.
- 컨테이너 루트 파일시스템은 읽기 전용으로 쓸 수 있다. 쓰기 경로는 `/var/data`와
  메모리·임시 PDF 작업용 `/tmp`뿐이다.
- SQLite 단일 writer 계약 때문에 replica와 worker는 각각 1개다. Kubernetes 갱신 전략은
  `Recreate`다.

## 공개 reverse proxy gate

로컬 Compose는 `DEPLOYMENT_EXPOSURE=local`, `DEPLOYMENT_PLATFORM=local`과 loopback
forwarded 신뢰만 허용한다. 공개 배포는 `public`을 명시해야 하며 다음 증거가 없으면
entrypoint가 웹 시작을 차단한다. 환경의 SHA-256은 canary artifact 식별자일 뿐 신뢰
증명이 아니다. 서명 artifact 원문과 고정 policy를 검증할 운영 adapter가 아직 없으므로
`PRODUCTION_FORWARDED_EVIDENCE_VERIFIER_AVAILABLE=False`이고 현재 모든 public 설정은
값을 전부 채워도 BLOCKED다.

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

공유 링크의 requester 한도는 링크별 IP에 대해 12회/분이다. forwarded 신뢰가 빠지면
모든 사용자가 edge proxy IP 하나로 합쳐져 정상 사용자도 함께 429가 될 수 있고, 반대로
신뢰 범위가 넓거나 XFF가 정화되지 않으면 공격자가 IP 통장을 바꿔 한도를 우회할 수 있다.

이 Render 판정은 [Render 환경변수 문서](https://render.com/docs/environment-variables)의
Docker/All runtimes 범위와 [Uvicorn proxy 설정](https://www.uvicorn.org/settings/)의
기본 loopback·명시 IP/IP Network 신뢰 계약을 따른다. Python native 전용 기본값이나
outbound 주소를 Docker 컨테이너가 실제로 보는 ingress peer 주소로 추정하지 않는다.

`RENDER=true`, `RENDER_SERVICE_TYPE=web`, `RENDER_EXTERNAL_URL` 또는 Render hostname
marker가 하나라도 보이면 `DEPLOYMENT_PLATFORM=render`와 `DEPLOYMENT_EXPOSURE=public`을
강제한다. `KUBERNETES_SERVICE_HOST`/`PORT`, `KUBERNETES_PORT` 또는 projected
service-account marker가 보이면 같은 방식으로 `kubernetes`/`public`을 강제한다. 따라서
플랫폼 안에서 `local`이라고 자기선언해 public gate를 우회할 수 없다. 다만 marker는
플랫폼이 이름을 바꾸거나 주입을 생략하면 완전하지 않으며, 공개성의 충분한 증명은 아니다.

Compose는 `127.0.0.1:${HOST_PORT}:10000` loopback bind만 release 계약으로 고정한다.
일반 `docker run -p 0.0.0.0:...` 또는 다른 orchestrator의 공개 port는 marker로 확실히
감지할 수 없고 public readiness를 얻지 못한다. 별도 공개 플랫폼 계약 없이는 BLOCKED다.

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
공개 이미지 판정은 정직하게 BLOCKED다. validator와 fixture 시험은 Docker나 외부 registry를
호출하지 않는다.

기본 `BETA_ADMIN_ONLY=1`이면 `ADMIN_EMAILS`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`가 모두 있어야 시작한다. `PIPELINE=real`은
추가로 `PROVENANCE_SEAL_SECRET`(UTF-8 32바이트 이상), `ANTHROPIC_API_KEY`,
`DART_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`을 요구한다. 검증 오류에는 변수명만
나오고 값은 출력되지 않는다.

`BACKUP_S3_BUCKET`을 설정하면 외부 백업 구성으로 간주한다. 이 경우
`BACKUP_TRIGGER_SECRET`, S3 전용 자격증명, `BACKUP_DATA_BOUNDARY_ID`,
`BACKUP_DATA_AUTHORITY_ID`, `BACKUP_MANIFEST_MIN_RETENTION_DAYS`가 모두 필요하다.
현재 저장소에는 production-ready `BackupManifestAppender` 구현과 앱 시작 시 provider
설치 호출이 없으므로 환경 검증은 구성된 외부 백업 배포를 의도적으로 차단한다. DB bucket과
다른 권한·보존 경계의 append-only sink, signer, 최신 checkpoint 통제 경로를 구현하고
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
`/readyz`를 별도로 관측하고, `/var/data/storage.db`의 플랫폼 외부 백업을 구성한다.
