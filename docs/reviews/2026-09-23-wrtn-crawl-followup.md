# 뤼튼 기본 공식 웹 수집 경로 후속 검증

**현재 기본 수집기는 공개 뉴스룸 약칭 정의까지 페이지 상한 안에서 도달한다. 수집 우선순위 수정은 하지 않는다.** `282fcf05` 위에서 공개 HTML 응답을 재생하면 뉴스룸은 네 번째 HTML 조회이고, `공식 웹 근거 문서 2개`와 `truncated_page_cap=2`가 동시에 나타나면서 약칭 `뤼튼` 1개가 연결된다. 이번 변경은 이 결과를 재현하는 홈페이지 시험과 이 문서뿐이다.

대상 운영 기록은 배포 `fb6cc61`, 실행 `5cdd5383931d2d82b0eaf6adaa9d8307`의 이전과 같은 세 입력 파일이다. 원본 경로·SHA-256 및 이전 위치 계약 수정은 [선행 보고서](2026-09-23-wrtn-live-news.md)에 기록했다. 세 원본, 기존 `app/conftest.py`, 제품 코드, 페이지·모델 호출 상한은 수정하지 않았다.

## 공개 응답과 재생 조건

2026-09-23 [공식 홈](https://wrtn.io/), [뉴스룸](https://wrtn.io/news/), [회사소개](https://wrtn.io/company/), 채용·영문·일문·서비스 경로와 robots/sitemap을 로그인·쿠키 없는 일반 공개 GET으로 읽었다. 루트·회사소개·뉴스룸은 선행 작업의 공개 보존본을 재사용했고, 기본 수집기가 요구한 주변 응답은 추가 확보했다. 운영 세션이나 DB, 회사 조회 API, 모델 API를 사용하지 않았다.

21개 공개 응답의 원문·최종 URL·상태·콘텐츠 형식·SHA-256을 `app/.local_evaluation_runs/wrtn-crawl-replay-20260923/manifest.json`에 보존했다. 일부 파일은 같은 작업폴더의 선행 공개 스냅샷 디렉터리를 참조한다. Manifest SHA-256은 `4ee26176ee3de4e38142d3451a3f839e391cbf855efdc83ebe329cccf949d9fd`이다. 전체 원문은 Git에 추가하지 않으며, [완료된 재생 결과](../../app/.local_evaluation_runs/wrtn-crawl-replay-20260923/result.json)와 재생 스크립트도 같은 로컬 디렉터리에 있다.

재생은 현재 `collect_official_web_documents`를 호출하고 모든 전송 함수를 보존 응답으로 주입한다. `root_identity_verification_required`를 기본값 `True`로 유지했다. 운영 보고서에 있는 법인명·법인 ID·홈페이지와 공개 footer의 등록번호를 입력했으며, 법인명과 번호가 실제 HTML에서 함께 확인되어 `root_identity_verified=1`이 나온다. `root_identity_name_only=0`이고, 수집기가 생성한 identity binding도 실측 회사소개 인용에 남은 결속 문자열과 일치했다. 이 재생 입력이 당시 기업개황 응답 전체를 복원했다는 뜻은 아니다.

`git diff fb6cc61 282fcf05 -- app/src/features/homepage`는 비어 있다. 즉 두 커밋의 홈페이지 수집 순서는 동일하며, 선행 수정은 확보한 공식 조각의 위치 검증과 약칭 전달만 바꾼다.

이번 전체 수집기가 실제 법인 신원을 확인해 만든 **동일한 typed 결과**에 `git show fb6cc61:app/src/features/pipeline/official_news_aliases.py`의 배포판 함수를 적용하면 약칭 0개, 현재 `282fcf05` 함수를 적용하면 `뤼튼` 1개다. [경계 전후 결과](../../app/.local_evaluation_runs/wrtn-crawl-replay-20260923/alias-boundary-comparison.json)를 보존했다. 추가 수집 우선순위 변경 없이 선행 위치 계약 수정만으로 이 재생의 약칭 누락이 해소된다.

## 기본 수집의 실제 방문 순서

robots·sitemap 조회를 뺀 HTML 요청 순서는 다음과 같다. 목록은 **공개 응답 재생의 호출 기록**이며 운영 당시 실제 URL 목록으로 단정하지 않는다.

| 페이지 순번 | 요청 URL |
|---:|---|
| 1 | `https://wrtn.io/` |
| 2 | `https://wrtn.io/careers/` |
| 3 | `https://career.wrtn.io/o/119686` |
| 4 | `https://wrtn.io/news/` |
| 5 | `https://wrtn.io/en/news-en/` |
| 6 | `https://wrtn.io/company/` |
| 7 | `https://wrtn.io/en/company-en/` |
| 8 | `https://wrtn.io/en/service-crack-en/` |
| 9 | `https://wrtn.io/en/service-kyarapu-en/` |
| 10 | `https://wrtn.io/en/careers-en/` |
| 11 | `https://wrtn.io/ja/careers-ja/` |
| 12 | `https://wrtn.io/ja/news-ja/` |

페이지 요청은 기본 상한 12회다. 일반 웹 전송 함수는 HTML 12회와 robots/sitemap 4회를 합쳐 16회, 별도의 기존 IR HTML 경로는 4회 호출됐다. 전부 메모리/파일 대역 호출이며 이 시험의 실제 네트워크·모델 호출은 0회다. 우선순위나 quota, URL 추출, 원문 검증을 바꾸지 않았다.

뉴스룸을 읽은 뒤에도 다른 장의 최소 몫과 영문·일문 경로가 예산을 사용한다. 뉴스룸이 이미 네 번째에 읽혔으므로 나중의 페이지 상한 도달을 뉴스룸 누락으로 해석할 수 없다.

## 문서 2개와 절단 2건의 의미

| 관측 | 공개 응답 기본 재생 | 배포 후 진단 |
|---|---:|---:|
| 공식 웹 `page_ok` | 7 | 7 |
| 공식 채용 `page_ok` | 3 | 3 |
| 공식 채용 `network_failed` | 1 | 1 |
| `robots_ok` | 2 | 2 |
| `sitemap_ok` / `sitemap_failed` | 1 / 1 | 1 / 1 |
| `root_identity_verified` | 1 | 1 |
| `truncated_page_cap` | 2 | 2 |
| `ir_pdf_none` / `ir_pdf_failed` | 1 / 1 | 1 / 1 |
| 최종 공식 웹 근거 문서 | 2 | 2 |

재생 수집기는 11개 문서를 만들지만, `wide_evidence_mapping.to_evidence_mappings`는 실제 장별 근거 조각이 있는 문서만 `documents`에 넘긴다. 나머지는 원문 없는 provenance 차선으로 분리된다. 이 재생에서 최종 두 문서는 **뉴스룸과 회사소개**다. 따라서 `문서수=2`는 HTML 요청을 2회만 했다는 뜻이 아니다.

`truncated_page_cap=2`도 두 문서를 읽지 못했다는 수가 아니다. 일반 큐 순회가 같은 전역 `pages_fetched=12`에서 한 번, 남은 도메인 신원 후보 순회가 같은 전역 상한에서 또 한 번 절단을 기록한다. 두 집계만으로 어느 URL이 빠졌는지 알 수 없다.

재생에서는 공개 `wrtn.io/sitemap.xml`의 최종 URL이 `https://wrtn.io/sitemap_index.xml`이고, 기존 infrastructure URL 경계가 이를 거절해 sitemap 실패가 된다. 채용 요청은 `https://career.wrtn.io/ko/o/119686`으로 이동하며 기존 경로 경계가 거절해 전송 실패가 된다. 기존 검증을 그대로 실행해 얻은 결과이고 이 범위를 넓히지 않았다. 운영 집계와 일치하더라도 당시 실패 URL이 반드시 이 두 주소였다고 확정하지 않는다.

현재 회사소개 본문 지문 `6dda01a5e69e994271a785cf31f69e34fc94e9c2393849b8ad9abe01609702a7`은 운영 보고서와 직접 일치한다. 뉴스룸 본문 지문은 `0826c07e5ec055c091135727dcb78af2587054a871f617179640e4fec824d530`이며, 조각의 명시 정의를 `282fcf05`가 읽어 `뤼튼`을 추가한다. 운영 전체 공식 조각이 없으므로 뉴스룸 문서의 운영 당시 지문 일치까지 검증한 것은 아니다.

## 원문 비일치 4건에 관해 알 수 있는 것

제공된 이전/이후 진단과 이후 보고서를 재귀 검사했다. 세 파일 모두 `entity_evidence`, `body`, `articles`, `excerpts`, `same_company`, `aliases`, `identity_context`라는 입력/원응답 필드는 0개다. 기사별 ID·원문·모델 반환값의 대응표가 없어 개별 오거부 재생은 불가능하다.

`news_intake/grounded.py`의 현재 판정 순서로 알 수 있는 범위는 다음과 같다.

- 이 4건은 모델 결과 구조·기사 ID 검사를 통과했고 모델이 `same_company=true`, `material=true`로 반환한 항목이다. 모델의 판단이 사실이었다는 뜻은 아니다.
- `entity_evidence`는 비어 있지 않은 문자열이고 1,000자 상한 안에 있었으나, 읽은 해당 기사 본문의 정확한 연속 문자열은 아니었다.
- 현재 코드에는 이미 같은 기사의 독립 검증된 인용문으로 신원을 보완하는 경로가 있다. 성공하면 `identity_recovered_from_excerpt`가 남는다. 실패 4건으로 집계됐다는 사실은 그 기존 경로에서도 복구할 근거를 얻지 못했다는 뜻이다.
- 복구 실패 시 임시 `article_excluded` 세부 원인은 전체 제외 집계에 합쳐지지 않는다. 따라서 별도로 기록된 `grounded_text_not_exact=2`가 이 4건과 어떻게 겹치는지 계산할 수 없다.

공백 차이, 문장 합침, 생성한 설명, 본문 절단, 인용 주체·맥락 불일치 중 무엇이 개별 원인인지 지금 자료로 가를 수 없다. 뉴스룸 수집 여부나 약칭만으로 `identity_evidence_not_exact=4`를 설명할 수도 없다. 확인에는 당시 기사 ID에 연결된 실제 body, entity_evidence, excerpts와 실제 회사 이름·별칭·신원 문맥이 필요하다. 추가 저장·관측 구현은 이번 소유 범위를 넘어 수행하지 않았다.

## 검증 결과와 전달

새 [공개 수집 재생 시험](../../app/src/features/homepage/tests/test_wrtn_public_crawl.py)은 실제 보존 HTML을 읽고 SHA-256을 확인한 뒤 기본 수집기 → 공식 웹 매핑 → typed 경계 → 약칭 추출까지 실행한다. 공개 응답 그대로의 경우와 다른 sitemap에 추가 장애를 주입한 대조 모두 뉴스룸 도달·페이지 12회·일반 웹 전송 16회·IR HTML 4회·약칭 1개를 유지했다. 후자의 장애 URL은 시험 대역이며 운영 실패 URL 추정이 아니다.

지정 Python으로 기존 conftest를 유지하고 `PYTHON_DOTENV_DISABLED=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, 임시 DB·진단 경로를 적용했다. 이 작업폴더의 `app`에서 실행한 명령은 다음과 같다.

```text
.venv/Scripts/python.exe -m pytest src/features/homepage/tests/test_wrtn_public_crawl.py src/features/homepage/tests/test_wide_page_budget.py -q --tb=short
```

**13개 통과, 1개 건너뜀.** 건너뛴 것은 기존 실시간 사이트 통합시험으로, 활성화 환경변수가 없어서 실행하지 않았다. 새 공개 스냅샷 시험 2개는 모두 실행·통과했다. 새 시험은 `local_integration` 표시가 있고 보존 자료가 없는 환경에서는 명시적으로 건너뛰며, 다른 위치의 스냅샷은 `WRTN_PUBLIC_CRAWL_FIXTURE_DIR`로 지정할 수 있다.

이번 작업은 수집 순서 변경 불필요라는 결론이다. 선행 `282fcf05`의 위치 계약 수정은 여전히 필요하지만, 새로운 우선순위 변경·추측 복구·원문 일치 완화는 추가하지 않았다. 배포 후 실제 기사 채택 복구 및 기존 4건의 개별 원인은 총괄의 별도 검증으로 남는다. 유료 실행·비용 상한 확대·배포·push·비밀/DB 접근은 하지 않았고, 진행과 완료 보고에는 Orca orchestration 스킬의 CLI만 사용했다.
