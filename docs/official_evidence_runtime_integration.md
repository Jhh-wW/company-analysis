# 공식 근거 수집 계약의 실서비스 결합 지도

## 현재 상태

새 공식 근거 경로는 구현과 오프라인 결합시험까지 존재하지만, 2026-08-31
현재 실서비스 `RealPipeline`에는 연결되어 있지 않다. 아래 함수들은 정의와
시험 밖의 생산 호출이 0건이다.

- `analysis_engine/src/features/evidence_collection.collect.collect_dart_evidence`
- `app/src/features/homepage/wide_collect.collect_official_web_documents`
- `app/src/features/homepage/wide_fragments.build_fragments_for_collection`
- `app/src/features/homepage/wide_evidence_mapping.to_evidence_mappings`
- `app/src/features/chapter_evidence/produce.produce_from_collection_envelopes`

`app/src/features/chapter_evidence/tests/test_combined_collectors_end_to_end.py`는
이 함수들을 가짜 전송으로 이어 보는 계약시험이다. 웹 요청, single-flight,
유료 phase, 실제 composer를 거치는 서비스 종단시험은 아니다.

## 실제 서비스 호출 그래프

현재 실서비스의 조사 경로는 다음과 같다.

```text
web 요청
  -> web.job_runtime._run_pipeline_worker
     -> generation_coordination.activate(GenerationSession.callbacks)
        -> features.pipeline.real.RealPipeline.run
           -> RealPipeline._run_metered
              -> DART company.json / list.json / 재무 / latest report 조회
              -> ReportSourceIdentity.capture
              -> generation_coordination.coordinate
                 -> cache hit이면 즉시 반환
                 -> miss의 single-flight owner만 이후 진행
              -> features.pipeline.real._collect
                 -> 기존 DART 원문 download/read/make_fragments
                 -> homepage.logic.collect_homepage_fragments
                 -> homepage.ir_pdf.collect_official_ir_fragments
                 -> revenuemix.build
              -> v2: generation_coordination.ensure_paid_phase
                     -> _run_v2_composer
              -> v1: generation_coordination.ensure_paid_phase
                     -> span 선택 / writer / 검증
```

새 수집기를 호출할 수 있는 돈·잠금 안전 경계는
`generation_coordination.coordinate`가 cache miss owner를 확정한 뒤이고,
첫 `generation_coordination.ensure_paid_phase`보다 앞이다. 이보다 앞에서
호출하면 waiter도 같은 무료 네트워크 수집을 중복 실행한다. 이보다 뒤에서
호출하면 무료 preflight가 유료 phase 안으로 들어간다.

## 단순 호출 추가로 끝나지 않는 이유

현재 `_collect`의 출력은 legacy `dict[int, dict[str, str]]`인 `frags`다. 새
경로의 출력은 `ChapterEvidenceCandidates`와 `SectionEvidenceBundle`이며,
현재 v1/v2 composer는 이를 읽지 않는다. 새 함수를 `_collect`에서 호출만 해도
보고서 입력과 출고 판정은 그대로여서 운영 품질은 변하지 않는다.

또한 `past_changes:historical_performance`와 `competitive_position`의 다섯 칸은
`InjectedSlotFacts`로만 채울 수 있다. 3개년 실적의 최종 `FactRecord` ID는
현재 canonical 조립 중 만들어지고, 경쟁 비교 `FactRecord`는 기본 보고서가
작성된 뒤 `_attach_competitive_position`에서 만들어진다. 따라서 현 구조에서
유료 작성 전 새 9장 게이트를 바로 실행하면 이 칸들이 비어 항상 중단된다.
가짜 Fact ID를 넣거나 시험의 `InjectedSlotFacts`를 복사하는 것은 사실 결속을
우회하므로 금지한다.

## 필요한 패치 지도

1. `app/src/shared/report_evidence/runtime_port.py`를 추가한다.
   `OfficialEvidenceCollector` Protocol과 원문 없는 입력 DTO를 정의한다. 입력은
   회사 ID·법인명·공식 별칭·DART 원본 홈페이지·수집 시각·기존 DART 호출
   callable/카운터를 담고, 출력은 shared 계약의
   `ChapterEvidenceCandidates`만 담는다. pipeline feature는 이 shared port만
   import한다.

