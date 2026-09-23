# 공식 근거의 법인 약칭을 뉴스에 전달하는 경로 조사

2026-09-22 · 읽기 전용 조사 · 작업 `task_aa392b26a3e5` · 배정 `ctx_8700d02420df`

후속 사용자 지시로 구현까지 진행했다. 위 작업·배정은 이미 종료되었으며, 아래 **후속 구현과 무료 회귀검증** 절이 현재 변경 내용이다.

## 결론

**뤼튼의 법인 약칭 연결은 추측이 아니라 공개 공식 원문으로 입증할 수 있다.** 공식 뉴스룸은 전체 법인명 직후에 `뤼튼테크놀로지스(이하 뤼튼, 대표 이세영)`라고 명시한다. 이 정의를 이미 수집된, 같은 법인으로 결속된 원문 조각에서 확인할 수 있다면 추가 검색·AI 분석 없이 별칭을 추출하는 좁은 구현이 가능하다. 회사명이 길다는 이유로 앞부분을 자르거나 제품명을 회사명으로 바꾸는 구현은 필요하지 않다.

다만 **운영 당시 `stock_name` 값과 `OfficialEvidenceCollectionResult`의 실제 조각·결속 원본은 이번 조사에서 확보하지 않았다.** 그러므로 운영 실행에 짧은 이름이 아예 없었는지, 명시 정의가 조각으로 전달됐는지까지 확정하지 않는다. 저장된 뉴스 집계와 PDF 출처 표만으로 그 값을 복원할 수 없다. 제품 코드는 수정하지 않았고, 구현은 파일 소유권 조정 이후 별도 작업이다.

## 실제 공식 자료

