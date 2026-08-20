# 개발·기획 검수 안내

이 문서는 이 작업 폴더를 직접 연 검수자가 제품 계약, 실행, 검증, 출고와 운영 한계를
한 번에 확인하는 시작점이다. 현재 상태가 바뀌면 날짜별 안내를 늘리지 말고 이 파일을
갱신한다.

## 1. 먼저 읽을 정본

1. [문서 지도](README.md)와 권위 순서
2. [출력물 기준](출력물%20기준/README.md)
3. [런타임 출고 계약](출력물%20기준/90_공통_규칙/런타임_출고_계약.md)
4. [시스템 개요](architecture/system-overview.md)와
   [기능별 책임 지도](architecture/feature-map.md)
5. [웹서비스 실행 안내](../app/README.md)와
   [분석 엔진 안내](../analysis_engine/README.md)

`docs/출력물 기준/`의 20개 문서가 내용·목차·PDF 품질의 정본이다. 날짜가 붙은
`research/`와 `reviews/` 문서는 과거 스냅샷이며 현재 계약이나 시험 수를 덮어쓰지 않는다.

## 2. 현재 제품 계약

- 입력: **회사명 필수, 주소 힌트 선택**
- 제외: 채용공고, 공고 이미지/OCR, 직무·개인 맞춤, 자소서·면접 답안
- 스키마: `Report.schema_version=company-report-v3-canonical`
- 구조: 핵심 요약, 1~9장, 출처·검증 부록의 고정 정본
- 근거: 공식 자료 우선, 외부 자료는 교차검증, 사실 원장 단일 소유
- 비교: 양사 공식 근거의 지표·기간·연결/별도 범위가 맞지 않으면 출고 차단
- 출력: 승인된 같은 보고서를 웹·PDF·Notion에 동등 렌더
- 파일 정본: 세 독립 승인 뒤 `finalize`된 PDF

주소가 비어도 회사 식별부터 결과까지 진행되어야 한다. 화면이나 코드가 지역·주소를
필수로 요구하면 정본을 바꾸지 말고 release blocker로 처리한다. 구형 DOCX/Word와
채용 결합 필드는 호환 코드일 뿐 신규 조사·캐시·출력에 연결하지 않는다.

## 3. 저장소와 실행 경계

- `app/`: FastAPI 화면, 인증, 비용·공유 제어, pipeline 조립, 저장, PDF·Notion
- `analysis_engine/`: 실제 회사 식별·자료 수집·판정 엔진
- `docs/출력물 기준/`: 사람이 읽는 내용·출고 정본
- `render.yaml`: 관리자 전용 첫 배포와 단일 SQLite 인스턴스 설정
- `.github/workflows/quality-gate.yml`: app·engine 회귀 시험과 Docker health 확인

`analysis_engine`은 독립 설치 패키지가 아니다. `app/src/features/pipeline/real.py`가
저장소 배치를 전제로 경로를 추가해 동적으로 읽으므로 디렉터리를 삭제하거나 Docker
allowlist에서 빼면 `PIPELINE=real`이 깨진다.

## 4. 환경과 비밀값

| 범위 | 핵심 값 |
|---|---|
| 안전한 첫 실행 | `PIPELINE=demo`, `BETA_ADMIN_ONLY=1` |
| 배포 인증 | `ADMIN_EMAILS`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` |
| 공유 링크 | `SHARE_PUBLIC_BASE_URL` |
| PDF 승인 | `PDF_RELEASE_PARTICIPANTS`, `PROVENANCE_SEAL_SECRET` |
| 실제 조사 | `DART_API_KEY`, `ANTHROPIC_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` |
| 선택 전송 | `NOTION_TOKEN`, `NOTION_PARENT_PAGE_ID` |

형식과 조건은 [`app/.env.example`](../app/.env.example), Render 생성·보관·복구 절차는
[Render 운영 배포](../app/docs/Render_배포.md)를 따른다. 비밀값, 실제 참여자 `sub`,
로컬 관리자 URL을 Git·문서·메신저·화면 캡처에 남기지 않는다.

## 5. 안전한 로컬 실행

Python 3.13을 사용한다.

```powershell
cd app
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\로컬데모켜기.ps1
```

로컬 데모는 외부 provider를 호출하지 않고 `app/.local_demo/`에 격리한다. 임의 회사의
실제 흐름을 보기 위한 기본 성능시험도 먼저 비용 없는 미리보기로 실행한다.

```powershell
.\실시간성능시험켜기.ps1 -Port 8020
```

유료 provider 호출은 사용자가 비용 발생을 명시적으로 승인하고 현재 PowerShell
프로세스에 네 provider 비밀을 넣은 경우에만 `-EnablePaidProviders`를 붙인다.
실행기가 출력하는 로컬 관리자 URL은 bearer 권한과 같으므로 공유하지 않고 종료 뒤
폐기한다.

## 6. 검증 기준과 갱신 명령

현재 인수 기준 스냅샷은 다음과 같다.

- app 전체 회귀: **2121 passed, 3 skipped, 24 warnings** (`73.80s`, exit `0`)
- analysis_engine 회귀: **135 passed** (`12.78s`, exit `0`)
- 두 묶음 합계: **2256 passed, 3 skipped**, 실패 `0`
- 독립 감사: **A PASS, B PASS**

이 숫자는 코드와 문서 정리 시점의 스냅샷이다. 변경 뒤 아래 두 명령을 각각 실행하고
실패 원인을 해결한 다음, 합산 결과와 warning을 이 섹션에 갱신한다.

```powershell
# app 폴더
$env:TLDEXTRACT_CACHE="$PWD\.cache\tldextract"
.\.venv\Scripts\python -m pytest src tools/tests -q `
  --basetemp=.pytest_tmp_handoff_app

# 저장소 루트
cd ..
.\app\.venv\Scripts\python -m pytest analysis_engine/src -q `
  --basetemp=app/.pytest_tmp_handoff_engine
