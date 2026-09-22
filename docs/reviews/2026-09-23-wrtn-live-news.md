# 뤼튼 실측 뉴스 0건 후속 재현 및 최소 수정

배포 `fb6cc61`, 실행 `5cdd5383931d2d82b0eaf6adaa9d8307`을 조사했다. **현행 공식 웹 위치 계약과 약칭 추출기의 구형 위치 검증이 충돌하는 결함을 공개 원문으로 재현하고 수정했다.** 뉴스룸 공개 HTML을 현행 생산기로 변환한 동일 조각 3개에 배포판 약칭 코드를 적용하면 약칭 0개, 수정판을 적용하면 `뤼튼` 1개다. 운영 당시 개별 기사 본문·모델 응답·전체 공식 수집 객체는 제공된 파일에 없으므로, 실측 외부 기사 0건의 모든 원인이 밝혀졌거나 이번 수정으로 운영 채택이 복구됐다고 결론내리지 않는다.

## 실측 입력과 확인 범위

원본 디렉터리는 `C:/Users/jh-wo/.claude/workspace/기업분석2/app/.local_evaluation_runs/deployment-20260923`이다. 세 원본은 읽기만 했다.

| 파일 | SHA-256 |
|---|---|
| `wrtn-before-diagnostics.json` | `b16a3615938ccd559a73ae287f9fe29564d7989983bcf69948b407521491880d` |
| `wrtn-after-diagnostics.json` | `8a13a38c6a69fbfce38abc388ed7f65282271ddd01fde2ae0c396f3a8898cd80` |
| `wrtn-after-report.json` | `a18335ebd204512c7fbade5210605491d3a1ce3ed34e40b62ba9c88f3891a775` |

| 관측 | 배포 전 | 배포 후 |
|---|---:|---:|
| 검색 반환 / 검색 호출 | 200 / 11 | 200 / 11 |
| 선별 후보 | 56 | 59 |
| 본문 시도 기사 / 본문 읽음 | 20 / 15 | 15 / 12 |
| 분석 AI 호출 / 상한 | 3 / 3 | 3 / 3 |
| 법인 근거 비일치 | 4 | 4 |
| 법인 이름 미확인 | 1 | 1 |
| 독립 기사 / 뉴스 조각 | 0 / 0 | 0 / 0 |
| 공식 웹 문서 수 | 2 | 2 |

배포 후 추가 관측은 `grounded_non_material=4`, `metadata_only_not_body=1`, `grounded_subject_missing=2`, `grounded_text_not_exact=2`, `grounded_wrong_company=1`, 본문 확보 실패 3건이다. 이 값들을 모두 약칭 문제로 합치지 않는다. `분석AI호출=3`은 분석한 기사 수가 아니다.

`검증된이름변형=[]`는 `news_intake/identity_names.py::derived_company_names`가 만든 한·영 혼합 파생 표기 목록이다. `NewsCompanyContext.aliases` 전체가 빈 배열이라는 증거가 아니다. 별도로 배포 후 `5b_뉴스_검색스냅샷`에 `공식약칭근거`가 없는 사실을 확인했다. 이는 새 공식 약칭 증거가 추가되지 않았음을 뜻하지만, 기업개황 `stock_name`에 이미 같은 이름이 있었는지까지 알려주지는 않는다.

## 실제 공개 원문 재현