| 자료 | 확인한 내용 | 사용할 수 있는 범위 |
|---|---|---|
| [공식 뉴스룸](https://wrtn.io/news/) | 2026-09-22 일반 공개 GET 200. 법인명 직후의 명시 약칭 정의와 하단 `사업자 등록번호 202-81-67042` 확인 | 공식 수집기가 기존 DART 신원에 결속한 원문 조각이라면 약칭 증거로 사용 가능 |
| [상장 준비 보도자료](https://wrtn.io/news/%eb%a4%bc%ed%8a%bc%ed%85%8c%ed%81%ac%eb%86%80%eb%a1%9c%ec%a7%80%ec%8a%a4-%ec%83%81%ec%9e%a5-%ec%a3%bc%ea%b4%80%ec%82%ac-%ec%84%a0%ec%a0%95-%ec%b0%a9%ec%88%98/) | 2026-09-02 게시. 공개 GET 본문에서도 동일한 법인 약칭 정의와 하단 등록번호 확인 | 목록의 단어가 우연히 붙은 추론이 아니라 상세 보도자료에도 있는 정의 |
| 기존 보존 [뉴스룸 평문](../../app/.local_evaluation_runs/wrtn-news-review-20260922/claude/wrtn-news-list-2026-09-22.txt) | 71·75·79·83·87행에 명시 정의. 111행에 등록번호 | 재수집 없이 검토 가능한 기존 조사 자료. 단, 이 파일 자체는 운영의 typed 결속 객체가 아님 |
| 기존 보존 [DART 감사보고서 주석](../../app/.local_evaluation_runs/wrtn-news-review-20260922/claude/dart-20260414000008-notes.txt) | 접수번호 `20260414000008`, 전체 법인명 뒤 `이하 "회사"`라는 일반 지칭 | 법인명·사업 설명은 유효하지만 **회사**는 뉴스 검색 별칭으로 승격하면 안 됨 |
| [DART 기업개황 공식 필드 정의](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019002) | `stock_name`은 상장 종목명 또는 기타법인의 약식명칭. `corp_name`, `corp_name_eng`, `jurir_no`, `bizr_no`, `hm_url`도 같은 응답에 있음 | 우선 이미 받은 기업개황의 약식명칭을 그대로 활용. 새 company.json 요청 불필요 |

뉴스룸에서 확인한 위 괄호 정의 문자열의 UTF-8 SHA-256은 `93e4cb71525659c561dbaae252a4eefd5da5bb5a578afb07beb25ab9df713dc5`다. 이는 이번 공개 GET에서 확인한 짧은 정의 문자열의 해시이며, 운영 수집 문서의 `content_sha256` 또는 신원 영수증을 대신하지 않는다. 웹 검색 도구의 공식 페이지 열기는 실패했지만 별도의 일반 공개 GET은 성공했으며 로그인·쿠키·접근제한 우회는 하지 않았다.

뉴스룸의 회사·서비스 메뉴에는 뤼튼 외에 크랙·캬라푸·뤼튼 AX 등도 등장한다. **메뉴 노출·제품 운영·서비스 제공 관계만으로는 회사의 동일 이름이 되지 않는다.** 위 명시 정의가 다른 이유다.

## 현재 코드에서 정보가 분리되는 지점

1. [real.py](../../app/src/features/pipeline/real.py)의 `_official_company_aliases`(3128행)는 `corp_name_eng`, `corp_eng_name`, `stock_name`만 읽고 빈 값 제거·중복 제거 후 반환한다. `stock_name`을 누락하는 코드 결함은 없다. 해당 값이 실제로 `뤼튼`이면 이미 전달되는 구조다. 비상장사라 이 필드가 없을 것이라는 가정도 틀리다.
2. 같은 파일 3144행의 `_official_company_registration_numbers`는 이미 받은 `bizr_no`·`jurir_no`를 정규화한다. 운영 공식 수집 요청은 이 번호와 법인 ID·법인명·홈페이지·도메인 입증 자료를 함께 운반한다.
3. [news_research_context.py](../../app/src/features/pipeline/news_research_context.py)의 `official_news_context`(20행)는 결속된 문서의 조각을 읽지만 반환값은 **공식 도메인과 사업 문맥 문자열**뿐이다. `identity`, `business_model`, `portfolio`, `operations_partners` 네 장에서 조각당 600자·전체 4,000자로 잘라 문맥을 만든다. 괄호 약칭 정의가 있어도 `aliases`로 승격하는 경로가 없다. 정의가 다른 장에 있거나 절단 위치 뒤에 있으면 문맥에서도 빠질 수 있다.
4. `real.py::_run_news_search_branch`(6133행)는 위 도메인·문맥과 `_official_company_aliases(profile)`를 별개로 `prepare_news_research`에 넘긴다. [core 어댑터](../../app/src/core/news_research_adapter.py)는 받은 별칭을 `NewsCompanyContext`로 그대로 전달한다.
5. [identity_names.py](../../app/src/features/news_intake/identity_names.py)는 공식 입력 이름과 검증된 한·영 전체 표기를 사용한다. 사업 문맥의 단어나 법인명의 일부를 새 별칭으로 만들지 않는다. 이는 유지해야 하는 경계다.

따라서 확인한 결함 후보는 **공식 본문에만 있는 명시 약칭이 뉴스 이름 목록으로 전달되지 않는 것**이다. `stock_name` 손실이나 이번 운영 5건 전체의 원인으로 바꾸어 설명하지 않는다. 앞서 수락된 보조사 `도` 수정은 별도이며 그대로 보존한다.

## 이미 있는 결속 필드와 사용할 경계

[OfficialEvidenceCollectionRequest/Result](../../app/src/shared/report_evidence/runtime_port.py)와 [문서·조각 자료형](../../app/src/shared/report_evidence/models.py)에 필요한 필드가 이미 있다.

| 단위 | 재사용할 필드 | 보존할 의미 |
|---|---|---|
| 요청·결과 | `company_id`, `company_name`, `company_aliases`, `company_registration_numbers`, `source_snapshot_sha256` | 같은 법인의 같은 공식 수집 결과인지 확인 |
| 문서 | `document_id`, `canonical_url`, `source_kind`, `content_sha256`, `identity_binding`, `exact_evidence_hashes`, `usable_ranges`, `domain_attestation_source_id`, `domain_attestation_evidence` | 법인·도메인·원문·범위의 기존 검증 계보 |
| 조각 | `company_id`, `document_id`, `fragment_id`, `location`, `text`, `text_sha256` | 정의가 어느 원문의 어느 조각에 실제로 있었는지 확인 |

[공식 웹 변환기](../../app/src/features/homepage/wide_evidence_mapping.py)는 본문 구간을 `URL#index`와 원문 해시로 내보낸다. [공식 수집 어댑터](../../app/src/web/official_evidence_adapter.py)의 `_classified_evidence_location_bindings`는 문서 ID·해시·위치·usable range를 대조한다. `OfficialEvidenceCollectionResult`는 법인 일치와 formal source 정책을 검증하고, source snapshot에는 문서·조각 해시·`identity_binding`·도메인 입증·위치가 들어간다.

별칭 추출에 `identity_context` 문자열이나 `identity_binding`의 단순 비어있지 않음만 사용하면 안 된다. **기존 계약을 통과한 typed 문서·조각에서 절단 전 원문을 읽어야 한다.** `provenance_documents`는 원문 없는 감사용이며, 무분류 관측값 역시 개수·해시만 넘기므로 이 경로에서 별칭을 복원할 수 없다.

등록번호가 없는 DART 정확한 홈페이지 host에 대해서는 [현재 신원 규칙](../../app/src/features/homepage/official_identity.py)이 이름만 확인하는 별도 결속을 허용하고, 교차 도메인은 법인명과 등록번호를 요구한다. 이번 제안은 이 기존 차이를 유지한다. 이름만 확인한 영수증을 등록번호 이중 확인으로 표시하거나 다른 도메인까지 확장해서는 안 된다. 이번 공개 페이지 번호가 운영 기업개황 번호와 같았다는 사실까지 직접 대조한 것은 아니다.

## 결정적으로 파싱할 수 있는 최소 조건

다음 조건을 모두 만족할 때만 뉴스 전용 별칭을 추가하는 방안을 권한다.

1. 먼저 이미 받은 기업개황의 `stock_name`과 공식 영문명 목록을 그대로 유지한다. `stock_name=뤼튼`이면 이 목적의 추가 파싱은 불필요하다. 응답의 성공·현재 법인 일치 검사를 건너뛰거나 별도 회사의 응답을 결합하지 않는다.
2. 공식 수집 **완료 후**, 현재 요청 법인 ID와 결과·문서·조각 ID가 일치하고 기존 출처·도메인·원문 계약을 통과한 조각만 읽는다. 뉴스 기사·검색 제목·사용자 입력·프롬프트의 설명으로 공식 이름을 만들지 않는다.
3. 하나의 연속 조각에서 `정확한 전체 법인명 + (이하 + 단일 고유 약칭 + 닫는 괄호)`가 직접 붙은 정의만 인식한다. 이번 실제 원문에 있는 `, 대표 이름` 꼬리와 약칭의 짝이 맞는 인용부호는 닫힌 문법으로 처리할 수 있다. 그 밖의 임의 서술·여러 별칭 나열·괄호 밖 문장 결합은 보류한다.
4. 정의 왼쪽 전체 이름은 기존 `shared.company_identity.exact_company_names_equivalent`로 현재 공식 법인명과 비교한다. 법인 표지·표기 정규화 외의 접두 절단·유사도·substring은 금지한다. `회사`, `당사`, `그룹`, `서비스`, `제품` 등 일반 지칭은 별칭으로 쓰지 않는다. 문법·길이 상한·금지 지칭은 feature 상수로 둔다.
5. 약칭과 함께 법인 ID, 문서 ID·URL, 조각 ID·위치·해시, 정의의 조각 내 시작/끝과 정확 문자열 해시, 기존 `identity_binding`, 공식 snapshot을 증거로 남긴다. 정의가 여러 조각에 걸치거나 관련 키가 누락·상충하면 추가하지 않는다. 웹 원문을 새로 읽어 수집 당시 해시를 대체하지 않는다.
6. 기존 DART 별칭과 검증된 새 별칭을 결정적인 순서로 중복 제거하여 **뉴스 요청 한 곳**에 전달한다. 공식 웹 검증을 위한 초기 별칭 입력으로 되먹여 신원을 순환 증명하지 않는다. 새 별칭이 생겨도 기사별 동일 법인·사업 문맥·실질성·주체·정확 인용 검증은 그대로다.

이 조건은 현재 공개 뤼튼 정의에 적용 가능하다. 다만 동일한 정의가 실제 `official_evidence.candidates[*].fragments[*].text`에 보존됐는지는 다음 구현 작업의 첫 확인 사항이다. 정의가 없는 상태에서 문자열만 만들어 넣는 구현은 **불가**하다. 기존 공시의 `이하 회사`만 확보된 경우에도 추가 별칭은 0개여야 한다.

## 최소 변경 범위 제안

| 파일 | 필요한 변경 |
|---|---|
| `pipeline/news_research_context.py` | 현재 문맥 함수와 별도로, typed 공식 후보에서 명시 약칭과 증거를 반환하는 순수 함수 추가. 네 장·600자 절단 후 문맥 대신 기존 후보 전체의 원문 조각을 사용 |
| `pipeline/real.py`의 `_run_news_search_branch` | 공식 수집 이후 뉴스 요청에서만 기존 DART 별칭과 새 검증 약칭을 결합하고 증거 요약을 진단에 보존 |
| `pipeline` 안의 새 별칭 전용 상수 파일 | 닫힌 정의 문법·상한·일반 지칭 목록 |
| `pipeline/tests` 안의 새 회귀 파일 | 실제 정의의 파싱, typed 결속, 거절 반례, 요청 전달·지문·호출 예산 확인 |

`_official_company_aliases(profile)`의 기존 profile 전용 계약은 유지하는 편이 낫다. 이 함수는 공식 웹 수집과 다른 분석 경계에서도 사용하므로 이를 전역 확장하면 뉴스 이외의 회사 신원 판정까지 바뀐다. 위 최소안에서는 `news_intake`가 pipeline 또는 homepage feature를 직접 import하지 않으며, core의 기존 문자열 별칭 입력으로 전달할 수 있다. 뉴스 DTO까지 증거 객체를 운반하기로 결정하면 core·shared 계약 변경이 추가되므로 별도 소유권 조정이 필요하다.

공식 `source_snapshot_sha256`는 이미 원문·결속 변경을 반영하고 최종 생성 digest에 합쳐진다. 뉴스 `company_digest`도 `asdict(company)`를 해시하므로 별칭이 바뀌면 다른 snapshot이 된다. 별칭 증거의 문서·위치가 달라졌을 때 공식 snapshot과 결과 캐시가 함께 바뀌는 검사를 추가해야 한다.

**PDF팀 fix-sources 및 pipeline 담당자와 위 두 기존 파일의 소유권을 먼저 조정해야 한다. 이 조사에서는 구현하지 않았다.**

## 비용과 반례

약칭 추출 자체는 이미 메모리에 있는 필드·조각을 읽는 코드라 추가 웹 조회·DART 조회·유료 모델 호출이 필요 없다. 다만 `aliases`에 새 이름을 넣으면 현재 `search_plan`이 별칭 검색 최대 2개를 만들기 때문에, 비어 있던 별칭 칸이 채워질 때 실제 뉴스 검색 1회가 늘 수 있다. 전체 검색 상한 12회는 그대로지만 **호출수가 항상 같다고 주장할 수 없다.** 엄격한 호출수 불변이 필요하면 기존 검색 슬롯 대체 또는 검증 전용 이름과 검색 이름의 분리까지 범위를 별도로 정해야 한다. 분석 호출 상한 증가는 필요하지 않다.

| 입력·상황 | 기대 처리 |
|---|---|
| 같은 법인으로 결속된 공식 원문에 실제 괄호 정의 존재 | 약칭과 증거를 추가하되 기사 검증은 계속 수행 |
| DART `stock_name`이 이미 `뤼튼` | 기존 값 재사용, 중복 별칭·회사 조회 추가 없음 |
| `뤼튼테크놀로지스(이하 회사)` | 일반 지칭 거절 |
| `뤼튼테크놀로지스가 운영하는 서비스 뤼튼`, `크랙(Crack)` | 제품·서비스 관계이므로 법인 별칭으로 승격 금지 |
| `뤼튼테크놀로지스재팬(이하 뤼튼)` | 전체 법인명 불일치로 거절. 접두 일치로 부모 법인에 연결 금지 |
| 다른 법인 ID의 똑같은 이름·문장 | 대상 법인 결속 불일치로 거절 |
| 회사 이름만 복사한 다른 host, 등록번호 불일치 | 기존 공식 웹 검증에서 차단. 약칭 파싱으로 구제 금지 |
| 본문에 없는 모델 생성 정의, 정의가 잘린 조각, 다른 두 조각을 이어 붙인 정의 | 원문 결속 부족으로 추가하지 않음 |
| 공식 약칭이 있어도 동명이인 기사·자회사 기사이거나 인용문이 본문에 없음 | 기존 뉴스 동일 법인·주체·원문 검증에서 계속 차단 |

## 수행 범위와 남은 확인

소스·기존 평문 자료 읽기, 공개 웹 검색·GET, 보고서 작성만 수행했다. 테스트·제품 모듈 import·유료 API·서버·DB·비밀 파일 접근은 하지 않았다. 편집은 이 문서 하나뿐이며 이전 뉴스 조사 수정과 다른 작업자의 변경을 보존했다.

남은 확인은 운영 기업개황의 실제 `stock_name`과 기존 typed 공식 조각에 명시 정의가 있는지다. 이 확인 없이 운영 누락의 직접 원인이 해결됐다고 말할 수 없다. 개별 원응답이 없는 기존 신원 실패 5건을 전부 약칭 문제로 설명하지 않으며, 읽은 기사 15개를 모두 분석한 것으로 해석하지 않는다.

조정자와 진행·완료 보고에는 Orca orchestration 스킬(`%USERPROFILE%/.agents/skills/orchestration/SKILL.md`)을 사용했다.

## 후속 구현과 무료 회귀검증

사용자의 `이어서해` 지시에 따라 구현했다. 조정자가 PDF 작업자의 별도 워크트리 이동과 파일 경계를 확인하고, 이 워크트리의 `real.py::_run_news_search_branch` import·뉴스 별칭·진단 연결을 승인했다. 이전에 종료된 배정의 lifecycle은 재사용하지 않았다.

### 변경한 파일과 동작

| 파일 | 변경 |
|---|---|
| `app/src/features/pipeline/official_news_alias_constants.py` | 명시 정의 문법, 일반 지칭·부정·제품·다른 법인 제외 조건, 신규 약칭 최대 1개 상한 |
| `app/src/features/pipeline/official_news_aliases.py` | 기존 typed 공식 수집 결과의 법인·출처·원문 결속을 확인하고 약칭과 근거 반환 |
| `app/src/features/pipeline/real.py` | 뉴스 검색 갈래에서만 기존 DART 별칭 뒤에 새 약칭을 연결하고 `공식약칭근거` 진단 기록 |
| `app/src/features/pipeline/tests/test_official_news_aliases.py` | 실제 공개 정의, typed 계약, 거절 반례, 뉴스 전달·기사 검증·호출 수·캐시 회귀 |
| 이 문서 | 구현 결과와 남은 한계 기록 |

`news_research_context.py`, `_official_company_aliases`, 홈페이지 수집·신원 판별, 뉴스 분석·인용 판정·호출 예산은 수정하지 않았다. 새 helper는 다른 feature를 import하지 않고 pipeline 내부와 shared 계약만 사용한다. 조정자가 병행 커밋한 이전 뉴스 수정과 검수 수정도 보존했다.

실제 공개 문장은 `AI(인공지능) 서비스 플랫폼 기업 ‘뤼튼테크놀로지스(이하 뤼튼, 대표 이세영)’가 상장 준비에 본격 착수했다.`이다. 시험에서는 이 문자열을 그대로 typed 조각에 넣어 `뤼튼` 1개와 위의 정의 SHA-256이 반환되는 것을 확인했다. 따라서 앞 수식어가 존재하더라도 실제 원문의 인용부호 경계를 사용해 정확한 전체 법인명을 비교한다. 문자 600개 이후·기존 뉴스 문맥에 포함되지 않는 장에 있는 정의도 읽는다. 회사 접두어를 잘라 만들거나 서로 다른 조각을 붙이지 않는다.

### 유지한 신원·원문 계약

- 현재 profile의 법인 ID와 `OfficialEvidenceCollectionResult.company_id`가 일치해야 한다. 후보·문서·조각 자료형과 기존 생성 검증을 다시 확인하며 회사 ID, 문서 ID, 조각 해시 허용 목록, 실제 텍스트 해시, formal 출처 정책 및 공식 snapshot 불일치는 빈 tuple로 처리한다.
- 공식 웹은 기존 shared 공개 출처 판정으로 HTTPS URL과 도메인 입증을 검사한다. 기업개황 입증은 현재 법인 ID·법인명·홈페이지에 맞아야 한다. 교차 도메인 DART 공시 영수증은 기존 검증기에 더해 현재 법인 ID와 정규화한 사업자·법인등록번호의 해시도 일치해야 한다. 번호가 없거나 다른 경우 허용하지 않는다.
- DART 공시 경로는 문서 ID·접수 URL과 `corp_code`, `rcept_no`, `source_kind`, `identity_check=verified_match`가 정확히 맞아야 한다. 기존 수집기가 메타 부재를 정직하게 기록한 `unverifiable_no_fetcher_metadata`는 새 약칭으로 승격하지 않는다. 일반 문서 수집이나 기존 기사 판정을 변경한 것은 아니다.
- 웹의 `URL#index`와 DART의 `start-end`를 문서 `usable_ranges` 및 조각 길이에 대조한다. 문서 전체 원문을 새로 읽거나 `content_sha256`을 조각 해시로 바꾸지 않는다. DTO가 보유하지 않은 전체 문서의 해시를 새로 검산했다고 주장하지 않는다.
- 정확한 전체 법인명은 shared `exact_company_names_equivalent`로 비교한다. `회사`, `당사`, `그룹`, `서비스` 등의 일반 지칭, 제품·브랜드 관계, 다른 법인·자회사, 부정·가정·정정 문맥, 짝이 틀린 인용부호, 잘린 정의는 거절한다. 서로 다른 약칭 정의가 둘 이상 나오면 임의 선택하지 않는다.
- 진단에는 새 약칭을 추가한 경우에만 법인 ID, 문서 ID·URL·해시, 조각 ID·위치·해시, 조각 내 정의 시작/끝·정확 문구·해시, 기존 `identity_binding`, 공식 snapshot을 기록한다. 근거가 없거나 기존 DART 별칭과 중복이면 기존 aliases와 진단 기본 형태를 유지한다.

### 무료 재현 결과와 비용

시험용 법인 ID와 typed 영수증은 **대역**이다. 공개 정의의 실제성은 앞 절의 공개 원문으로 확인했지만, 이 fixture가 운영 당시 저장 객체라고 주장하지 않는다. 기사·모델 응답도 유료 호출 없이 경계 검증을 위한 대역을 사용했다.

수정 전 동작을 재현하여 공식 정의 추가를 꺼 두면 `뤼튼은 생성형 AI 플랫폼을 운영하며 기업용 AI 서비스를 새롭게 출시했다.`라는 시험 기사가 `identity_name_missing`으로 거절된다. 같은 공식 근거에서 약칭을 연결하면 정확 원문 인용 1개를 얻는다. 수정 전후 각각 기사 본문 읽기 1회·분석 대역 1회이며, 추가 약칭 추출 자체의 HTTP·DART·모델 호출은 0회다. alias가 있어도 다음은 계속 거절한다.

| 반례 | 결과 |
|---|---|
| `뤼튼`이라는 이름으로 대학 입시 상담을 하는 무관한 사업 기사 | 사업 문맥 불일치 |
| `뤼튼재팬`만 주체로 등장하는 기사 | 법인명 경계 불일치 |
| 기사 본문에 없는 `폐기`를 인용으로 생성 | 정확 원문 인용 불일치 |
| 모델이 `same_company=False`로 답한 기사 | 기존 동일 법인 거절 유지 |
| 다른 법인 ID·변조 해시·잘못된 원문 위치·다른 등록번호 | 약칭 추가 0개 |

뉴스 표시 회사명은 전체 법인명을 유지한다. 기존 영문명·`stock_name` 순서를 유지하며 `stock_name=뤼튼`이면 추가 별칭과 해당 신규 진단이 없다. 검색어의 빈 별칭 자리에 새 이름이 들어갈 때만 기존 검색 계획에 **최대 1회**를 추가할 수 있다. 기존 별칭 검색 최대 2개·총 검색 최대 12회는 그대로이며, 분석 상한 시험값 3회와 작성 예약량도 동일하다. 검색 API 비용까지 항상 같다고 주장하지 않는다. 공식 원문 재수집이나 추가 AI 검증은 도입하지 않았다.

약칭 변화는 기존 뉴스 `company_digest`를 바꾼다. 같은 약칭이어도 문서 전체 해시나 usable range가 바뀌면 공식 snapshot과 최종 생성 digest가 달라진다. 공식 신원 수집의 초기 aliases에는 새 이름을 넣지 않으므로 별칭으로 스스로 공식성을 입증하는 순환 경로가 없다.

### 검증 실행

루트 `.venv/Scripts/python.exe -X utf8`, `app/conftest.py` 유지, pytest plugin 자동로드 비활성, `-p no:cacheprovider`로 실행했다. `PYTHON_DOTENV_DISABLED=1`을 설정하고, import 전에 `app/.local_evaluation_runs/official-alias-fix/isolated`를 만든 뒤 `STORAGE_DB_PATH`를 그 아래 `storage.db`로 분리했다. pytest 임시 파일도 같은 작업 전용 하위 경로를 사용했다. 실제 저장 DB·비밀 파일·유료 API·서버는 사용하지 않았다.

선별 범위는 신규 공식 약칭 시험, pipeline의 뉴스 문맥·호출 예산·뉴스 배선·검색 전송 진단·병렬 갈래, 직전 뉴스 조사 `도` 회귀시험이다. 최종 실행은 **142개 통과, 7.02초**였다. 이 중 신규 공식 약칭 회귀는 58개다. `git diff --check`도 통과했다.

위 환경을 설정한 뒤 `app`에서 실행한 최종 명령은 다음과 같다.

```powershell
& ../.venv/Scripts/python.exe -X utf8 -m pytest `
  src/features/pipeline/tests/test_official_news_aliases.py `
  src/features/pipeline/tests/test_news_research_context.py `
  src/features/pipeline/tests/test_news_call_budget.py `
  src/features/pipeline/tests/test_news_intake_wiring.py `
  src/features/pipeline/tests/test_news_transport_diagnostics.py `
  src/features/pipeline/tests/test_parallel_collect_branches.py `
  src/features/news_intake/tests/test_name_particle_grounding.py `
  -p no:cacheprovider --basetemp .local_evaluation_runs/official-alias-fix/pytest-final -q
```

### 남는 한계

실제 운영 `stock_name`과 typed 공식 조각 원본은 여전히 확보하지 않았다. 다음 실행이 이미 수집한 공식 조각에 이 정의를 포함해야 개선이 적용되며, 정의가 없으면 새 약칭은 0개다. 문법은 이번에 입증한 괄호 정의와 단일 고유 약칭·선택적 대표 꼬리로 좁혔다. 인용부호 없이 법인명 앞에 수식어가 붙은 문장, 여러 단어 약칭, 영문 약칭 정의 등은 확장하지 않았다. 부정·제품·관계회사 표현이 같은 문장에 있으면 정상 정의도 보수적으로 제외할 수 있다.

회사의 명시적 약칭도 모든 기사에 대한 동일 법인 보증은 아니다. 기존 기사별 주체·사업 문맥·정확 인용·모델 판정을 계속 사용한다. 운영 읽기 15개를 모두 분석했다고 해석하지 않으며, 저장되지 않은 원응답의 신원 실패 5개가 모두 잘못되었다고 결론내리지 않는다. 운영 최종사용 0건이 해결되었다거나 보고서 시간·총비용이 실제로 줄었다는 실측 주장도 하지 않는다.
