# 문체 진단(지난 일정 미래형 개수)의 실행 기록 전달 — 2026-09-23

작성 기준 HEAD `bd2b5da4`(통합 브랜치 `fix/report-quality-integration-20260922` 머리와 같음). 작업 브랜치 `Jhh-wW/fix-style-observation-20260923`, 별도 워크트리. 커밋하지 않았다(조정자가 최종 검사 뒤 담당). 유료 호출·서버·기존 DB·비밀 파일은 건드리지 않았다.

## 결론

- **정정.** 배경 보고는 「문체 정규화기가 개수를 composer 싱크로 보내지만 `observed_composition_steps`가 닫힌 step 계약 밖이라 버린다」였다. 실제는 달랐다. `composer/pipeline.py`의 `_record_style_diagnostics`는 `logger.info(extra=pipeline_style_diagnostics)`로 **운영 로그에만** 남기고 실행 기록 싱크(`composition_diagnostics_sink`)에는 **아무것도 넣지 않았다**. 버려지는 기록조차 없었고, 실행 진단(`steps`)에는 이 값이 한 번도 실리지 않았다. 다른 워크트리의 미커밋 변경도 읽기 전용으로 확인했으며 싱크 배선은 없었다.
- 「보내면 버려진다」는 절반은 맞다. `shared/report_quality/composition_diagnostics.py`의 `observed_composition_steps`는 닫힌 10개 step 분기뿐이라 문체 step이 없었다.
- **수정.** 공유 계약에 step `8_문체_표기`, 닫힌 렌더 구분 칸 `렌더`(`1차`/`보충`/`확보근거`), 칸 `사유별`, 닫힌 사유 `past_dated_future_tense`를 추가하고, 정규화기에 `_style` 분기를 넣고, composer 송신부가 세 호출부(확보 근거·본 경로·보충 경로)에서 각자 렌더 구분을 실어 싱크에 넣게 했다. 0건이면 로그도 싱크 기록도 만들지 않는다. 기존 로그는 그대로다.
- **독립 검토가 찾은 것.** 보충 실행(`RUN_SUPPLEMENTS`)에서는 1차 렌더 기록 뒤 조기 반환 없이 보충 병합본 렌더가 다시 기록되고, 출고되는 것은 보충 쪽이다(`pipeline.py` 2555 → 2622 보충 결정 → 2862). 구별 칸이 없으면 보충 대상이 아닌 장의 같은 문장이 두 기록에 거듭 세어진다. HEAD의 로그도 원래 두 번 남기던 동작이지만 이번 변경으로 `steps`까지 번지므로, 조정자 결정(B안)대로 닫힌 «렌더» 칸을 계약에 넣어 소비자가 구별하게 했다.
- **검증.** 대상 5개 시험 파일 136 통과(기준선 98 → 1차 124 → B안 136). 변이 8종 전부 시험이 잡음. 3폴더 회귀 5,066 통과·2 실패는 로컬 공개 픽스처 부재였고, 조정자가 준 공개 DART XML 2개(해시 일치)로 그 2건만 재실행해 2 통과.
- **남은 것.** 커밋·병합은 조정자 몫. 운영에서 실제로 `steps`에 `8_문체_표기`가 실리는지는 유료 실행 뒤 실행 진단을 봐야 확인된다(이번엔 오프라인 시험만).

## 조사 사실

| # | 사실 | 근거 |
|---|---|---|
| 1 | `_record_style_diagnostics`는 로그만 남기고 싱크에 넣지 않았다 | `app/src/features/composer/pipeline.py:1160`(수정 전), 커밋 `9eda8bab` 메시지 「운영 로그에 개수만 남긴다」, `test_style_diagnostics_wiring.py`도 로그만 단정 |
| 2 | 정규화기는 닫힌 step 분기라 문체 step이 없었다 | `app/src/shared/report_quality/composition_diagnostics.py:166`(수정 전) |
| 3 | 실행 기능은 `finally`에서 싱크를 정규화기로 걸러 `steps`에 옮긴다 → 차단·예외로 끝나도 전달된다 | `app/src/features/pipeline/real.py:7088` |
| 4 | 세 호출부 모두 싱크와 같은 객체인 `composition_diagnostics` 리스트가 스코프에 있다 | `pipeline.py` `_finish_evidence_available` 인자, `run_v2` 안 한 번만 바인딩(독립 검토 K6) |
| 5 | 관측 요약 한 줄 허용목록(`RUN_SUMMARY_STEP_NAMES`)에는 원래 `8_*` composition step이 없다 → 손댈 필요 없음 | `app/src/features/observability/constants.py:208` |
| 6 | `test_run_composition_diagnostics.py`는 `len(records) == len(sink)`를 단정한다 → 송신부가 넣기 시작하면 공유 계약이 반드시 받아야 한다 | `app/src/features/composer/tests/test_run_composition_diagnostics.py:52` |
| 7 | 보충 실행에서는 1차 렌더 기록 뒤 보충 렌더가 다시 기록된다(출고본은 보충 쪽) | `pipeline.py:2555`(1차 기록) → `:2622`(`RUN_SUPPLEMENTS`) → `:2862`(보충 기록), 조기 반환 없음 |