2026-09-23 로그인·쿠키 없는 일반 공개 GET으로 [홈](https://wrtn.io/), [회사소개](https://wrtn.io/company/), [공식 뉴스룸](https://wrtn.io/news/)을 확인했다. 웹 도구와 Python 기본 GET은 403이었고 PowerShell `Invoke-WebRequest -UseBasicParsing`은 200이었다. 로그인·세션·DB·비밀 파일은 사용하지 않았다.

| 공개 문서 | 생산 본문 구간 | 약칭 정의를 포함한 구간 | 생산/typed 조각 | 배포판 → 수정판 약칭 |
|---|---:|---:|---:|---|
| `/` | 8 | 0 | 0 / 0 | 0 → 0 |
| `/company/` | 26 | 0 | 4 / 4 | 0 → 0 |
| `/news/` | 17 | 5 | 3 / 3 | 0 → 1 (`뤼튼`) |

실측 보고서의 회사소개 문서 `document_content_sha256`와 이번 공개 HTML에 생산 추출 순서를 적용한 지문은 모두 **`6dda01a5e69e994271a785cf31f69e34fc94e9c2393849b8ad9abe01609702a7`**이다. 보고서에 남은 위치도 `https://wrtn.io/company/ · 목록 11번째 항목`으로 현행 생산기 형식이다. 이 문서에는 약칭 정의가 없어 수정 후에도 약칭을 만들지 않는다.

뉴스룸에서는 생산기가 항목 URL과 `range_index=2`로 내보낸 2026-08-26 투자 유치 보도자료의 정의를 수정판이 채택했다. 정의는 `뤼튼테크놀로지스(이하 뤼튼, 대표 이세영)`이며 SHA-256은 `93e4cb71525659c561dbaae252a4eefd5da5bb5a578afb07beb25ab9df713dc5`이다. 원문을 잘라 새 회사명을 추측하거나 뉴스 기사로 공식 신원을 만든 것이 아니다.

| 공개 GET HTML | SHA-256 |
|---|---|
| 홈 | `191827586251b261ec7e8cbd6c458940aecd4f400ca9dd2edcd5818412f03c84` |
| 회사소개 | `d80c25acd04426ccbbc77e69312cc49f44f6b9a79e5b1609d6bd9e4d9abe34fe` |
| 뉴스룸 | `50f61c70910018961a1cd729148417272fa1b4390c1eaa0855f608f75cf931ce` |

뉴스룸의 생산 본문 지문은 `0826c07e5ec055c091135727dcb78af2587054a871f617179640e4fec824d530`이다. 원시 공개 HTML, 추출 결과 및 [전후 재생 결과](../../app/.local_evaluation_runs/wrtn-live-news-20260923/alias-replay.json)는 이 작업폴더의 `app/.local_evaluation_runs/wrtn-live-news-20260923`에 보존했다. 이 공개 스냅샷들은 로컬 검증 산출물이며 Git에는 추가하지 않는다.

재생에서는 공개 HTML을 실제 `extract_list_items`/`extract_usable_ranges` → `build_fragments` → `to_evidence_mappings` → 공식 위치 검사 → `produce_from_collection_envelopes` → `OfficialEvidenceCollectionResult`로 전달했다. 배포판 함수는 `git show fb6cc61:app/src/features/pipeline/official_news_aliases.py`로 읽은 원본 코드를 사용했다. 법인·도메인 영수증은 시험 대역이며 운영 수집 당시의 영수증을 재구성했다고 주장하지 않는다. 회사소개 본문 지문 일치는 운영 자료와 직접 대조한 결과이고, 뉴스룸이 운영 당시 두 공식 문서 중 하나였는지는 입력만으로 확인되지 않는다.

## 결함과 변경

`homepage/wide_fragments.py`는 일반 조각에 `URL · 목록 N번째 항목`, 뉴스 목록 조각에 실제 `item_url`을 사용하며 `range_index`도 함께 내보낸다. `web/official_evidence_adapter.py`는 이 현행 계약을 이미 검증한다. 그러나 `chapter_evidence/normalize.py`와 `EvidenceFragment`가 인덱스를 버렸고, `pipeline/official_news_aliases.py::_fragment_matches_location`은 `URL#index`만 받아 모든 현행 웹 조각을 거절했다. 기존 약칭 시험은 `#0` 위치를 직접 만들어 이 결합 오류를 발견하지 못했다.

총괄의 범위 확장 승인에 따라 네 구현 파일만 수정했다.

| 파일 | 최소 변경 |
|---|---|
| `shared/report_evidence/models.py` | `EvidenceFragment.range_index` 추가, 구형 기본값 `-1`, bool·문자열·실수·`-2` 이하 거절 |
| `features/chapter_evidence/normalize.py` | 생산기의 인덱스를 typed 조각까지 전달 |
| `shared/report_evidence/runtime_port.py` | 새 인덱스와 항목 URL을 공식 snapshot에 결속. 둘 다 구형 기본값이면 기존 지문 유지 |
| `features/pipeline/official_news_aliases.py` | 현행 위치·명시 인덱스·구간 길이를 대조하고 항목 URL의 원문 문서 origin 일치를 검사. 증거 진단에도 인덱스 보존 |

회사 ID·공식 출처·기존 신원 결속·조각 해시·문서 허용 해시·원문 범위·snapshot 검증은 유지한다. 다른 회사, 다른 origin의 URL, 위치 불일치, 범위 밖 인덱스, 다른 길이 구간, 조작된 원문/메타데이터는 거절한다. 인덱스가 없는 새 위치를 추측하지 않는다. 구형 `URL#index` 및 DART offset은 계속 지원하고 뉴스 제목·날짜·실제 URL은 보존한다.

기존 약칭 파싱 문법, 보조사 `도`, 뉴스 원문 일치 판정, 분석 호출 상한은 수정하지 않았다. 네 구현 파일에는 새 HTTP·DART·AI 호출이 없다. 약칭이 새로 전달되면 기존 검색 정책이 별칭 검색 슬롯을 사용할 수 있으므로 전체 검색 호출 수 불변을 주장하지 않으며 검색·분석 상한은 유지한다.

## 무료 시험

새 [결합 회귀시험](../../app/src/features/pipeline/tests/test_official_news_alias_locations.py)은 실제 생산기가 목록 위치를 만든 뒤 typed 변환과 약칭·뉴스 요청을 잇는다. 뉴스룸 입력은 기존 공개 HTML fixture를 재사용한다. 수정 전 일반 웹과 뉴스 목록 정상 경로 둘 다 약칭 `0`으로 실패했고, 수정 후 통과했다.

`range_index` 형식·범위·위치 불일치, 잘못된 URL, 타회사, 정의 부재, 원문 변조, frozen 객체 변조, snapshot 변경을 검사했다. `-1` 구형 기본값의 기존 지문 `c133914793d502568b6ca16a9d9f04e9fa8466273f9033ee2a45b155c7fbe0c3`도 그대로 유지됨을 확인했다.

실행 Python은 지정된 `C:/Users/jh-wo/.claude/workspace/기업분석2/.venv/Scripts/python.exe`이다. 모든 무료 실행은 `PYTHON_DOTENV_DISABLED=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, 임시 `STORAGE_DB_PATH`와 `OBSERVABILITY_RECORDS_PATH`를 설정했고 기존 `app/conftest.py`를 유지했다. 테스트 작업 디렉터리는 이 작업폴더의 `app`이다.

```text
python -m pytest src/features/pipeline/tests/test_official_news_alias_locations.py src/features/pipeline/tests/test_official_news_aliases.py src/features/pipeline/tests/test_news_intake_wiring.py src/features/pipeline/tests/test_news_research_context.py src/features/pipeline/tests/test_news_call_budget.py src/features/news_intake/tests src/features/chapter_evidence/tests src/shared/report_evidence/tests src/features/homepage/tests/test_news_list_metadata.py src/features/homepage/tests/test_wide_evidence_mapping.py src/web/tests/test_official_evidence_adapter.py -q --tb=short
```

위 관련 회귀 묶음은 **1,107개 통과**했다. 이후 범위 안의 다른 길이 구간 인덱스 거절 시험을 추가한 최종 약칭 회귀 묶음은 **80개 통과**했다. `git diff --check`도 통과했다.

## 남은 원인과 다음 판단에 필요한 자료

`identity_evidence_not_exact=4`는 모델의 법인 근거가 읽은 본문에 정확한 연속 문자열로 없었다는 판정이다. 실제 `entity_evidence`와 그때 읽은 본문이 없으므로 공백·문장 결합·환각·본문 차이 중 무엇인지 확인할 수 없다. 기존 원문 일치를 완화하거나 검증되지 않은 추측 복구를 추가하지 않았다.

`identity_name_missing=1`도 기사 원문과 실제 `company_name/aliases/entity_evidence`가 없어 짧은 이름 때문이었다고 확정할 수 없다. 이번 수정은 공식 정의가 실제 typed 조각까지 도달했을 때의 명백한 차단을 제거한다. 정의 없는 문서만 수집됐다면 약칭은 여전히 0이어야 한다.

공식 수집에는 `truncated_page_cap=2`, sitemap 실패 1건, 채용 경로 실패가 있고 사전검사도 `transient_web_failure`로 부분 보고서 전환했다. 이 수집 한계와 본문/주체/실질성/매체 검증은 별개다. 다음 운영 확인은 총괄이 보유한 정확한 공식 조각·기업개황 이름 입력과 기사별 원응답을 대조하고 새 실행의 `공식약칭근거` 및 최종 기사 채택을 확인해야 한다.

유료 실행, 비용 상한 확대, 배포, push는 하지 않았다. 진행·범위 승인·완료 보고에는 [Orca orchestration 스킬](C:/Users/jh-wo/.agents/skills/orchestration/SKILL.md)의 CLI를 사용했다.
