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
- 비밀값과 승인 참여자 식별자는 Git, 채팅, 티켓, 화면 캡처에 남기지 않는다.

## 활성 제품 계약

신규 분석 입력은 **회사명 필수 + 주소 힌트 선택**이다. 주소를 비워도 회사 후보 확인과
조사가 진행되어야 한다. 채용공고, 공고 이미지, OCR, 직무·개인 맞춤 입력은 신규 흐름의
입력이 아니다.

화면, PDF, Notion은 서로 다른 보고서를 만들지 않는다. `report_standard`를 통과한 같은
정본 보고서를 렌더링하며, **PDF가 사용자 다운로드와 출고 승인의 파일 정본**이다.
Notion 전송은 PDF가 세 독립 승인을 받고 `finalize`된 뒤에만 같은 내용을 보낸다.

## 처음 배포하는 순서

1. GitHub Actions `quality-gate`가 초록색인지 확인한다.
2. Render에서 저장소 루트의 `render.yaml`로 Blueprint를 만든다.
3. 아래 환경변수를 Render 대시보드에 직접 입력한다.
4. Render가 발급한 HTTPS 주소를 Google OAuth와 공유 링크 기준 주소에 반영한다.
5. `/healthz`, 관리자 로그인, 비관리자 차단을 확인한다.
6. `PIPELINE=demo`에서 회사명만 입력한 경우와 주소 힌트를 함께 입력한 경우를 각각 시험한다.
7. PDF 준비 → 참여자 원장 → 세 승인 → `finalize` → 다운로드·Notion 흐름을 시험한다.
8. 첫 SQLite 백업을 내려받아 해시 검증하고, 비밀값 복구 묶음을 별도로 확인한다.

## 필수·조건부 환경변수

| 이름 | 배포 값과 보관 원칙 |
|---|---|
| `ADMIN_EMAILS` | 관리자 Google 이메일. 여러 명이면 쉼표로 구분 |
| `GOOGLE_CLIENT_ID` | 배포용 Google OAuth 클라이언트 ID |
| `GOOGLE_CLIENT_SECRET` | 비밀 관리자에 보관하는 OAuth 비밀 |
| `GOOGLE_REDIRECT_URI` | `https://<service-host>/auth/callback` |
| `SHARE_PUBLIC_BASE_URL` | 검증된 공개 HTTPS origin. 경로·쿼리 없이 `https://<service-host>` 형식 |
| `PDF_RELEASE_PARTICIPANTS` | `author·producer·fact·editorial·visual` 다섯 역할과 Google OAuth 불변 `sub`를 연결한 JSON |
| `PROVENANCE_SEAL_SECRET` | 모든 재배포·worker·복구에서 동일하게 유지할 32바이트 이상의 무작위 비밀 |
| `ANTHROPIC_API_KEY` | `PIPELINE=real`에서 생성 모델을 사용할 때만 |
| `DART_API_KEY` | `PIPELINE=real`에서 전자공시를 조회할 때만 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | `PIPELINE=real`에서 뉴스 검색을 사용할 때만 |
| `NOTION_TOKEN` / `NOTION_PARENT_PAGE_ID` | 승인 완료 보고서를 Notion으로 보낼 때만 |

`AUTH_COOKIE_INSECURE`와 로컬 관리자 capability는 로컬 전용이다. Render에는 설정하지 않는다.
실제 값과 JSON 예시는 `app/.env.example`의 설명을 따르되 실제 사람의 `sub`나 비밀값을
파일에 복사하지 않는다.

### 승인 참여자와 provenance 비밀 준비

1. 다섯 참여자가 배포용 Google OAuth로 로그인한 뒤 운영자가 계정의 불변 `sub`를
   신원 확인과 함께 수집한다.
2. `author`, `producer`, `fact`, `editorial`, `visual` 각 역할에 식별자를 배정해
   `PDF_RELEASE_PARTICIPANTS` JSON을 만든다.
3. `fact`, `editorial`, `visual`은 서로 다른 사람이어야 하고, `author` 또는
   `producer`와도 겹치면 안 된다.
4. 비공개 로컬 터미널에서 다음처럼 32바이트보다 큰 무작위 값을 만든다.

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

5. 출력값을 즉시 조직 비밀 관리자에 `PROVENANCE_SEAL_SECRET`으로 저장하고 Render에
   같은 값을 넣는다. 터미널 기록, 채팅, 문서, 저장소에는 복사하지 않는다.
6. 참여자 JSON과 비밀값의 소유자·마지막 검증일·복구 담당자를 비밀 관리자의 같은
   복구 항목에 기록한다.

## 주소와 Google OAuth 연결

1. Render의 `https://...onrender.com` 주소 또는 검증된 사용자 도메인을 확정한다.
2. Google Cloud의 승인된 리디렉션 URI에
   `https://<service-host>/auth/callback`을 등록한다.
3. Render의 `GOOGLE_REDIRECT_URI`와 `SHARE_PUBLIC_BASE_URL`을 같은 host 기준으로
   갱신하고 재배포한다.
4. OAuth 동의 화면이 Testing 상태라면 운영 관리자와 승인 참여자를 테스트 사용자에 넣는다.
5. `/healthz` 응답, HTTPS, 로그인 callback, 관리자 허용·비관리자 거절을 확인한다.

서비스 이름이나 사용자 도메인이 바뀌면 세 값과 Google 등록 URI를 함께 바꾼다. 예전
공유 링크를 새 host로 자동 추정하지 않는다.