## 변경 파일 (6개, 소유권 범위 안)

| 파일 | 변경 |
|---|---|
| `app/src/shared/report_quality/composition_diagnostic_constants.py` | `STYLE_STEP="8_문체_표기"`, `STYLE_REASON_PAST_DATED_FUTURE_TENSE="past_dated_future_tense"`, `STYLE_REASONS`, `STYLE_COUNTS_FIELD="사유별"`, `STYLE_RENDER_FIELD="렌더"`, `STYLE_RENDER_PRIMARY/SUPPLEMENT/EVIDENCE_AVAILABLE="1차"/"보충"/"확보근거"`, `STYLE_RENDERS`. 사유 글자는 composer의 `style_normalizer_constants.PAST_DATED_FUTURE_TENSE`와 같은 값을 «따로 적는» 방식(장 이동 사유 코드와 같음, feature import 없음) |
| `app/src/shared/report_quality/composition_diagnostics.py` | `_style(record)` 추가(`:171`) + dispatch 한 줄(`:219`). 기존 10개 분기는 그대로 |
| `app/src/features/composer/pipeline.py` | `_record_style_diagnostics(diagnostics, composition_diagnostics, *, render)`(`:1166`). 비어 있지 않을 때만 기존 로그 + `sink.append({"step", "렌더", "사유별"})`. 호출부 3곳: `:1368` 확보 근거(`확보근거`), `:2555` 본 경로(`1차`), `:2862` 보충(`보충`). 「출고되는 렌더」 주석을 관측 대상과 맞게 고침. 본문·사용자 표시·정산·출고 흐름 변화 없음 |
| `app/src/shared/report_quality/tests/test_style_observation.py` | 신규 223줄. 계약 글자 고정, 닫힌 칸만 남기고 원문 버림, 열린 사유별 값 12종·사전 아님 4종·칸 없음·비슷한 step 이름 거부, 닫힌 렌더 3종 통과·열린 렌더 7종 거부·렌더 칸 없음 거부, 기존 진단 사이 순서·개수 보존, 중복 미병합, 생산자 기록 불변·결과 사본 |
| `app/src/features/composer/tests/test_style_diagnostics_wiring.py` | 운영 경로(`run_v2`)에서 싱크 도달 + `observed_composition_steps` 통과(렌더 `1차`), 기준일 이전이면 빈 이벤트 없음(다른 진단은 있음), 싱크 기록에 원문·회사명 없음·세 칸만, 확보 근거 0건 → 빈 이벤트 없음, 확보 근거·보충 호출부의 «실제 송신»(렌더 out-인자에 1을 심는 스파이, 보충은 `1차`·`보충` 2건 순서), 사유 코드가 공유 닫힌 목록에 있음, 송신 기록이 사본임 |
| `app/src/features/pipeline/tests/test_composition_diagnostics_delivery_errors.py` | 오염 싱크에 문체 기록 추가: 생존 1건(원문·본문 여분 키 포함) + 죽어야 하는 7건(문자열 개수·0건·bool·사유 밖 코드·빈 사유별·렌더 밖 값·렌더 칸 없음). manifest 오류·예산 소진·비예산 치명 오류 세 갈래 모두 `steps`에 정확히 닫힌 3건만 전달됨을 단정 |

## 계약 (정규화기가 받는 꼴)

```
{"step": "8_문체_표기", "렌더": "1차" | "보충" | "확보근거",
 "사유별": {"past_dated_future_tense": <int >= 1>}}
```

