# Render 운영 배포

이 문서는 `app/`을 Render에 배포하고 복구하는 운영 절차다. 서비스의 내용·출고
정본은 [런타임 출고 계약](../../docs/출력물%20기준/90_공통_규칙/런타임_출고_계약.md),
환경변수 형식은 [`.env.example`](../.env.example), 전체 실행법은
[`app/README.md`](../README.md)를 우선한다.

## 배포 원칙

- 비공개 저장소의 기본 브랜치와 통과한 GitHub Actions `quality-gate`에서만 배포한다.
- 첫 배포는 `PIPELINE=demo`, `BETA_ADMIN_ONLY=1`로 시작한다.
- Uvicorn worker와 Render instance는 각각 `1`로 유지한다.
- SQLite와 실행 이력은 `/var/data` 영속 디스크 하나에 둔다.
- 비밀값과 사용자 식별자는 Git, 채팅, 티켓, 화면 캡처에 남기지 않는다.
- 현재 `render.yaml`의 모든 서비스는 `autoDeployTrigger: off`라 커밋이나 CI 통과만으로 배포되지 않는다. 이번 로컬 작업에서는 Render 연결·배포를 하지 않으며, 첫 push 전 대시보드와 Blueprint의 Auto Sync도 비활성인지 별도 확인한다.

## 활성 제품 계약

신규 분석 입력은 **회사명 필수 + 주소 힌트 선택**이다. 주소를 비워도 회사 후보 확인과
조사가 진행되어야 한다. 채용공고, 공고 이미지, OCR, 직무·개인 맞춤 입력은 신규 흐름의
입력이 아니다.

화면, PDF, Notion은 서로 다른 보고서를 만들지 않는다. `report_standard`를 통과한 같은
정본 보고서를 렌더링하며, **PDF가 사용자 다운로드 정본**이다. 같은 보고서·PDF·모든
페이지 PNG hash의 필수 자동검사를 전부 통과한 자동출고 레코드를 웹·PDF·Notion이
공유한다. 하나라도 실패하면 세 채널 전체를 `GATE_STOPPED`한다.

## 처음 배포하는 순서

1. GitHub Actions `quality-gate`가 초록색인지 확인한다.
2. Render에서 저장소 루트의 `render.yaml`로 Blueprint를 만든다.
3. 아래 환경변수를 Render 대시보드에 직접 입력한다.
4. Render가 발급한 HTTPS 주소를 Google OAuth와 공유 링크 기준 주소에 반영한다.
5. `/healthz`(프로세스 생존)와 `/readyz`(SQLite·로그인 설정 준비), 관리자 로그인,
   비관리자 차단을 확인한다.
6. `PIPELINE=demo`에서 회사명만 입력한 경우와 주소 힌트를 함께 입력한 경우를 각각 시험한다.
7. PDF 준비 → 필수 자동검사 → hash 결속 자동출고 → 다운로드·Notion 흐름과 수동 승인 410을 시험한다.
8. 첫 SQLite 백업을 내려받아 해시 검증하고, 비밀값 복구 묶음을 별도로 확인한다.

## 필수·조건부 환경변수

| 이름 | 배포 값과 보관 원칙 |
|---|---|
| `ADMIN_EMAILS` | 관리자 Google 이메일. 여러 명이면 쉼표로 구분 |
| `GOOGLE_CLIENT_ID` | 배포용 Google OAuth 클라이언트 ID |
| `GOOGLE_CLIENT_SECRET` | 비밀 관리자에 보관하는 OAuth 비밀 |
| `GOOGLE_REDIRECT_URI` | `https://<service-host>/auth/callback` |
| `SHARE_PUBLIC_BASE_URL` | 검증된 공개 HTTPS origin. 경로·쿼리 없이 `https://<service-host>` 형식 |
| `PDF_RELEASE_PARTICIPANTS` | 구형 수동 승인 감사자료를 해석해야 할 때만 복구하는 과거 역할 JSON. 신규 출고 권한 아님 |
| `PROVENANCE_SEAL_SECRET` | 모든 재배포·worker·복구에서 동일하게 유지할 32바이트 이상의 무작위 비밀 |
| `ANTHROPIC_API_KEY` | `PIPELINE=real`에서 생성 모델을 사용할 때만 |
| `DART_API_KEY` | `PIPELINE=real`에서 전자공시를 조회할 때만 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | `PIPELINE=real`에서 뉴스 검색을 사용할 때만 |
| `NOTION_TOKEN` / `NOTION_PARENT_PAGE_ID` | 자동출고 완료 보고서를 Notion으로 보낼 때만 |

`AUTH_COOKIE_INSECURE`와 로컬 관리자 capability는 로컬 전용이다. Render에는 설정하지 않는다.
실제 값과 JSON 예시는 `app/.env.example`의 설명을 따르되 실제 사람의 `sub`나 비밀값을
파일에 복사하지 않는다.

