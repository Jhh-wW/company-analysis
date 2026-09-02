# Render 운영 배포

`app/`을 Render에 배포하고 되돌리는 절차다. 서비스 내용·출고 정본은
[런타임 출고 계약](../../docs/출력물%20기준/90_공통_규칙/런타임_출고_계약.md), 환경변수
형식은 [`.env.example`](../.env.example), 실행법은 [`app/README.md`](../README.md)가 우선한다.

## 배포 원칙

- 자동 배포는 꺼져 있다(`render.yaml`의 `autoDeployTrigger: off`). 커밋이나 CI 통과만으로는
  배포되지 않으며, 사람이 Dashboard에서 **Manual Deploy**를 눌러야 한다.
- Uvicorn worker와 Render instance는 각각 `1`이다. SQLite와 인메모리 작업 상태 때문에
  이 값이 계약이다.
- `standard` web plan과 `/var/data` 1GB 영속 디스크를 쓴다. 실제 DART 후보 색인이
  Starter의 512MB를 넘어 재시작된 실측에 따른 최소 사양이다. 플랜·디스크 비용은 바뀌므로
  배포 직전 [Render 요금 페이지](https://render.com/pricing)와 Dashboard 예상 청구액을 본다.
- ⛔ `maxShutdownDelaySeconds`를 `render.yaml`에 **넣지 마라.** 영속 디스크가 있는 서비스에
  이 값을 쓰면 Render가 Blueprint 동기화를 거부한다(`max shutdown delay is not supported
  for services with a disk`). 디스크는 SQLite·보고서·감사 기록을 보존하므로 뺄 수 없다.
- ⚠️ 그래서 종료 유예는 Render 기본 **30초**다. 앱은 HTTP 정리 20초 → 조사 정리 240초 →
  취소 1초를 **직렬**로 기대하므로 어긋난다. 배포·재시작 중이던 조사는 비용·중단 표식을
  저장하기 전에 잘릴 수 있다. 종료·교체 순서는
  [배포 교체 계약](../../docs/architecture/deployment-contract.md)을 따른다.
- 비밀값과 사용자 식별자는 저장소·문서·메신저·화면 캡처에 남기지 않는다.

## Blueprint를 동기화하지 않는 이유

이 서비스의 Blueprint는 **지금 저장소를 가리키지 않는다.** Render가 그 저장소에 접근하지
못해 Manual Sync가 실패한다. Blueprint Settings에서 바꿀 수 있는 것은 Auto Sync 여부와
Blueprint 파일 경로뿐이고 연결 저장소를 바꾸는 항목이 없다. 새 저장소로 Blueprint를
새로 만들면 기존 서비스를 넘겨받지 않고 이름에 접미사가 붙은 **복제 서비스**가 생긴다.

→ **Blueprint Manual Sync / Deploy Blueprint를 누르지 않는다.** 환경변수는 서비스의
**Environment 탭에서 직접 편집**한다. Sync가 돌지 않으므로 대시보드에서 고친 값이
Blueprint 파일 값에 덮여 쓰이지 않는다.

`render.yaml`은 Blueprint가 되살아났을 때 어긋나지 않도록 대시보드와 **같은 값**으로
유지한다. 배포 계약 시험(`deploy/tests/test_deployment_contract.py`)이 이 파일을 직접 읽어
검사하므로, 값을 바꾸면 같은 커밋에서 시험도 함께 고친다.

## 출시 릴리스에서 바꾸는 값 두 개

| 키 | 지금 | 출시 |
|---|---|---|
| `REPORT_RELEASE_MODE` | `SHADOW` | **`FULL`** |
| `DEPLOYMENT_RUNTIME_CONTRACT` | `render-admin-real-no-forwarded-v1` | **`render-portfolio-link-v1`** |

`TYPED_DART_COLLECTOR`는 **설정하지 않는다.** 값을 넣지 않는 것이 꺼진 상태이며, 실제
DART 문서로 검증된 적이 없어 이번 출시에서는 켜지 않는다. `BETA_ADMIN_ONLY`도 `"1"`
그대로 둔다 — 로그인 벽은 유지하고 초대 명단 회원만 통과시키기 위해서다.

### 순서 (①과 ②를 바꾸지 않는다)

1. **Manual Deploy로 새 커밋을 먼저 올린다.** 이 시점의 환경변수는 아직 `SHADOW`다.
2. `/healthz`의 `commit` 값이 방금 올린 SHA인지 확인한다.
3. **그 다음에** Environment 탭에서 위 두 값을 편집한다. 저장하면 서비스가 재시작한다.
4. `/healthz`·`/readyz`를 다시 확인하고, 초대 링크를 하나 발급해 주소·QR 흐름을 눈으로 본다.

①②를 뒤바꾸면 **옛 코드가 `FULL`로 돌면서** 연습 모드에서 만든 캐시를 재사용할 수 있다.
새 판정을 지키는 방어는 새 코드에만 있다.

되돌릴 때는 같은 Environment 탭에서 두 값을 옛 값으로 돌려놓는다.

## 두 계약이 갈리는 지점

`render-portfolio-link-v1`은 관리자 실분석 계약과 **같은 안전 조건**(고정 `PUBLIC_ORIGIN`,
빈 `FORWARDED_ALLOW_IPS`, `BETA_ADMIN_ONLY=1`, 같은 `--no-proxy-headers` 실행 명령) 위에서
링크·초대·QR 입구만 여는 계약이다.

| 동작 | `render-admin-real-no-forwarded-v1` | `render-portfolio-link-v1` |
|---|---|---|
| 관리자 로그인·실제 분석 | 허용 | 허용 |
| `/admin/links/new`(초대 링크 발급) | 차단(404) | 허용 |
| `/admin/invite`(회원 초대) | 차단(409) | 허용 |
| `/admin/members/{email}/limit`(회원 한도) | 차단(409) | 허용 |
| `/k/` 링크 입구 | 로그인 화면으로 이동 | 열림 |
| 명단 밖 구글 로그인 | `/auth/not-admin` | `/auth/not-admin`(그대로) |
| 명단 회원 구글 로그인 | 홈·조사 경로 통과, `/admin`은 차단 | 같음 |
| Host 고정·CSRF Origin 고정 | 켜짐 | 켜짐(그대로) |
| `ENGINE_V2` | 배포자가 값 선택 | `1` 필수 |
| `REPORT_RELEASE_MODE` | 배포자가 값 선택 | 필수(`SHADOW`·`ENFORCE_NO_PARTIAL`·`FULL` 중 하나) |

★ 「명단 회원이 로그인 벽을 통과한다」는 `BETA_ADMIN_ONLY=1`인 모든 배포에 계약과 무관하게
적용된다. 로그인 벽은 «누가 통과하는가»의 축이고 runtime contract는 «어느 forwarded-header
신뢰 모델을 쓰는가»의 축이라 서로 다른 문제이기 때문이다. 링크 발급·초대·`/k/` 입구만
계약에 따라 갈린다.

## 필수·조건부 환경변수

| 이름 | 배포 값과 보관 원칙 |
|---|---|
| `PIPELINE` | `real` |
| `BETA_ADMIN_ONLY` | `1`. 관리자와 초대 명단 회원만 로그인 벽을 통과 |
| `DEPLOYMENT_RUNTIME_CONTRACT` | 출시 후 `render-portfolio-link-v1` |
| `DEPLOYMENT_EXPOSURE` / `DEPLOYMENT_PLATFORM` | `public` / `render` |
| `ADMIN_EMAILS` | 관리자 Google 이메일. 여러 명이면 쉼표로 구분 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | 배포용 OAuth 값. 비밀 관리자에 보관 |
| `PUBLIC_ORIGIN` | Blueprint가 `RENDER_EXTERNAL_URL`을 self-reference해 고정한 HTTPS origin |
| `GOOGLE_REDIRECT_URI` | 정확히 `<PUBLIC_ORIGIN>/auth/callback` |
| `FORWARDED_ALLOW_IPS` | 빈 값. proxy headers를 신뢰하지 않음 |
| `GRACEFUL_SHUTDOWN_SECONDS` | `20` |
| `APP_DATA_ROOT` / `STORAGE_DB_PATH` | `/var/data` / `/var/data/storage.db` |
| `REPORT_ARTIFACT_CAPACITY_BYTES` | `536870912`(512MiB). 넘으면 새 출고를 막는다 |
| `PROVENANCE_SEAL_SECRET` | 재배포·복구에서 동일하게 유지할 32바이트 이상 무작위 비밀 |
| `ENGINE_V2` | `1`. 정확히 `1`일 때만 v2다 |
| `REPORT_RELEASE_MODE` | 출시 후 `FULL`. 오타·공백은 시작 검증이 거부 |
| `ANTHROPIC_API_KEY` / `DART_API_KEY` | `PIPELINE=real` 필수 provider 비밀 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | `PIPELINE=real` 필수 provider 비밀 |
| `SHARE_PUBLIC_BASE_URL` | QR·복사용 절대 주소의 정본. 없으면 `RENDER_EXTERNAL_URL` |
| `NOTION_TOKEN` / `NOTION_PARENT_PAGE_ID` | Notion 전송을 쓸 때만 |
| `TYPED_DART_COLLECTOR` | **넣지 않는다.** 값이 없으면 꺼진 상태다 |

`AUTH_COOKIE_INSECURE`와 로컬 관리자 capability는 로컬 전용이라 Render에 설정하지 않는다.
`BACKUP_S3_BUCKET`을 미리 넣으면 준비되지 않은 adapter 때문에 시작 검증이 의도적으로
실패한다.

### `ENGINE_V2`가 하는 일

코드는 `os.environ.get("ENGINE_V2") == "1"` 하나로만 갈린다
(`app/src/features/pipeline/real.py`). 켜져 있는 곳은 `render.yaml` 한 곳뿐이고
`app/Dockerfile` 기본값에는 일부러 넣지 않았다 — 이미지 기본값은 돈이 들지 않는 상태만
담고, v2를 켤지는 배포 설정이 한 곳에서 정한다.

값을 `true`·`yes`·`" 1 "`처럼 적으면 예전에는 **조용히** v1로 떨어졌다. 지금은 시작
검증(`deploy/validate_environment.py`)이 `1`과 `0` 외의 값을 거부해 컨테이너가 뜨지 않는다.
값을 아예 넣지 않는 것은 오류가 아니다(v1을 쓰겠다는 정상적인 선택이다).

켜면 보고서 본문을 composer가 쓰고 2·4·7장에 도식이 실린다. v2 전용 캐시는 배포
revision·생성기 지문·사업연도·실제 DART 접수번호·정규화한 재무 응답 지문이 **모두** 같을
때만 재사용한다. 하나라도 다르면 새로 조사한다.

## 주소와 Google OAuth 연결

1. Render가 발급한 고정 `https://...onrender.com` 주소를 확정한다.
2. Google Cloud의 승인된 리디렉션 URI에 `https://<주소>/auth/callback`을 등록한다.
3. 자동 고정된 `PUBLIC_ORIGIN`을 확인하고 `GOOGLE_REDIRECT_URI`를 같은 host로 맞춘다.
4. 동의 화면이 Testing 상태면 운영 관리자를 테스트 사용자에 넣는다.
5. `/healthz`·`/readyz`, HTTPS, 로그인 callback, 관리자 허용·비관리자 거절을 확인한다.

서비스 이름이 바뀌면 self-reference가 만든 주소를 확인하고 `GOOGLE_REDIRECT_URI`와 Google
등록 URI를 함께 바꾼다. 자세한 설정은 [구글 로그인](구글로그인_설정.md)을 따른다.

## 자동출고와 Notion 전송

```text
report_standard 통과
  → report/PDF/모든 페이지 PNG 해시 고정
  → 사실·인용·수치·구조·금지 문구·PDF 렌더·채널 동등성 자동검사
  → 검사 전후 해시 재대조와 자동출고 레코드 확정
  → PDF 다운로드 허용 → 설정된 경우 같은 정본을 Notion으로 전송
```

검사 하나라도 실패하거나 검사 뒤 해시가 바뀌면 재승인하지 않고 전체를 `GATE_STOPPED`한다.
`/review/pdf/*` GET/POST는 `410`이고, Notion은 별도 내용을 만드는 채널이 아니다.

## 데이터 보존과 백업

영속 디스크는 재시작·재배포를 견디는 보존 수단이지 **독립 백업이 아니다.** 외부 백업과
정기 작업은 코드와 인증 경로까지만 있고 `render.yaml`에 cron으로 선언돼 있지 않다.
`BackupManifestAppender` 운영 구현·독립 sink/signer·최신 checkpoint 공급 경로가 없어
**외부 백업 배포는 막혀 있다.** 배포 버튼만 눌러서는 재해 복구가 생기지 않는다.

모든 쓰기 연결은 `synchronous=EXTRA`를 강제한다. 비용·출고 원장의 마지막 commit이 전원
중단 직후 되돌아가지 않게 하는 계약이므로 성능을 이유로 낮추지 않는다. 실행 중 DB 파일이
사라지면 빈 DB를 자동으로 만들지 않고 서비스가 닫힌다.

최초 승인 PDF 원본에는 `REPORT_ARTIFACT_CAPACITY_BYTES`가 512MiB를 배정한다. 한도를 넘으면
과거 원본을 지우지 않고 **새 출고를 닫는다.** 설정값을 새 결과에 맞춰 늘리는 것을 장애
해결로 삼지 않는다.

외부 백업 adapter를 구현한 뒤 처음 한 번 설정할 값과 조건, 복구 세대 검증 절차, 비밀 복구
묶음은 [장기 휴면 백업·복구](장기_휴면_백업.md)와
[배포 운영 런북](../../ops/배포_운영_런북.md)의 「백업 무결성과 독립 서명 manifest」를 따른다.
비상시 같은 디스크 안 임시 백업만 `python tools/backup_sqlite.py backup`으로 만들 수 있다.

## 배포 체크리스트

- [ ] GitHub Actions `quality-gate` 통과
- [ ] `standard` plan과 1GB 디스크의 현재 청구 조건 승인
- [ ] `PIPELINE=real`, `BETA_ADMIN_ONLY=1`, instance/worker 각각 1개
- [ ] `PUBLIC_ORIGIN`·Google 승인 URI·`GOOGLE_REDIRECT_URI`가 같은 host의 `/auth/callback`
- [ ] `PROVENANCE_SEAL_SECRET`이 32바이트 이상이며 기존 값 그대로 보존
- [ ] Manual Deploy → `/healthz`의 commit 확인 → Environment 탭에서 값 두 개 편집 → 재확인
- [ ] 관리자 로그인, 명단 밖 구글 로그인 차단, 명단 회원 통과 확인
- [ ] 초대 링크 1개 발급해 주소·QR과 한도 표시 확인
- [ ] 작은 회사 1건의 실제 조사·출고 게이트·PDF·비용 기록 통과
- [ ] 재시작 뒤 SQLite·보고서·감사 기록이 `/var/data`에 남아 있는지 확인
- [ ] 외부 백업이 아직 막혀 있다는 사실을 운영자에게 고지

공식 참고: [Render Blueprint](https://render.com/docs/blueprint-spec),
[Render 요금](https://render.com/pricing), [Render 영속 디스크](https://render.com/docs/disks),
[Render 상태 확인](https://render.com/docs/health-checks),
[Google OAuth 웹 서버](https://developers.google.com/identity/protocols/oauth2/web-server)