- `렌더`가 닫힌 세 값 밖(빈 문자열·None·정수·bool·목록·공백 붙은 비슷한 값)이거나 칸이 없으면, 또는 사유 목록 밖 키, `bool`(`type(value) is int`로 거름), 음수, 0, 문자열, 실수, `None`, 빈 `사유별`, 사전이 아닌 `사유별`, `사유별` 칸 없음이면 → 기록 **통째로** 버림(장 이동·처분 기록과 같은 fail-closed).
- 결과에는 `step`·`렌더`·`사유별` 세 칸만 옮겨 적는다. 원문·본문·응답·오류문 같은 여분 키는 결과에 없다.
- 0은 계약 밖이다. 0건이면 composer가 기록 자체를 만들지 않으므로(빈 이벤트 금지) 0이 오면 오염이다.
- 같은 기록이 두 번 와도 합치지 않는다. 보충 실행에서 `1차`(출고되지 않은 후보 렌더)와 `보충`(출고된 병합본)이 각각 남으며, 출고본 개수는 `보충` 기록을, 보충이 없는 실행은 `1차` 기록을 보면 된다.
- 이 기록은 «검수 제외» 장부(`REVIEW_SCOPE_ITEMS`)가 아니다. 시제 표기는 문장을 빼지 않으므로 제외 장부에 넣으면 화면 안내문이 안 뺀 문장을 「…개를 뺐습니다」로 센다.

## 시험 근거