### provenance 비밀 준비

1. 비공개 로컬 터미널에서 다음처럼 32바이트보다 큰 무작위 값을 만든다.

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

2. 출력값을 즉시 조직 비밀 관리자에 `PROVENANCE_SEAL_SECRET`으로 저장하고 Render에
   같은 값을 넣는다. 터미널 기록, 채팅, 문서, 저장소에는 복사하지 않는다.
3. 비밀값의 소유자·마지막 검증일·복구 담당자를 비밀 관리자의 같은
   복구 항목에 기록한다.

## 주소와 Google OAuth 연결

1. Render의 `https://...onrender.com` 주소 또는 검증된 사용자 도메인을 확정한다.
2. Google Cloud의 승인된 리디렉션 URI에
   `https://<service-host>/auth/callback`을 등록한다.
3. Render의 `GOOGLE_REDIRECT_URI`와 `SHARE_PUBLIC_BASE_URL`을 같은 host 기준으로
   갱신하고 재배포한다.
4. OAuth 동의 화면이 Testing 상태라면 운영 관리자를 테스트 사용자에 넣는다.
5. `/healthz`와 `/readyz` 응답, HTTPS, 로그인 callback, 관리자 허용·비관리자 거절을
   확인한다.

서비스 이름이나 사용자 도메인이 바뀌면 세 값과 Google 등록 URI를 함께 바꾼다. 예전
공유 링크를 새 host로 자동 추정하지 않는다.

## 데모에서 먼저 확인할 것

`PIPELINE=demo`와 `BETA_ADMIN_ONLY=1`에서는 저장된 데모 자료만 사용하므로 외부 조사
API 비용이 발생하지 않는다. 다음을 확인한다.

- 회사명만 입력해 후보 확인부터 결과까지 완료
- 선택 주소 힌트를 넣었을 때 동일 회사로 식별
- 비관리자 접근 차단과 공유 링크 만료·철회
- 정본 화면과 PDF 내용 일치
- 자동검사 실패 시 웹·PDF·Notion 전체 차단, 수동 승인 GET/POST 410

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

사용자가 외부 호출과 예상 비용을 승인하고 provider 비밀과 예산 한도를 확인한 뒤에만
`PIPELINE=real`로 바꾼다. 작은 회사 입력 한 건으로 DART·뉴스·홈페이지 수집, 정본
게이트, 자동출고 흐름을 끝까지 시험한 뒤 범위를 늘린다.

실제 조사도 입력 계약은 회사명과 선택 주소뿐이다. 개인정보나 채용공고 원문을 넣지
않는다. Google Places 후보 검색은 결과 보관·표시 약관 검토가 완료될 때까지 활성화하지
않는다.

## 데이터 백업과 비밀 복구

현재 `storage.db`는 웹 서비스에 붙은 영속 디스크에 있다. Render cron job은 다른 서비스의
영속 디스크를 읽을 수 없으므로 다음 구조로 매일 외부 백업한다.

```text
Render cron
  → POST https://<웹 주소>/internal/backup/run (Bearer 비밀)
  → 웹 프로세스가 자기 디스크의 SQLite Backup API 스냅샷 생성
  → 비공개 S3 호환 bucket에 DB와 .sha256 업로드
  → 두 파일을 다시 내려받아 SHA-256·SQLite integrity·외래키 검사
  → 35일 또는 35개를 넘은 과거 백업 삭제
```

`render.yaml`의 백업 cron은 매일 `19:00 UTC`, 한국 시각 다음 날 `04:00`에 실행된다. 백업
파일은 `company-analysis/storage-backup-<UTC시각>.sqlite3`와 같은 이름의
`.sha256` 한 쌍이다. 성공 응답은 원격 파일을 다시 검증한 뒤에만 반환되므로 업로드만 된
손상 파일을 성공으로 기록하지 않는다.

### 최초 한 번 설정

1. S3 또는 S3 호환 공급자에 **비공개 bucket**을 만들고 공개 접근을 차단한다.
2. `company-analysis/` prefix에 `PutObject`, `GetObject`, `DeleteObject`, `ListBucket`만
   가능한 백업 전용 자격증명을 만든다. 다른 bucket 권한은 주지 않는다.
3. 웹 서비스 Environment에 아래 값을 넣는다.
   - `BACKUP_S3_BUCKET`, `BACKUP_S3_REGION`
   - AWS S3가 아니면 공급자의 HTTPS `BACKUP_S3_ENDPOINT_URL`
   - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
   - 기본 암호화는 `AES256`; KMS를 쓰면 `BACKUP_S3_SERVER_SIDE_ENCRYPTION=aws:kms`와
     `BACKUP_S3_KMS_KEY_ID`