## 데모에서 먼저 확인할 것

`PIPELINE=demo`와 `BETA_ADMIN_ONLY=1`에서는 저장된 데모 자료만 사용하므로 외부 조사
API 비용이 발생하지 않는다. 다음을 확인한다.

- 회사명만 입력해 후보 확인부터 결과까지 완료
- 선택 주소 힌트를 넣었을 때 동일 회사로 식별
- 비관리자 접근 차단과 공유 링크 만료·철회
- 정본 화면과 PDF 내용 일치
- 승인 조건을 충족하지 않은 PDF 다운로드·Notion 전송 차단

`BETA_ADMIN_ONLY=0`은 운영 승인과 공개 전 체크리스트를 마친 뒤에만 사용한다.

## PDF 승인과 Notion 전송

활성 출고 순서는 다음 하나뿐이다.

```text
report_standard 통과
  → PDF prepare 및 파일 해시 고정
  → author·producer·fact·editorial·visual 참여자 원장 결속
  → fact·editorial·visual 세 사람이 같은 해시에 독립 승인
  → finalize
  → PDF 다운로드 허용
  → 설정된 경우 같은 정본을 Notion으로 전송
```

- 세 검토자는 서로 달라야 하며 `author`·`producer`가 검토자를 겸할 수 없다.
- 승인 뒤 PDF 바이트가 하나라도 바뀌면 해시가 달라지므로 기존 승인은 무효다.
- 참여자 원장, 세 승인 또는 provenance seal 중 하나라도 없으면 fail-closed한다.
- Notion은 별도 내용을 생성하는 채널이 아니며 `finalize` 전 보고서를 받을 수 없다.

## 실제 조사로 전환

사용자가 외부 호출과 예상 비용을 승인하고 provider 비밀과 예산 한도를 확인한 뒤에만
`PIPELINE=real`로 바꾼다. 작은 회사 입력 한 건으로 DART·뉴스·홈페이지 수집, 정본
게이트, PDF 승인 흐름을 끝까지 시험한 뒤 범위를 늘린다.

실제 조사도 입력 계약은 회사명과 선택 주소뿐이다. 개인정보나 채용공고 원문을 넣지
않는다. Google Places 후보 검색은 결과 보관·표시 약관 검토가 완료될 때까지 활성화하지
않는다.

## 데이터 백업과 비밀 복구

Render Shell에서 SQLite 일관성 백업을 만든다.

```console
python tools/backup_sqlite.py backup
```

`/var/data/backups`의 `.sqlite3`와 같은 이름의 `.sha256`을 함께 내려받아 로컬에서
검증한다. 상세 절차는 [장기 휴면 백업](장기_휴면_백업.md)을 따른다. DB 백업에는 환경
비밀이 들어 있지 않으므로 다음 **복구 묶음**을 조직 비밀 관리자에 별도로 보관한다.

- Google OAuth ID·비밀과 승인 URI
- `SHARE_PUBLIC_BASE_URL`
- `PDF_RELEASE_PARTICIPANTS`
- `PROVENANCE_SEAL_SECRET`
- real provider 키와 Notion 설정(사용하는 경우)
- 각 값의 소유자, 회전일, 마지막 복구 시험일

복구 영향은 다음과 같다.

- `PROVENANCE_SEAL_SECRET`을 잃거나 다른 값으로 복구하면 기존 provenance seal과 캐시를
  신뢰할 수 없어 기존 보고서 출고가 차단된다. 임시 새 키로 우회하지 말고 원래 값을 복구한다.
- `PDF_RELEASE_PARTICIPANTS`를 잃거나 `sub`가 달라지면 새 승인을 받을 수 없다. 역할
  재지정은 신원 확인과 운영 승인 뒤 별도 변경으로 기록한다.
- `SHARE_PUBLIC_BASE_URL`은 현재 검증 도메인에서 다시 정할 수 있지만, 누락·오설정
  동안 공개 공유 URL 발급을 중단하고 기존 링크의 host 영향을 점검한다.
- OAuth, provider, Notion 비밀이 유출되었거나 복구 여부가 불명확하면 먼저 회전하고
  callback·권한·전송을 다시 시험한다.

## 공개 전 체크리스트

- [ ] GitHub Actions `quality-gate`와 Docker `/healthz` 통과
- [ ] `PIPELINE=demo`, `BETA_ADMIN_ONLY=1`에서 첫 검증
- [ ] 회사명 단독 입력과 선택 주소 입력 모두 통과
- [ ] 관리자·비관리자·공유 링크 권한 확인
- [ ] 다섯 역할이 올바른 불변 `sub`에 연결됨
- [ ] 세 독립 검토자가 같은 PDF 해시에 승인하고 `finalize` 성공
- [ ] 승인 전 다운로드·Notion 전송이 차단됨
- [ ] SQLite 백업과 SHA-256 검증 완료
- [ ] 비밀 복구 묶음의 소유자와 복구 시험일 확인
- [ ] worker `1`, instance `1`, 디스크 `/var/data` 유지
- [ ] real 전환 시 provider 비용과 예산 한도 별도 승인

공식 참고: [Render Blueprint](https://render.com/docs/blueprint-spec),
[Render 영속 디스크](https://render.com/docs/disks),
[Render SSH](https://render.com/docs/ssh),
[Render 상태 확인](https://render.com/docs/health-checks),
[Google OAuth 웹 서버](https://developers.google.com/identity/protocols/oauth2/web-server)
