# 전체 회사명 뒤 보조사 때문에 발생한 뉴스 원문 오거부 수정

작성일: 2026-09-22 · 작업: `task_0986e7529fd1` · 배정: `ctx_f962e0fd6850`

## 결과와 범위

전체 법인명에 보조사 **도**가 붙은 실제 기사 문장이 회사명 경계 검사에서 탈락하는 결함을 재현하고 수정했다. 기존 조사 목록에 `도` 한 개만 추가했으며, 조사 다음에도 문자열 끝 또는 비문자 경계가 있어야 한다. 전체 회사명을 짧게 자르거나 새로운 브랜드 별칭을 생성하지 않는다. 관련 무료 회귀시험은 **184개 통과**했다.

운영 뤼튼 기록은 검색 응답 합계 200, 선별 56, 본문 시도 20, 읽음 15, 분석 호출 3/상한 3, 최종 사용 0이다. 읽은 15개를 모두 분석했다는 뜻이 아니다. 개별 원응답이 없어 기존 신원 실패 5건이 모두 오거부인지, 이번 결함과 같은 사례인지 확인할 수 없다. `identity_evidence_not_exact=4`를 이번 조사 경계 문제로 설명하지 않는다.

## 공개 원문과 수정 전후 재현

[이데일리 원문](https://www.edaily.co.kr/News/Read?mediaCodeNo=257&newsId=02007366645447608)은 2026-05-10 등록·수정됐으며, 2026-09-22 공개 페이지에서 본문을 확인했다. 해당 본문에는 **뤼튼테크놀로지스도**라는 전체 법인명과 조사 표기가 있다. 생성형 AI 플랫폼 운영 및 일본 법인의 매출 추이에 관한 연속 한 문장을 [회귀시험](../../app/src/features/news_intake/tests/test_name_particle_grounding.py)의 `ARTICLE_TEXT`에 보존했다.

입력 회사명은 `주식회사 뤼튼테크놀로지스`, 신원 문맥은 `생성형 AI 플랫폼 운영`, 별칭은 빈 값이다. 기사 본문·신원 근거·인용에 실제 원문을 그대로 사용하고, 검색·본문 수집·분석은 메모리 대역으로 주입했다. 원문을 확보한 날짜와 기사 발행일을 구분하며, 운영 당시 입력이나 AI 원응답을 재생했다고 주장하지 않는다.

| 재현 입력 | 수정 전 | 수정 후 |
|---|---|---|
| 실제 원문 한 문장을 신원 근거와 인용에 그대로 반환 | 채택 0, `identity_name_missing`, `grounded_identity_unverified` | 채택 1, 원문·범위·해시 보존 |
| 전체 법인명이 `는`으로 끝나는 별도 합성 신원 문장 + 동일한 실제 원문 인용 | 신원 통과 후 `grounded_subject_missing`, 채택 0 | 채택 1, 실제 인용 범위 보존 |
| 전체 이름은 같지만 대학 캠퍼스·입시 문맥인 합성 본문 | 이름 경계에서 먼저 탈락 | `identity_context_mismatch`로 계속 차단 |
| 인용의 단어를 바꿔 본문에 없는 문장 반환 | 신원 경계에서 먼저 탈락 | `grounded_text_not_exact`로 계속 차단 |

제품 수정 전에 새 시험을 먼저 실행해 **4 실패 / 5 통과**를 확인했다. 정상 원문의 채택 0이 핵심 실패이며, 나머지 세 실패는 수정 후 예상한 후속 거절 사유 대신 이름 검사가 먼저 막히는 상태였다. 이후 제품 수정과 회귀 보강 후 전체 선별 검사 184개가 통과했다. 새 시험은 추가 조사 대안만 불가능한 정규식으로 바꾸는 범위 한정 대역으로 기존 동작을 함께 재생하므로, Git 상태를 바꾸지 않고 두 탈락 경로의 수정 전후 차이를 계속 확인한다.

## 안전 기준과 호출·비용

- `뤼튼`처럼 검증되지 않은 짧은 이름은 추가하지 않았고 계속 거절한다. 향후 별칭 연결은 공식 문서로 같은 법인임을 검증하고 기존 입력 경계로 전달해야 한다.
- `뤼튼도시개발`, `뤼튼테크놀로지스도시개발`, `서울뤼튼테크놀로지스`, `뤼튼테크놀로지스재팬` 같은 부분 이름·접미사·자회사 후보는 통과하지 않는다. 모델의 `same_company=false`와 `material=false`도 그대로 존중한다.
- 같은 이름이더라도 사업 문맥이 다른 본문은 거절한다. 원문에 없는 인용, 변조한 신원 근거와 빈 인용의 조합도 거절한다. 원문을 고치거나 떨어진 문장을 합치는 복구를 추가하지 않았다.
- 두 재현 경로 모두 수정 전후 본문 호출 **1→1**, 분석 호출 **1→1**을 확인했다. 분석 요청 문자열·스키마·토큰 상한은 완전히 동일하며, 응답은 유료 API가 아닌 고정 대역이다. 추가 외부 호출이나 모델 재시도를 도입하지 않았다.
- 검색어 이름 목록도 전체 공식명 한 개로 그대로다. 실제 유료 실행 비용·전체 생성 시간·운영 채택률 개선은 측정하지 않았다. 실제 추론 과금이 항상 정확히 같다고 주장하지 않으며, 이번 수정으로 호출 또는 토큰 예산을 늘리지는 않았다.

## 검증 실행

루트 `.venv/Scripts/python.exe -X utf8`로 실행했다. `app/conftest.py`를 그대로 적용했으며 `--confcutdir`는 사용하지 않았다. 실행 전에 `app/.local_evaluation_runs/parallel-news-fix/isolated` 부모 디렉터리를 생성하고 다음 환경을 설정했다.

```powershell
$env:STORAGE_DB_PATH = "$env:USERPROFILE/.claude/workspace/기업분석2/app/.local_evaluation_runs/parallel-news-fix/isolated/storage.db"
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:PYTHON_DOTENV_DISABLED = '1'
```

`app` 폴더에서 다음 명령으로 관련 시험만 실행했다. 수집 단계의 저장 경로를 위 임시 경로로 지정했고, 시험별 저장 경로는 기존 `app/conftest.py`의 격리를 따른다. 직접 DB 조회, 서버 실행, `.env` 비밀 읽기, 유료 API 호출은 하지 않았다.

```powershell
& '../.venv/Scripts/python.exe' -X utf8 -m pytest -p no:cacheprovider `
  src/features/news_intake/tests/test_name_particle_grounding.py `
  src/features/news_intake/tests/test_identity_names.py `
  src/features/news_intake/tests/test_identity_recovery.py `
  src/features/news_intake/tests/test_grounded_contract.py `
  src/features/news_intake/tests/test_collection.py `
  src/features/news_intake/tests/test_metadata_relevance.py `
  -q --basetemp="$env:USERPROFILE/.claude/workspace/기업분석2/app/.local_evaluation_runs/parallel-news-fix/grounding-final-ctx-f962e0fd6850"
```

결과: **184 passed in 3.35s**, 종료 코드 0. `git diff --check`도 변경한 추적 소스 기준으로 통과했다.

## 이 작업자가 변경한 파일

- `app/src/features/news_intake/identity_names.py`: 기존 이름 경계에 새 닫힌 조사 대안 연결.
- `app/src/features/news_intake/name_boundary_constants.py`: 이번 원문에서 확인한 보조사 `도` 상수.
- `app/src/features/news_intake/tests/test_name_particle_grounding.py`: 공개 원문·안전 반례·전후 호출 계약 회귀 14개.
- `docs/reviews/2026-09-22-news-grounding-followup.md`: 이 보고서.

공유 브랜치의 `collection.py`, `constants.py`, 매체 등록 시험과 다른 작업자의 변경을 편집하지 않았다. `grounded.py`, `grounded_mapping.py`, `classify.py`도 수정할 필요가 없었다. checkout·commit·reset을 하지 않았다.

## 남은 한계

이번 보완은 전체 법인명 뒤 조사 하나에 한정된다. 공식 법인명과 브랜드명의 근거 연결, 다른 조사 복합형, 원응답의 신원 근거 비정확 복사 문제는 해결됐다고 볼 수 없다. 회사명 일치는 여전히 동일 법인·실질성의 충분조건이 아니며 기존 문맥·모델·원문 검증을 함께 거친다. 이데일리 원문이 운영의 읽음 15개나 분석 입력에 실제로 포함됐는지도 확인하지 못했다. 배포와 새 유료 보고서 생성은 하지 않았다.

조정자와의 재현·계획·결과 전달에는 Orca orchestration 스킬(`%USERPROFILE%/.agents/skills/orchestration/SKILL.md`)을 사용했다.