```

CI와 같은 최종 확인은 GitHub Actions `quality-gate`에서 app·engine 시험 뒤 Docker
이미지를 만들고 `/healthz`까지 통과했는지 본다. 독립 A/B 판정의 전제가 바뀌는
내용·보안·PDF 변경이면 해당 검토도 다시 수행해 PASS 근거를 갱신한다.

## 7. PDF 출고 승인

```text
report_standard 통과
  → PDF prepare와 SHA-256 고정
  → author·producer·fact·editorial·visual 다섯 역할 결속
  → fact·editorial·visual 세 독립 승인
  → finalize
  → PDF 다운로드 및 선택적 Notion 전송
```

세 검토자는 서로 다른 불변 사용자 `sub`여야 하고 `author`·`producer`와 겹칠 수 없다.
세 사람은 같은 PDF 해시에 승인해야 한다. 바이트 변경, 참여자 원장 불일치, provenance
seal 실패가 있으면 기존 승인을 재사용하지 않고 fail-closed한다.

## 8. 배포·백업·복구

- 첫 Render 배포: `PIPELINE=demo`, `BETA_ADMIN_ONLY=1`
- Uvicorn worker `1`, Render instance `1`, 영속 디스크 `/var/data`
- SQLite 백업과 `.sha256`을 함께 내려받아 검증
- DB와 별도로 OAuth·provider·Notion 비밀, `SHARE_PUBLIC_BASE_URL`,
  `PDF_RELEASE_PARTICIPANTS`, `PROVENANCE_SEAL_SECRET`을 비밀 관리자에 보관

`PROVENANCE_SEAL_SECRET`을 잃거나 바꾸면 기존 seal·캐시의 신뢰를 복구할 수 없어
출고가 차단된다. 참여자 JSON을 잃거나 `sub`가 달라지면 새 PDF 승인을 받을 수 없다.
상세 절차는 [Render 운영 배포](../app/docs/Render_배포.md)와
[장기 휴면 백업](../app/docs/장기_휴면_백업.md)을 따른다.

## 9. 알려진 운영 한계

- SQLite 계약 때문에 multiworker·다중 instance 운영은 지원하지 않는다.
- Google Places 후보 검색은 결과 보관·표시 약관 검토가 끝날 때까지 잠겨 있다.
- 실제 OAuth, provider, Notion 계정과의 staging smoke test는 배포 환경에서 별도로
  수행해야 하며 로컬 mock PASS로 대체하지 않는다.
- 비용 원장은 예상비용 기반 운영 차단이며 provider 청구액의 절대 hard cap이 아니다.
- PDF 자동 구조·시각 검사가 실제 screen reader, 인쇄 장치, 모든 PDF/UA 조건을 완전히
  대신하지 않는다.
- 공유 링크는 capability다. 유출되면 만료를 기다리지 말고 즉시 철회한다.
- 실행 중 provider thread는 취소 신호만으로 즉시 끝나지 않을 수 있으므로 종료·재배포
  때 진행 중 작업과 원장을 확인한다.

## 10. 안전 규칙

- 외부 호출·유료 API·실제 계정 전송은 명시적 승인 없이 실행하지 않는다.
- 실제 DB, `.env`, API/OAuth/Notion 비밀, 백업, 사용자 식별자를 커밋하지 않는다.
- 조사 원문을 공개 저장소에 재배포하지 않고 정제 요약과 검증 해시만 남긴다.
- 정본을 통과하지 않은 초안, 구형 캐시, 승인 전 PDF를 공개·다운로드·Notion 전송하지 않는다.
- 기존 사용자 변경이 있는 dirty worktree에서 무관한 파일을 되돌리거나 삭제하지 않는다.

## 11. 검수 체크리스트

- [ ] 문서 지도와 출력물 기준 20개를 읽음
- [ ] 회사명 단독 입력과 선택 주소 입력을 각각 확인
- [ ] `PIPELINE=demo` 로컬 실행과 관리자 URL 폐기 확인
- [ ] app·analysis_engine 회귀 시험을 새로 실행하고 이 문서의 숫자를 갱신
- [ ] GitHub Actions와 Docker `/healthz` 확인
- [ ] 다섯 역할 `sub`와 세 검토자의 상호 배타성 확인
- [ ] 동일 PDF 해시의 세 승인 → `finalize` → 다운로드·Notion 차단/허용 확인
- [ ] SQLite 백업 해시 검증과 비밀 복구 묶음 확인
- [ ] 운영 한계와 미완료 staging 시험을 이슈·release 판단에 반영
- [ ] 날짜별 리뷰를 현재 정본이나 최신 PASS 증거로 인용하지 않음
