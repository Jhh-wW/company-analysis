# 뉴스 본문 병렬 수집의 운영 연결 검토

2026-09-22 · 운영 연결 담당 Astra · 외부 통신·유료 API·배포·커밋·푸시 없이 검증

운영 기본 `_fetch_news_article_text`가 `NewsResearchSession.collect`를 거쳐 실제 수집기의 `BodyFetchConcurrency`에 연결된다. 기본 동시 요청은 2개이며 같은 호스트는 1개씩 읽는다. 환경변수 `NEWS_BODY_FETCH_CONCURRENCY=1`로 순차 실행에 복귀할 수 있고, 안전성을 확인하지 않은 주입 콜백은 항상 순차로 유지한다.

이번 담당자가 변경한 파일은 다음과 같다. 기존 공급자 병렬화 변경이 함께 있는 `real.py`에서는 뉴스 본문 함수와 수집 연결 부분만 추가로 수정했다.

| 파일 | 변경 |
|---|---|
| `app/src/features/pipeline/real.py` | 기본 콜백 판별, 환경변수 해석, 작업 문맥 격리, 취소·마감 확인, 수집기 옵션 전달 |
| `app/src/features/pipeline/news_parallel_constants.py` | 환경변수 이름, 기본 2·순차 1·절대 상한 3 |
| `app/src/features/pipeline/tests/test_news_parallel_runtime.py` | 실제 세션과 운영 본문 경로의 가짜 통합시험 24개 |
| `app/src/core/news_research_adapter.py` | `body_fetch` 전달, 수집 옵션 생성, HTTP 격리 문맥 연결 |
| `app/src/features/homepage/safe_http.py` | 독립 요청 사전과 부모 절대 마감 보존, 완료 판정의 잠금 보호 재사용 |
| `app/src/features/homepage/tests/test_isolated_request_scope.py` | 독립 사전·절대 마감·예외 복원·완료 판정 재사용 시험 5개 |
| `docs/reviews/2026-09-22-news-parallel-runtime.md` | 이 검토 기록 |

운영 설정은 다음과 같다. 본문 병렬 옵션은 기존 뉴스 기능 스위치를 대체하지 않으며, 뉴스 수집이 실행되는 경로에 적용된다.

| 콜백과 설정 | 실제 실행 |
|---|---|
| 운영 기본 콜백, 환경변수 없음 | 동시 2개, 호스트당 1개 |
| 운영 기본 콜백, 값 `1` | 호출 스레드에서 순차 실행 |
| 운영 기본 콜백, 값 `2` 또는 `3` | 지정된 폭, 호스트당 1개 |
| 빈 값·잘못된 값·범위 밖 값 | 순차 실행 |
| 주입된 다른 콜백 | 설정과 무관하게 순차 실행 |

기본 함수 객체를 보관하고 동일성으로 판별한다. 이름을 같게 붙이거나 전역 함수를 시험 대역으로 바꿔도 병렬 안전성을 자동 승인하지 않는다. `pipeline`에는 다른 기능을 직접 가져오는 새 import를 추가하지 않았고, 새 기능 경계 연결은 `core/news_research_adapter.py`를 통한다. 뉴스 정책·스냅샷 지문·분석 상한·모델 선택은 바꾸지 않았다.

`copy_context()`는 문맥 값의 참조를 복사하므로 그 자체로 DNS·robots 사전이나 진단 목록을 격리하지 않는다. 각 본문 콜백은 별도 DNS·robots 사전과 `run_diagnostics.use_steps` 목록을 사용한다. 완료된 캐시 판정은 수집 하나가 소유하는 `IsolatedRequestCache`에 잠금 아래 복사하고 다음 작업이 다시 독립 사전으로 복사한다. 작업 중 사전을 서로 공유하거나 부모 사전을 갱신하지 않는다. 같은 호스트의 콜백은 바깥 수집기 슬롯에서 직렬화되므로, 첫 콜백이 반환하기 전에 저장한 판정을 다음 콜백이 재사용한다. 같은 origin의 세 기사에서 robots 요청이 정확히 한 번임을 순차·병렬 양쪽에서 검증했다.

DNS 응답 튜플과 완성된 robots 판정 값은 읽기 전용으로 재사용한다. robots 파서는 로더가 판정을 저장하기 전에 완성하며, 이후 운영 경로에서는 `can_fetch`로 읽기만 한다. 수집기 합류 후 부모 스레드가 본문 진단을 URL 순서로 병합하고, 각 요청 안의 진단 순서는 보존한다. 성공·취소·예외에서 부모 ContextVar와 예산 객체가 복원된다.

HTTP 작업 예산은 부모가 쓰던 시계와 `expires_at`을 독립 객체로 그대로 이어받는다. 부모가 없으면 전송 계층의 기존 요청별 10초를 유지하며 robots·기사·URL 변형 전체를 묶은 새 10초 제한을 만들지 않는다. 총괄 최종 검토에서 초안의 묶음 제한이 정상 본문을 조기에 포기하게 할 수 있음을 발견해 수정했다. robots 8초와 본문 8초를 각각 소비하는 가짜 시계 시험이 순차·병렬 양쪽에서 통과했다. 이미 만료된 부모 마감은 복사 시점부터 실패하며, 캐시가 있어도 만료된 예산으로 새 전송을 시작하지 않는다.

