# 2026-09-08 작업 중단 및 재개 인계

## 현재 상태

- 사용자가 토큰 부족으로 모든 작업 중단과 현재 변경의 Git 저장을 요청했다. 이 문서는 완료 선언이 아닌 중간 저장 기록이다.
- 기준 커밋은 `1692d4198dd974e11cb9cb236c1425ff1e38ac36`이며, 이번 저장은 그 뒤의 공급자 실패 처리와 회귀 테스트를 포함한다.
- 이번 중단에서는 푸시, Render 배포, 추가 유료 호출을 하지 않았다.
- 작업자 3명의 종료 상태를 Orca에서 확인했다. 평가 서버와 그 실행용 부모 프로세스도 종료를 확인했다. 평가 DB·체크포인트·기존 산출물은 삭제하거나 초기화하지 않았다.

## 사용자 요구사항

- 우리은행 한 곳이 아니라 기업 규모와 산업 전반에서 자료량에 비례하는 일관된 보고서 품질을 검증한다.
- DART를 중심으로 공식 발표와 신뢰할 수 있는 언론을 충분히 활용한다. 뉴스는 본문 근거와 별도 목록 모두에 반영하며, 보도된 숫자는 출처·날짜와 함께 쓴다.
- 자료 부족과 수집·분석 장애를 구분한다. 뉴스 하나를 형식적으로 끼워 넣는 방식은 요구사항을 충족하지 않는다.
- 유료 실험은 허용됐지만 미정산 비용을 임의로 0원 처리하거나 차단을 우회해서는 안 된다.
- 재개 요청 전에는 자동으로 작업자, 테스트, 평가 서버를 다시 실행하지 않는다.

## 이번에 저장하는 코드

- `app/src/features/pipeline/real.py`: 공급자 호출 실패 시 HTTP 상태만으로 0원을 가정하지 않고, 공통 게이트웨이의 비용 관측을 정산 기준으로 사용한다. 원래 예외를 담은 `ProviderCallFailed`를 보존한다.
- `app/src/features/news_intake/collection.py`: 공급자 실패를 단순 콘텐츠 분석 실패로 삼키지 않고 전파하여, 뒤따르는 예산 차단이 최초 원인을 가리지 않게 한다.
- 파이프라인과 뉴스 단계에 공급자 상태·전송 상태·비용 관측의 안전한 진단을 남긴다. 비밀값이나 원문 응답은 기록하지 않는다.
- SDK 요청 정규화, 실제 SDK 예외와 임시 SQLite 관측 기록, 후속 배치 중단, 비용 정산, 실패 진단 회귀 테스트를 저장한다.
- 위 변경은 오류 보존과 정산 일관성 수정이다. 실제 HTTP 400을 일으킨 요청의 정확한 문제까지 해결됐다는 의미는 아니다.

## 검증 결과와 한계

중단 시 총괄이 다음 오프라인 검사만 다시 실행했고, **67개 통과, 5.18초**를 확인했다. 작업 경로는 `app`이다.

```powershell
..\.venv\Scripts\python.exe -X utf8 -B -m pytest src/features/news_intake/tests/test_provider_fatal_boundary.py src/features/pipeline/tests/test_grounded_request_protocol.py src/features/pipeline/tests/test_provider_fatal_contract.py src/features/pipeline/tests/test_failure_classification.py src/features/pipeline/tests/test_request_metering.py src/features/pipeline/tests/test_run_diagnostics_wiring.py -q -p no:cacheprovider --tb=short --show-capture=no
```

- 앞선 구현 작업자는 뉴스·파이프라인 범위에서 1,288개 통과를 보고했다. 이는 별도 실행 결과이며 전체 앱 검증이 아니다.
- 최신 전체 앱 검증은 **미확인**이다. 작업자의 시작 해시 수집이 Windows PowerShell의 `Path.GetRelativePath` 미지원으로 실패했고, 최종 pytest 요약과 종료 코드도 확보하지 못했다. 성공으로 집계하지 않는다.
- 재개 시 호환되는 상대경로 계산으로 생산 파일 해시를 시작·종료에 수집하고, 출력과 종료 코드를 별도로 보존해 전체 검증을 다시 해야 한다.
- 이전의 전체 8,995개 통과 결과는 당시 실행 중 코드 변경이 있어 최신 고정본 성공으로 사용하지 않는다.
- 실제 8개 회사의 최종 품질 검증은 **0/8**이다. 임의의 전체 진행률을 제시하지 않는다.

