# Render 시험 배포

첫 배포는 **관리자만 접속할 수 있고 AI API 비용이 없는 데모 모드**다. 다만 Render Starter와 1GB 디스크 요금은 현재 월 최소 약 **$7.25**가 발생한다. 실제 AI 조사는 아래 확인을 모두 끝낸 뒤에만 켠다.

## 배포하면 어디에 저장되나

- 프로그램 코드: Docker 이미지
- SQLite DB: `/var/data/storage.db`
- 처리 이력: `/var/data/observability/runs.jsonl`
- DART·네이버 고정 캐시와 호출 횟수: `/var/data` 아래
- 개인정보 삭제에 쓰는 도메인 판별 캐시: `/var/data/cache/tldextract`

`/var/data` 전체가 Render의 1GB 영속 디스크다. 서버를 재시작하거나 새 버전을 배포해도 이 안의 파일은 유지된다. 서비스나 디스크를 삭제하면 사라질 수 있으므로 외부 백업은 별도로 보관한다.

## 1. Render에 처음 올리기

1. 코드를 GitHub 저장소에 올린다.
2. Render에서 **New → Blueprint**를 고르고 그 저장소를 연결한다.
3. 저장소 루트의 `render.yaml`을 선택한다.
4. 아래 환경변수 값을 Render 화면에 직접 넣는다. 코드나 채팅에 비밀키를 붙여 넣지 않는다.

| 이름 | 넣을 값 |
|---|---|
| `ADMIN_EMAILS` | 관리자 구글 이메일. 여러 명이면 쉼표로 구분 |
| `GOOGLE_CLIENT_ID` | Google OAuth 클라이언트 ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth 비밀키 |
| `GOOGLE_REDIRECT_URI` | 첫 배포 뒤 정할 `https://서비스주소/auth/callback` |
| `ANTHROPIC_API_KEY` | 실제 조사와 이미지 글자 추출용 |
| `DART_API_KEY` | 전자공시 조회용 |
| `NAVER_CLIENT_ID` | 네이버 뉴스 검색용 |
| `NAVER_CLIENT_SECRET` | 네이버 뉴스 검색용 비밀키 |
| `NOTION_TOKEN` | 노션 전송용 통합 토큰 |
| `NOTION_PARENT_PAGE_ID` | 보고서를 만들 부모 노션 페이지 ID |

`AUTH_COOKIE_INSECURE`는 Render에 만들지 않는다. HTTPS 로그인 쿠키를 약하게 만드는 로컬 시험 전용 값이다.

서비스를 오래 사용하지 않아도 그대로 두면 Render 월 사용료는 계속 나온다. 사용하지 않는 동안 완전히 0원으로 만들려면 SQLite DB를 외부에 백업하고 실제 복원까지 확인한 뒤 **Render 서비스와 디스크를 함께 삭제**해야 한다.

## 2. 첫 배포 뒤 주소 연결

1. Render가 보여준 `https://...onrender.com` 주소를 복사한다.
2. Google Cloud의 승인된 리디렉션 URI에 `https://...onrender.com/auth/callback`을 추가한다.
3. Render의 `GOOGLE_REDIRECT_URI`에도 똑같은 주소를 넣고 다시 배포한다.
4. `/healthz`가 정상 응답하는지 확인한다.
5. 관리자 이메일로 로그인하고, 다른 이메일은 막히는지 확인한다.

서비스 이름 `company-analysis-beta`가 이미 사용 중이면 Render에서 이름을 바꿔도 된다. 이때 바뀐 주소를 Google 설정에도 똑같이 반영한다.

## 3. 안전한 데모 상태에서 먼저 확인

처음에는 다음 값이 자동으로 들어간다.

```text
PIPELINE=demo
BETA_ADMIN_ONLY=1
```

이 상태에서는 관리자만 접속할 수 있고 AI 조사 비용은 발생하지 않는다. Render 서버·디스크 요금은 별도다. 로그인, 저장, 보고서 화면, 워드 다운로드, 노션 전송을 먼저 확인한다.

## 4. 첫 외부 백업 확인

Render의 **Shell**에서 다음 명령으로 실행 중인 SQLite를 안전하게 백업한다.

```console
python tools/backup_sqlite.py backup
```

`/var/data/backups`에 생긴 `.sqlite3` 파일과 같은 이름의 `.sha256` 파일을 둘 다 내려받는다. Render의 **Connect → SSH**에 나온 접속 대상이 `srv-xxxx@ssh.singapore.render.com`이라면, 내 컴퓨터에서 파일마다 다음처럼 실행한다. `srv-xxxx`와 파일명은 화면에 나온 실제 값으로 바꾼다.

```console
scp -s srv-xxxx@ssh.singapore.render.com:/var/data/backups/storage-backup-날짜와시간.sqlite3 .
scp -s srv-xxxx@ssh.singapore.render.com:/var/data/backups/storage-backup-날짜와시간.sqlite3.sha256 .
```

두 파일을 받은 뒤 로컬에서 `verify`가 통과하는지 확인해야 백업 완료다. 이 전송은 실행 중인 유료 서비스에서 해야 하며, 임시 작업 인스턴스에서는 영속 디스크가 보이지 않는다. 자세한 보관·복구 순서는 `app/docs/장기_휴면_백업.md`를 따른다.

## 5. 실제 조사를 켤 때

API 키와 비용 안전장치를 확인한 뒤 Render 환경변수의 `PIPELINE`만 `real`로 바꾸고 재배포한다. 실제 회사 확인, 이미지 글자 추출, 보고서 생성은 API 비용이 발생한다.

주의: Starter는 메모리가 512MB다. 로컬 근사 측정에서 데모는 약 58MB였지만 실제 조사 엔진과 한국어 개인정보 삭제 모델을 함께 올리면 약 410MB까지 사용했다. 큰 이미지를 처리할 때는 메모리가 부족해 서버가 재시작될 수 있으므로, 처음에는 작은 이미지 한 건으로 시험하고 Render의 **Metrics → Memory**를 확인한다. 메모리 한도에 가까우면 정식 공개 전에 더 큰 요금제로 올리거나 이미지 크기·동시 처리 수를 줄이는 결정을 해야 한다.

시험을 마치면 다음 중 하나로 되돌린다.

- AI API 비용을 막기: `PIPELINE=demo` (Render 서버·디스크 요금은 계속 발생)
- 외부 공개 전까지 관리자만 사용: `BETA_ADMIN_ONLY=1` 유지
- 정식 공개할 때: 충분히 확인한 뒤 `BETA_ADMIN_ONLY=0`

## 6. 배포 설정에서 바꾸면 안 되는 값

- Uvicorn worker: `1`
- Render instance 수: `1`
- 영속 디스크 위치: `/var/data`

SQLite 영속 디스크는 한 서버만 사용해야 한다. worker나 instance를 늘리면 메모리 작업 상태와 비용 장부가 서로 달라질 수 있다.

공식 참고 문서: [Render Blueprint](https://render.com/docs/blueprint-spec), [Render 영속 디스크와 파일 전송](https://render.com/docs/disks), [Render SSH](https://render.com/docs/ssh), [Render 상태 확인](https://render.com/docs/health-checks)