4. cron의 `BACKUP_TRIGGER_URL`을 실제 공개 웹 주소의
   `https://<주소>/internal/backup/run`으로 넣는다. `BACKUP_TRIGGER_SECRET`은 Blueprint가
   웹에 자동 생성하고 cron이 같은 값을 참조하므로 사람이 복사하지 않는다.
5. 기존 Blueprint에 서비스를 추가하는 경우 새 `sync: false` 값은 자동 반영되지 않을 수
   있다. 웹의 bucket·자격증명과 cron URL이 실제 대시보드에 있는지 직접 확인한다.
6. Render 알림에서 `company-analysis-backup`의 실패 알림을 운영 수신처로 켠다. 별도로
   외부 저장소 또는 감시 도구에서 `company-analysis/`의 최신 DB 객체가 24시간 넘게
   생성되지 않으면 알리도록 설정한다. 이 감시는 Render 장애로 cron 자체가 실행되지 않는
   경우까지 잡기 위해 Render 밖에 둔다.

### 주간 XLSX와 일일 정리

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

Blueprint 반영 전에는 세 cron 모두 실행되지 않는다. 실제 배포 직전에는 다음을 확인한다.

1. `BACKUP_TRIGGER_URL`과 두 cron의 `MAINTENANCE_TRIGGER_URL`이 정확한 HTTPS 내부 경로인지 확인한다.
2. 세 cron 실패 알림의 운영 수신처를 설정한다.
3. 각 cron을 한 번 수동 실행해 백업 재검증, XLSX 다운로드, 정리 작업 성공 사건을 확인한다.
4. 다음 예정 시각에 중복 파일·중복 정리 없이 한 번만 실행됐는지 확인한다.

S3 호환 공급자가 path-style 주소만 지원하면 `BACKUP_S3_ADDRESSING_STYLE=path`로 바꾼다.
기본 `auto`와 AWS 기본 endpoint에는 `BACKUP_S3_ENDPOINT_URL`을 넣지 않는다. URL에 접근키를
붙이지 않는다.

### 첫 실행과 다음 날 확인

Render 대시보드에서 cron을 한 번 수동 실행하고 로그의 `외부 백업 완료`를 확인한다. 외부
bucket에서 같은 이름의 `.sqlite3`와 `.sha256`을 내려받아 다음처럼 검증한다.

```console
python tools/backup_sqlite.py verify <내려받은.sqlite3> --checksum <내려받은.sqlite3.sha256>
```

그 다음 날 새 날짜의 한 쌍이 생겼는지 다시 확인하고 같은 `verify`를 통과시킨다. 한 파일만
있거나 검증이 실패하면 성공으로 보지 않는다. 복구 상세 절차는
[장기 휴면 백업](장기_휴면_백업.md)을 따른다.

외부 저장소가 아직 준비되지 않은 비상 상황에서만 Render Shell에서 아래 명령으로 일관성
있는 임시 백업을 만들 수 있다. 같은 디스크에만 남겨 두면 디스크 장애를 견디지 못한다.

```console
python tools/backup_sqlite.py backup
```

DB 백업에는 환경 비밀이 들어 있지 않으므로 다음 **복구 묶음**을 조직 비밀 관리자에 별도로
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

## 공개 전 체크리스트

- [ ] GitHub Actions `quality-gate`와 Docker `/readyz` 통과
- [ ] `PIPELINE=demo`, `BETA_ADMIN_ONLY=1`에서 첫 검증
- [ ] 회사명 단독 입력과 선택 주소 입력 모두 통과
- [ ] 관리자·비관리자·공유 링크 권한 확인
- [ ] 필수 자동검사 전 항목 통과 시 같은 hash로 웹·PDF·Notion 자동출고
- [ ] 검사 실패·검사 후 hash 변경 시 세 채널 전체 `GATE_STOPPED`
- [ ] 수동 승인 GET/POST가 410이고 구형 기록으로 우회 불가
- [ ] 실패 고객 청구 0원과 실패 provider 내부 AI 원가 보존
- [ ] SQLite 백업과 SHA-256 검증 완료
- [ ] S3 호환 외부 백업을 수동 실행하고 재다운로드 `verify` 통과
- [ ] 35일·35개 보관 정책과 24시간 미생성 외부 알림 수신처 확인
- [ ] 비밀 복구 묶음의 소유자와 복구 시험일 확인
- [ ] worker `1`, instance `1`, 디스크 `/var/data` 유지
- [ ] real 전환 시 provider 비용과 예산 한도 별도 승인

공식 참고: [Render Blueprint](https://render.com/docs/blueprint-spec),
[Render 영속 디스크](https://render.com/docs/disks),
[Render cron job](https://render.com/docs/cronjobs),
[Render SSH](https://render.com/docs/ssh),
[Render 상태 확인](https://render.com/docs/health-checks),
[Google OAuth 웹 서버](https://developers.google.com/identity/protocols/oauth2/web-server)
