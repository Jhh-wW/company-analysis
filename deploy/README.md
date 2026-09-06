# 클라우드 중립 배포 계약

이 디렉터리는 특정 사업자의 배포 기능을 호출하지 않는다. 하나의 OCI 이미지가 Docker
Compose와 Kubernetes에서 같은 비-root 계정, 상태 확인 경로, 영속 경로를 사용한다.

## 챕터 목차

이 파일은 인덱스이자 계약 정본이다. 배포 계약 이름·스위치 표는 여기 남기고,
절차와 환경 검증 세부는 아래 두 파일에 둔다.

| 챕터 | 다루는 것 | 파일 |
|---|---|---|
| 환경변수 | 시작 검증이 요구하는 값, NAVER API HUB 키, 외부 백업 구성 | [README_환경변수.md](README_환경변수.md) |
| 배포 절차 | 공개 reverse proxy gate, 로컬 빌드·smoke, 이미지 공급망 release gate, Kubernetes | [README_배포_절차.md](README_배포_절차.md) |

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
- `ADMIN_EMAILS`, Google OAuth 3개 값과 함께 `ANTHROPIC_API_KEY`, `DART_API_KEY`를
  Render 환경변수로만 주입한다. 뉴스 검색을 쓸 때는 `NCP_APIGW_API_KEY_ID`·`NCP_APIGW_API_KEY`도
  주입한다. 실제 값은 저장소·문서·로그에 남기지 않는다. 시작 검증이 요구하는 전체 목록은
  [README_환경변수.md](README_환경변수.md)에 있다.
- `PROVENANCE_SEAL_SECRET`은 32바이트 이상이어야 하며 재배포 뒤에도 같은 값을 보존한다.
- `PUBLIC_ORIGIN`은 Blueprint가 web service의 `RENDER_EXTERNAL_URL`을 self-reference해
  고정한다. `GOOGLE_REDIRECT_URI`는 정확히 `<PUBLIC_ORIGIN>/auth/callback`이어야 한다.
- `autoDeployTrigger: commit`이므로 main에 커밋이 올라가면 곧 운영판 배포다. 따라서
  PR은 회귀 묶음 4개(`app`, `analysis_engine`, `deploy/tests`, `ops`)가 통과한 뒤에만 main에 합친다.
  Dashboard의 Auto-Deploy 값은 Blueprint Sync가 켜져 있을 때만 이 파일을 따라간다. 어긋나면
  Dashboard → Settings → Build & Deploy → Auto-Deploy를 `On Commit`으로 맞춘다.
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

세 좁은 계약은 forwarded client IP를 사용하지 않기 때문에 [README_배포_절차.md](README_배포_절차.md)의
일반 공개 reverse proxy gate 승인 증거를 요구하지 않는다. 조건 하나라도 달라지면 예외가 아니며 fail-closed한다.
실제 분석 운영판 두 계약도 일반 공개 승인이나 독립 백업 완료를 뜻하지 않는다. 영속 디스크
동작과 제한은 [Render 영속 디스크 문서](https://render.com/docs/disks)를 따른다.

### 선언하지 않는 것이 꺼짐인 스위치

아래 환경변수는 `render.yaml`에 **키를 적지 않는 것**이 「꺼짐」이다. 값을 `"0"`으로
적어 두는 것도 금지는 아니지만, 키가 있으면 나중에 누가 `"1"`로 바꾸기가 너무 쉬워진다.
켤 때는 정확히 `"1"`이어야 하며, 프로세스가 시작할 때 한 번 읽고 그대로 굳는다.

| 환경변수 | 켜면 달라지는 것 | 되돌리는 법 |
|---|---|---|
| `TYPED_DART_COLLECTOR` | DART 수집을 typed 수집기로 바꾼다. 실제 문서로 검증된 적이 없다. | 키를 지우고 재배포 |
| `NEWS_INTAKE` | 언론 보도를 공식 자료와 분리된 보조 문서로 받아 산문 인용·부록에만 사용한다. 정확히 `"1"`일 때만 켜진다. `PIPELINE=real`이면 NAVER API HUB 키 두 개가 함께 필수가 된다. | 키를 지우고 재배포 |
| `EVIDENCE_RECLASSIFY` | 결정론 문지기가 채우지 못한 장의 근거를 AI가 한 번 재판정하고, 프로그램 검증을 통과한 정확 인용만 후보에 보탠다. | 키를 지우고 재배포 |
| `NEWSROOM_DATE_AI` | 뉴스룸 목록의 글자 날짜가 프로그램 판정으로 확정되지 않을 때 AI 예비 1회와 원문 존재 대조를 허용한다. 프로그램 판정은 항상 켜져 있다. | 키를 지우고 재배포 |
| `REVENUE_TABLE_V2` | 매출 구성표를 「제목 목록」이 아니라 「표 모양」으로 찾고, 3장 카드 작가 안내문을 새 문구로 바꾼다. 표가 나오는 회사가 늘어난다. **현재 출시 Blueprint에서 `"1"`로 켜져 있다**(검사판 재현·회귀 통과 뒤 결정). | 키를 지우고 재배포 |

`REVENUE_TABLE_V2`를 끄면(키 삭제) 파서는 표제 목록(`제품별 매출액`·`지역별 매출액` 등)으로만
표를 찾는 옛 경로로 돌아간다. 코드를 되감을 필요가 없고, 이미 만들어진 보고서도
바뀌지 않는다. 표는 AI 프롬프트에 들어가지 않으므로 켜고 꺼도 조사 비용은 그대로다.