취소 확인에는 총괄이 추가한 `generation_coordination.check_active()`를 사용한다. 기존 `ensure_paid_phase()`는 비용 예약과 ContextVar 스택을 열 수 있어 본문 스레드에서 부르지 않는다. 읽기 전용 확인은 본문 진입·각 URL 변형·실제 robots/기사 전송 직전·본문 반환·분석 직전·세션 반환 뒤에 적용한다. 총괄이 `body_prefetch`와 `collection`의 포괄적 예외 처리 앞에 요청 취소·전체 실행 마감·빌드 신원 변경 예외의 재전파를 추가했으므로 요청 중단이 일반 본문 실패로 바뀌지 않는다. 진행 중이던 요청은 기존 제한 내에서 합류하고, 호스트 자리를 기다리던 요청은 슬롯을 얻은 뒤 취소·마감을 확인해 새 전송을 막는다.

현재 URL 변형은 HTTPS 전환과 `www` 추가·제거뿐이다. 수집기 `HostSlots`는 `domain_host`가 만든 키를 사용하고 이 함수는 대소문자·끝 점·선행 `www`를 정규화한다. 따라서 운영 콜백 내부 변형과 robots 요청은 이미 같은 바깥 슬롯 범위 안에 있다. 불필요한 중첩 호스트 잠금은 추가하지 않았다. 변형의 호스트 키 동치와 `www` 유무가 다른 두 후보의 직렬화·대기 중 취소를 시험으로 고정했다. 앞으로 다른 호스트로 이동하는 URL 변형을 추가하면 이 계약도 다시 검토해야 한다.

robots origin별 판정·동일 origin 리다이렉트 제한·SSRF 검증·공인 IP 고정·응답 크기 상한을 완화하지 않았다. 각 origin의 robots 규칙은 그대로 확인하며 판정 재사용 키도 scheme·host·port를 유지한다.

검증은 프로젝트 `.venv/Scripts/python.exe`로 수행했다.

```powershell
$env:PYTHONPATH='app'
$env:PYTHONUTF8='1'
& '.venv/Scripts/python.exe' -m pytest app/src/features/pipeline/tests/test_news_parallel_runtime.py app/src/features/homepage/tests/test_isolated_request_scope.py -q -p no:cacheprovider
& '.venv/Scripts/python.exe' -m pytest app/src/features/pipeline/tests app/src/features/homepage/tests app/src/features/news_intake/tests app/src/core/tests -q -p no:cacheprovider
```

- 신규 최종 시험: **29개 통과**. 실제 `prepare_news_research → NewsResearchSession.collect → collect_from_snapshot → 운영 본문 콜백`을 통과하고, 전송만 가짜로 대체한다.
- 총괄 시간 계약 보강 뒤: 위 신규 시험에 요청별 제한·기존 부모 마감 보존 4개를 더했고, 기존 robots 캐시 시험과 함께 **42개 통과**했다. `robots_cache.py`는 기존 예산 캐시에 더해 명시적 격리 문맥의 캐시도 사용할 수 있게 연결했다. 일반 단독 호출은 캐시를 새로 만들지 않는다.
- 관련 전체 회귀: **2,657개 통과, 1개 건너뜀, 하위 시험 10개 통과**, 95.64초. 전체 수집 이후 추가한 순차·병렬 완전동치 시험 1개는 위 최종 29개 실행에서 별도로 통과했다.
- 총괄의 시간 계약 보강 후 같은 전체 범위 재검증: **2,662개 통과, 1개 건너뜀, 하위 시험 10개 통과**, 83.62초. 위 2,657개와 겹치는 재실행이므로 합산하지 않는다.
- 기존 기사 본문·SSRF 표적 시험: **84개 통과**.
- `git diff --check`: 변경한 기존 소스 파일에서 공백 오류 없음.

동시 2·3개의 실제 진입은 장벽으로 관측했다. 순차 복귀와 주입 콜백은 호출 스레드 ID로 확인했다. 부모 캐시·절대 마감·취소 Event 참조·진단 목록 보존, 작업별 사전 분리, 같은 origin의 robots 한 번, robots 조회 중 취소, 호스트 대기 중 취소, 부모 마감 전후 전송 차단을 검증했다. 순차와 병렬의 근거 조각·분석 입력·분석 호출 1회·동시 수집 정산 외 진단이 동일했다. 시험의 유료 단계 예약 훅은 호출되면 즉시 실패하도록 설치했다.

실제 언론사 응답 속도나 운영 지연은 측정하지 않았다. 캐시 격리·재사용과 병렬 진입은 입증했지만 운영 속도 개선 수치는 아직 없다. 이미 시작한 HTTP 요청은 즉시 강제 취소하지 않고 기존 제한 내에서 마친다. 선행 본문 정산과 후보 선택 관련 한계는 [본문 수집 구현 기록](2026-09-22-news-body-prefetch-implementation.md)을 따른다. 남은 구현 작업은 없으며, 배포와 실제 통신 검증은 수행하지 않았다.
