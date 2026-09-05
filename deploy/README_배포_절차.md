> 상위 인덱스: [README.md](README.md)
> 챕터: 공개 reverse proxy gate·로컬 빌드·이미지 공급망 release gate·Kubernetes

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
