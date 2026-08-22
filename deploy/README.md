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

기본 `BETA_ADMIN_ONLY=1`이면 `ADMIN_EMAILS`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`가 모두 있어야 시작한다. `PIPELINE=real`은
추가로 `PROVENANCE_SEAL_SECRET`(UTF-8 32바이트 이상), `ANTHROPIC_API_KEY`,
`DART_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`을 요구한다. 검증 오류에는 변수명만
나오고 값은 출력되지 않는다.

## Kubernetes

`kubernetes/base.yaml`에는 비밀값이 없다. 배포 전에 다음 두 항목을 플랫폼 안에서
준비한다.

1. Deployment의 예시 이미지 주소를 digest 또는 불변 태그로 바꾼다.
2. `company-analysis-runtime` Secret을 외부 비밀 관리 도구로 만든다. 저장소에 Secret
   manifest나 평문 값을 추가하지 않는다.

스토리지 클래스는 환경마다 다르므로 PVC에 고정하지 않았다. 볼륨이 UID/GID 1000으로
쓸 수 있는지, 플랫폼이 `fsGroup`을 지원하는지 먼저 확인한다. 배포 후 `/healthz`와
`/readyz`를 별도로 관측하고, `/var/data/storage.db`의 플랫폼 외부 백업을 구성한다.