실행 조건: root `.venv`(Python 3.13.15), `PYTHON_DOTENV_DISABLED=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, 새 짧은 임시 폴더의 `STORAGE_DB_PATH`와 `--basetemp`, `app/conftest.py` 유지, `-X utf8 -B -p no:cacheprovider`, `app/`에서 실행. 유료 호출·서버·기존 DB 없음. 같은 워크트리에서 pytest는 한 번에 하나만 돌렸다.

| 묶음 | 결과 |
|---|---|
| 대상 4개 파일(공유 정규화기·배선·실행 진단·전달 오류), 수정 전 | 98 통과 |
| 대상 4개 + 신규 `test_style_observation.py`, 1차 수정 후 | 124 통과 |
| 같은 5개 파일, B안(렌더 칸) 후 | 136 통과, 4.90초 |

## 변이 검사 (시험이 실제로 지켜 주는가)

구현을 하나씩 망가뜨리고 돌린 뒤 원본을 복구했다(파일 내용 비교로 복구 확인).

| 변이 | 결과 | 잡은 시험 |
|---|---|---|
| M1 정규화기 dispatch 제거(문체 step을 계약 밖으로) | 10 실패 | 공유 신규 5, 배선 2, 전달 오류 3 |
| M2 송신부 `append` 제거(로그만 남김 = 수정 전 상태) | 3 실패 | 배선 3 |
| M3 개수 하한 완화(`minimum=1` 제거 → 0·음수 허용) | 4 실패 | 공유 신규 1, 전달 오류 3 |
| M4 사유 목록 검사 제거(열린 키 허용) | 5 실패 | 공유 신규 2, 전달 오류 3 |
| M5 빈 `사유별` 허용 | 4 실패 | 공유 신규 1, 전달 오류 3 |
| M6 확보 근거 호출부 싱크를 빈 리스트로 | 1 실패 | 배선(확보근거 실제 송신) |
| M7 보충 호출부 싱크를 빈 리스트로 | 1 실패 | 배선(보충 실제 송신) |
| M8 보충 렌더 구분을 `1차`로 잘못 적음 | 1 실패 | 배선(보충 `1차`·`보충` 순서 단정) |

M1~M5는 1차 수정 시점의 4개 시험 파일(38건), M6~M8은 B안 뒤 배선 시험 파일(10건)로 돌렸다. M6·M7은 독립 검토가 「빈 리스트로 바꿔도 초록」이라고 지적한 바로 그 구멍이며, 새 배선 시험 2건이 막는다.

## 넓은 회귀

| 항목 | 값 |
|---|---|
| 1차 수정 후, `composer/tests` + `shared/report_quality/tests` + `pipeline/tests` 한 프로세스 | 5,066 통과 · 2 실패 · 경고 11 · 168.84초 |
| 실패 2건 | `test_candidate_recall.py::test_로컬통합_실제_CORPCODE에서_별명이_상위3에_든다`, `test_real_cache.py::test_로컬통합_삼성전자_저장원문은_가짜AI로_생성이후까지_무과금재현한다` — 둘 다 `@pytest.mark.local_integration`, 시험 스스로 「로컬 통합 시험을 선택했지만 …를 찾지 못했습니다」로 실패 선언. 이 워크트리에 공개 픽스처가 없었던 것 |
| B안 후, `composer/tests` + `shared/report_quality/tests` 한 프로세스 | 3,725 통과 · 0 실패 · 경고 11 · 103.17초 (`pipeline/tests`는 B안에서 전달 시험 파일만 바뀌어 위 5개 파일 실행으로 대신함) |

### 실패 2건 재확인 (조정자 지시 `msg_09cafec1c45a`)

조정자가 원래 작업 폴더에서 찾은 공개 DART XML 2개를 해시 확인 뒤 이 워크트리의 `app/.local_evaluation_runs/style-public-fixture/analysis_engine/` 아래 대응 경로로만 복사했다(`app/.gitignore:5`로 추적 제외, DB·세션·비밀 파일 없음). 시험은 `*/analysis_engine/corpcode/CORPCODE.xml`, `*/analysis_engine/pilot/raw_filings/20260310002820.xml`을 glob으로 찾고 후자는 해시까지 대조한다.

| 파일 | SHA-256 | 크기 |
|---|---|---|
| `pilot/raw_filings/20260310002820.xml` | `107f3645e46dcd5af1ba7613d5480304c1ceaa98a89ac3a70fd113d23365d163` | 8,266,498 B |
| `corpcode/CORPCODE.xml` | `67de29f0c8c7927eba9e888fb9c3063e9a6c6e8cca4175f6cb0bab6dd7d20239` | 30,239,102 B |

결과: 그 2건만 새 격리 DB(`so0923b\storage.db`)·`--basetemp so0923b\pytest`로 실행해 **2 통과, 21.60초**. 기존 가짜 AI 시험 그대로, 네트워크·AI 호출 없음.

## 독립 검토 (Opus, 읽기 전용, pytest 미실행)

차단 결함 0, 권고 3. K1 닫힌 계약·K2 원문 차단·K3 불변/사본·K4 기존 계약 불변·K5 feature 경계·K6 0건 규칙/같은 싱크·K7 finally 전달·K9 요약 허용목록 통과. 권고와 처리:

| 권고 | 처리 |
|---|---|
| 확보 근거·보충 호출부의 싱크 배선을 지키는 시험 없음(빈 리스트로 바꿔도 초록) | 배선 시험 2건 추가, M6·M7로 빨강 확인 |
| 보충 실행에서 출고되지 않은 1차 렌더 개수도 `steps`에 실리고 구별 칸이 없음 | 조정자 B안 — 닫힌 `렌더` 칸 추가, M8로 확인 |
| 「2·4번째가 출고 렌더」·「출고되는 렌더에서만」 주석이 보충 실행에선 부정확 | `pipeline.py` 두 주석과 배선 시험 docstring·주석을 관측 대상(후보 본문의 최종 렌더·보충 병합본 렌더)에 맞게 고침 |

검토가 확인 못 한 것: 실제 시험 통과(pytest 금지) — 이 문서의 실행 결과가 대신한다. 「render_report 자체가 던지면 기록이 남지 않는다」는 사소한 한계는 그대로다(append 뒤 예외에만 finally 전달이 미친다).

## 하지 않은 것 · 범위 밖

- `composer/render.py`, `composer/style_normalizer*.py`, `pipeline/real.py`, `observability/constants.py`는 건드리지 않았다.
- 보고서 본문·사용자 표시·정산·출고 흐름은 바꾸지 않았다. `pipeline.py` 변경은 시그니처·append·인자 전달·주석뿐이다.
- 운영 실측은 하지 않았다. 유료 실행이 필요하다.
- 커밋·푸시·브랜치 변경·stash를 하지 않았다.

## 실행 방법 (Windows PowerShell, 실제 실행해 136 통과 확인)

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$tmp = "$env:TEMP\so0923"          # 새 짧은 임시 폴더
New-Item -ItemType Directory -Force $tmp | Out-Null
$env:STORAGE_DB_PATH = "$tmp\storage.db"                    # 격리 DB (기존 DB 사용 금지)
Set-Location "$env:USERPROFILE\orca\workspaces\기업분석2\fix-style-observation-20260923\app"
& "$env:USERPROFILE\.claude\workspace\기업분석2\.venv\Scripts\python.exe" -X utf8 -B -m pytest -p no:cacheprovider --basetemp "$tmp\pytest" `
  src/shared/report_quality/tests/test_style_observation.py `
  src/shared/report_quality/tests/test_composition_diagnostics.py `
  src/features/composer/tests/test_style_diagnostics_wiring.py `
  src/features/composer/tests/test_run_composition_diagnostics.py `
  src/features/pipeline/tests/test_composition_diagnostics_delivery_errors.py -q --tb=short --show-capture=no
```

실패 2건 재확인은 같은 조건에서 `$tmp`만 `so0923b`로 바꾸고 대상을 위 두 로컬 통합 시험 노드 id로 지정한다(공개 픽스처가 `app\.local_evaluation_runs\*\analysis_engine\` 아래 있어야 한다).