2. `app/src/web/official_evidence_adapter.py`를 추가한다. web은 조립 계층이므로
   feature 간 직접 import 금지를 깨지 않고 다음 구현을 합성할 수 있다.

   ```text
   DartRuntimeFetcher(engine.get_json, engine.download_document, 같은 counter)
     -> collect_dart_evidence -> harvest_to_mapping
   collect_official_web_documents
     -> build_fragments_for_collection -> to_evidence_mappings
   두 envelope
     -> produce_from_collection_envelopes
   ```

   DART 어댑터에는 기존 `_MeteredEngine`의 `get_json`, `download_document`,
   `RAW_DIR`, 요청별 `counter`, 고정 `business_date`를 주입한다. 별도 키나 별도
   사용량 카운터를 만들지 않는다. 홈페이지는 DART `hm_url` 원문과 회사 공식
   별칭을 받는다.

3. `app/src/web/runtime.py::make_pipeline`이 위 adapter를 만들어
   `RealPipeline` 생성자에 주입한다. `features.pipeline.real`에서 homepage,
   chapter_evidence, engine evidence_collection을 직접 import하지 않는다.
   단위시험은 네트워크 없는 fake port를 주입한다.

4. `app/src/features/pipeline/real.py::RealPipeline._run_metered`에서
   `generation_coordination.coordinate`의 cache miss 뒤, `_collect`와 같은 무료
   수집 구간에서 port를 정확히 한 번 호출한다. `ensure_paid_phase` 전임을
   assertion/시험으로 고정한다. 실패·TRUNCATED는 빈 자료로 접지 않고 후보의
   UNKNOWN 진단으로 운반한다.

5. `app/src/features/pipeline/canonical_report.py`의 표 사실 생성부를 분리해
   3개년 실적 `FactRecord`를 유료 writer 전에도 동일한 ID로 만들 수 있게 한다.
   그 실제 ID만 `past_changes:historical_performance`의
   `InjectedSlotFacts`에 넣는다.

6. `app/src/features/company_comparison`의 자료 획득·코드 검증과 보고서 문장
   부착을 분리한다. 비교 대상·지표·기준·판단·한계의 검증된 Fact ID를 유료
   writer 전에 만들 수 있어야 한다. 현재처럼 기본 보고서 뒤에만 만들 수 있다면
   9장 일괄 게이트 정책과 실행 순서를 함께 재설계해야 하며, 가짜 ID로 메우지
   않는다.

7. 위 실제 Fact ID와 `produce_from_collection_envelopes`의 후보로
   `build_section_bundle` 9개를 만들고 `assess_generation_gate`를 실행한다.
   `STOP_INSUFFICIENT_EVIDENCE`와 `STOP_TRANSIENT_FAILURE`는 모두 pipeline의
   `Outcome.GATE_STOPPED`로 옮기되 서로 다른 닫힌 `final_gate_reason`을 남긴다.
   두 중단 모두 `generation_coordination.ensure_paid_phase`와 Anthropic 호출이
   0회여야 한다.

8. v1/v2 작성 입력을 legacy `frags`가 아니라 검증된
   `SectionEvidenceBundle`의 documents/fragments/fact IDs에서 만들도록
   adapter를 추가한다. provenance `Source`도 `CollectedEvidenceDocument`의
   canonical URL·publisher·시점·해시 결속에서 만든다. 이 소비부가 붙기 전에는
   새 수집기를 호출만 하지 않는다.

9. 오프라인 서비스 종단시험을 추가한다.

   - cache hit과 single-flight waiter는 새 수집기 0회, owner만 1회
   - invalid/다른 회사 envelope는 provider 0회
   - REQUIRED 실패·TRUNCATED는 `GATE_STOPPED`이고 provider 0회
   - 실제 검증 Fact ID가 없으면 READY가 되지 않음
   - READY일 때만 paid phase가 열리고 composer가 bundle의 근거를 소비함
   - legacy homepage/IR 수집은 동등성 확인 뒤 제거하여 이중 네트워크 호출 방지

이 패치 묶음은 collector 호출 한 줄이 아니라 single-flight, typed Fact 생성
시점, 9장 게이트, 두 composer 입력, provenance를 함께 바꾸는 수직 슬라이스다.
따라서 현재 보안 수정과 분리해 독립 통합 작업으로 수행한다.
