# 문서 지도

처음 저장소를 받은 개발자·기획자는 아래 순서로 읽는다.

1. [출력물 기준](출력물%20기준/README.md): 신규 기업분석의 내용·목차·문체·근거·PDF
   품질을 정하는 최상위 정본
2. [런타임 출고 계약](출력물%20기준/90_공통_규칙/런타임_출고_계약.md): 코드와 시험이
   지켜야 하는 최소 스키마·입력·금지 항목·출고 게이트
3. [보고서 구조 요약](REPORT_STRUCTURE.md): 위 정본을 개발자가 빠르게 읽기 위한 요약
4. [시스템 개요](architecture/system-overview.md),
   [기능별 책임 지도](architecture/feature-map.md),
   [ADR 0001](adr/0001-feature-oriented-structure.md): 실행 경계와 기능 소유권
5. [현재 검수 안내](REVIEW_GUIDE.md): 환경, 실행, 시험, 승인, 운영 제한과 검수 체크리스트
6. [50개 매칭 표본 분석](evidence/reference-reports-50-analysis-summary.md): 목차 설계의
   배포 가능한 연구 근거 요약

애플리케이션 실행·배포는 [`app/README.md`](../app/README.md)와 `app/docs/`의 활성
가이드를 함께 본다. 분석 엔진 진입점과 동적 import 계약은
[`analysis_engine/README.md`](../analysis_engine/README.md)에 있다.

## 권위 순서

문서가 충돌하면 다음 순서로 판정한다.

1. `docs/출력물 기준/`의 20개 정본
2. 그중 기계 판정 최소조건을 고정한 런타임 출고 계약
3. 활성 코드 계약과 회귀 시험
4. 날짜 없는 현재 안내: `docs/REVIEW_GUIDE.md`, 루트·`app/`·`analysis_engine/` README,
   `app/docs/`
5. 날짜가 붙은 연구·리뷰·사용 가이드

하위 문서나 과거 리뷰가 정본을 바꾸지 않는다. 활성 코드가 정본과 다르면 코드가 새
정의가 된 것이 아니라 출고 차단 결함으로 기록한다. 정본을 바꾸려면 `출력물 기준/`
변경과 코드·시험·모든 출력 채널의 동시 검토가 필요하다.

## 날짜 문서와 증거

- [`docs/reviews/`](reviews/README.md): 특정 날짜의 검토 스냅샷이며 현재 release
  판정이 아님
- [`docs/research/`](research/README.md): 설계 당시 조사 자료이며 현재 규범이 아님
- `local-demo-user-guide-2026-08-18.md`: 당시 로컬 데모 절차의 기록. 현재 실행은
  `app/README.md`를 우선
- `docs/evidence/`: clean clone에서 열리는 정제 요약만 보관

비추적 원문·스크린샷·브라우저 JSON은 라이선스·용량 때문에 Git에 없을 수 있다. 그런
경로는 클릭 링크로 만들지 않고 로컬 경로, SHA-256, 관찰 요약만 기록한다. 현재 시험
수치는 과거 날짜 문서에서 가져오지 말고 `docs/REVIEW_GUIDE.md`의 명령으로 다시 확인한다.

## 변경 책임

- 내용·목차·출고 규칙 변경: `docs/출력물 기준/`과 관련 코드·시험을 한 변경으로 검토
- 기능 경계 변경: `architecture/feature-map.md`와 필요하면 ADR 갱신
- 배포·복구 변경: `app/docs/Render_배포.md`와 `.env.example` 및 배포 manifest 대조
- 검수 상태 변경: 날짜별 안내를 늘리지 말고 `docs/REVIEW_GUIDE.md`를 현재 상태로 갱신
- 과거 리뷰: 수정해 현재처럼 보이게 하지 말고 후속 날짜 문서나 현재 문서에서 상태를
  명시