## 실제 평가 중단 원인

- 로컬 평가 경로: `app/.local_evaluation_runs/20260908_194644_8e1048200cc85c4e4723bbe3`.
- 우리은행 뉴스 근거 분석에서 실제 공급자 요청 1회가 HTTP 400으로 실패했다. 요청 오류의 상세 원인을 복원할 응답 본문은 보존되지 않았다.
- 검색 206건, 선별 후보 43건을 관측했다. 검색 결과가 없었던 것이 아니라 근거 분석이 실패했다.
- `94.41원`은 실제 청구 확정액이 아니라 **미확정 보수부채**다. 별도 확인된 이전 사용액 `712.57원 / 15회`와 섞지 않는다.
- 다음 회사는 미정산 비용 보호장치로 차단됐다. 나머지 7개 회사는 실행하지 않았다. 체크포인트의 `running` 표기를 실제 실행 중으로 오해하지 않는다.
- 재개 전 `budget/state_machine.py`의 부채 정산과 `POST /admin/budget/settle`의 증빙·감사 절차를 검토한다. 증거 없이 `CONFIRM_ZERO`나 실제 사용액 확정을 하지 않는다.

## Orca 알림: 조사만 했으며 수정하지 않음

- 사용자는 **Codex 하위 작업자 알림만** 앞으로 다른 프로젝트·세션에서도 받지 않기를 원한다. 메인 Codex와 Claude Code 알림은 유지해야 한다.
- Orca 전체 알림, Windows 알림, UAC, 보안 설정, 설치 앱 파일, 관리 훅은 이번 조사에서 변경하지 않았다.
- 설치된 Orca `1.4.197`의 전역 알림 설정과 실제 전송 경로에는 확인한 범위에서 하위 작업자 전용 차단 옵션이 없었다. 설정의 `enabled`와 `agentTaskComplete`는 켜져 있었다.
- 실제 알림 전달 경계에는 `agentType`, `paneKey` 등의 정보가 있지만, 하위 작업자 식별과 완료 후 수명 경계를 추가 확인해야 한다. 모든 훅을 막으면 작업 상태 보고까지 손상될 수 있다.
- 공개 원본 저장소 `https://github.com/stablyai/orca`는 발견했지만 복제·수정·빌드·설치는 하지 않았다. 설치된 묶음 파일을 바꾸는 임시 조치를 영구 해결이라고 설명하지 않는다.
- 조사 작업자 두 명은 사용자 요청에 따라 완료 전 종료했다. 알림 수정은 미완료다.

## 같은 PC에 남긴 상세 자료

다음 자료는 Git 제외 대상이며 로컬에 보존했다. 다른 PC에는 자동으로 따라가지 않는다. 재개에 중요한 요약은 이 문서에 포함했다.

- `.local-artifacts/wave3-provider-fatal-implementation-20260908.md`
- `.local-artifacts/wave3-news-fatal-boundary-regression-20260908.md`
- `.local-artifacts/wave3-request-protocol-regression-20260908.md`
- `.local-artifacts/wave3-final-frozen-app-regression-20260908.md`
- `.local-artifacts/wave3-liability-resolution-path-20260908.md`
- 기존 평가 DB, 체크포인트와 실행별 진단 자료.

## 재개 시 순서

1. 이 중간 저장 커밋과 작업 트리 상태를 확인한다. 종료된 작업자는 자동 재사용·재시작하지 않는다.
2. 최신 코드의 전체 오프라인 검증과 실패 처리 변경의 최종 리뷰를 수행한다.
3. 공급자 HTTP 400 원인과 미정산 처리 문제를 증거에 따라 해결한 뒤 실제 회사별 평가를 재개한다.
4. 뉴스의 본문 인용·목록·시점·다양성 및 자료 부족 표시를 실제 보고서로 비교한다.
5. Orca의 하위 작업자만 걸러내는 지속 가능한 변경을 별도로 구현·검증한다. 메인·Claude 알림은 유지한다.
6. 미검증 상태를 구분해 보고하고, 그 뒤 커밋·푸시·배포 여부를 정한다.
