"""회계정책 주석 상용구를 «장을 가리지 않고» 알아보는 표지.

실측 배경 — 실제 산출 PDF 2건에서 9장 「회사가 밝힌 차별점」이 금융자산 측정·
정부보조금·현금성자산 정의·대손충당금 4문장으로, 5장 「당면 과제」가 이연법인세
미적용 문장으로, 1·2·5장이 수익인식 기준·재고자산 총평균법·유동성 관리 상용구로
채워졌다. 8장(culture)에는 전용 가드가 있었지만 나머지 8개 장에는 아무 검사가
없었다.

★ 이 목록이 잡으려는 것은 «회사 이름을 바꿔도 그대로 말이 되는 문장»이다.
  회사 고유의 사업·제품·거래상대·금액·사건이 없고, 회계 인식·측정·분류·표시
  기준이나 회계기준 적용 사실만 서술하는 절이 대상이다.

★ 낱말 하나로는 아무것도 막지 않는다. 범주마다 «대상어 + 회계 처리 표지»를
  같은 절에서 함께 요구한다. 실측이 그렇게 시켰다 —
    · 「정부보조금을 수령하고 관련 시스템 구축을 진행 중이다」(정상 사실)와
      「수익관련보조금은 선수금으로 계상하며」(상용구)가 둘 다 «보조금»을 쓴다.
    · 「영업외수익으로 이자수익과 임대료수입을 인식하고 있다」(정상 사실)와
      「위험과 보상이 이전되고 … 수익으로 인식한다」(상용구)가 둘 다 «인식»을 쓴다.
  그래서 보조금은 «회계처리 표지»를, 수익인식은 «인식 요건 표지»를 함께 요구한다.

⚠️ 글자수 상한(".{0,10}")은 쓰지 않는다 — 근거 없는 매직 넘버다. 판단 경계는
   `culture_constants.SOURCE_CLAUSE_SPLIT_RE` 로 나눈 «같은 절»이다(다른 가드와
   같은 경계를 쓴다).

⚠️ 검토·승인·감독 면제는 두지 않는다. culture 가드의 면제를 그대로 옮기면
   「재무제표는 … 주주총회에서 최종 승인될 예정이다」 같은 «승인» 상용구가
   오히려 면제를 만든다(실측 차단 목록 7번).
"""

from typing import Final
import re


#: 항목의 «모든» 절이 회계정책 상용구일 때 붙는 차단 사유.
#: ⚠️ `shared/report_quality/review_diagnostic_constants.REVIEW_SCOPE_ITEMS` 에
#:    아직 등록돼 있지 않다 — 등록 전에는 닫힌 진단 전송 계약
#:    (`observed_review_outcomes`)이 이 사유의 기록을 버린다. 요청 항목이다.
ACCOUNTING_POLICY_BOILERPLATE: Final[str] = "accounting_policy_boilerplate"

#: 일부 절만 상용구인 «혼합» 항목의 관측 사유. 차단하지 않는다 — 회사 고유
#: 사실이 같은 항목에 섞여 있으면 그 사실까지 함께 지우게 되기 때문이다.
ACCOUNTING_POLICY_MIXED: Final[str] = "accounting_policy_mixed"

# ── ① 금융상품 인식·측정 ──────────────────────────────────────────────
# 「금융기관과 대출 약정을 체결했다」(정상)는 대상어가 «금융자산/부채/상품»이
# 아니므로 여기 들어오지 않는다.
FINANCIAL_INSTRUMENT_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(
    r"금융(?:자산|부채|상품)"
)
FINANCIAL_INSTRUMENT_MEASURE_RE: Final[re.Pattern[str]] = re.compile(
    r"공정가치(?:로|를|의)?(?:측정|평가)|상각후원가|손상차손|유효이자율"
)

# ── ② 대손충당금 ─────────────────────────────────────────────────────
BAD_DEBT_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(
    r"대손(?:충당금|추산액|상각비)"
)
BAD_DEBT_TREATMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"설정|계상|인식|산출|산정|환입"
)

# ── ③ 현금및현금성자산 «정의» ────────────────────────────────────────
# 정의 문장은 거의 언제나 「취득 당시 만기 3개월 이내」를 함께 적는다. 현금
# 보유액·자금 사정을 말한 사실 문장에는 이 기간 표지가 없다.
CASH_EQUIVALENT_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(
    r"현금(?:및)?현금성자산"
)
CASH_EQUIVALENT_MATURITY_RE: Final[re.Pattern[str]] = re.compile(r"3개월|삼개월")

# ── ④ 정부보조금 회계처리 ────────────────────────────────────────────
# 「보조금을 수령했다」는 사실이고, 「수익관련보조금은 선수금으로 계상한다」가
# 상용구다. 둘을 가르는 것은 처리 표지뿐이다.
SUBSIDY_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(r"보조금")
SUBSIDY_TREATMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"수익관련보조금|자산관련보조금|선수금으로계상|상계처리|"
    r"당기의?손익에반영|영업외수익으로인식|이연수익"
)

# ── ⑤ 재고자산 평가방법 ──────────────────────────────────────────────
INVENTORY_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(r"재고자산")
INVENTORY_METHOD_RE: Final[re.Pattern[str]] = re.compile(
    r"총평균법|이동평균법|선입선출|후입선출|개별법|실지재고조사|저가법|"
    r"순실현가능가치|취득원가로계상"
)

# ── ⑥ 이연법인세 ─────────────────────────────────────────────────────
DEFERRED_TAX_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(r"이연법인세")
DEFERRED_TAX_TREATMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"적용|회계처리|인식|계상|반영|산정"
)

# ── ⑦ 회계기준 적용 사실 ─────────────────────────────────────────────
ACCOUNTING_STANDARD_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(
    r"기업회계기준|국제회계기준|k-ifrs"
)
ACCOUNTING_STANDARD_CONTEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"적용|작성|따라|따른|준거|기준서|표시"
)

# ── ⑧ 재무제표 표시·승인 상용구 ──────────────────────────────────────
FINANCIAL_STATEMENT_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(r"재무제표")
FINANCIAL_STATEMENT_PRESENTATION_RE: Final[re.Pattern[str]] = re.compile(
    r"공정하게표시|승인(?:될|받을)?예정|주주총회에서(?:최종)?승인|"
    r"작성하고있|작성되었|주석으로기재"
)

# ── ⑨ 수익인식 «기준» ────────────────────────────────────────────────
# ★ 「시점에 인식된다」만으로는 절대 막지 않는다 — 「콘텐츠 매출은 … 이용하는
#   시점에 인식된다」는 수익원을 설명하는 사업 문장이기도 하다(경계 사례).
#   회계기준 주석에만 나오는 «인식 요건» 표지가 같은 절에 있어야 막는다.
REVENUE_RECOGNITION_ACT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:수익|매출|비용|손익|원가)(?:으로|로|을|를)?(?:인식|계상)|"
    r"시점에인식|인식되며|인식된다"
)
REVENUE_RECOGNITION_CRITERION_RE: Final[re.Pattern[str]] = re.compile(
    r"위험과보상|신뢰성있게측정|수행의무|선수수익|진행기준|인도기준|완성기준|"
    r"거래가격|변동대가|개별판매가격|대체용도|지급청구권"
)

# ── ⑩ 회계처리 방법·특례 서술 ────────────────────────────────────────
# ⚠️ 처리 표지에 「한다」·「하고있」·「적용」 같은 흔한 말을 넣지 않는다. 실측에서
#    「…회계처리하며 … 방식을 보상위원회가 매 결산기마다 검토하고 승인한다」처럼
#    실제 «보상위원회 승인 절차»를 말한 문장이 「한다」 하나로 걸렸다. 그 문장은
#    저장소가 「반드시 보존」으로 못 박아 둔 것이다
#    (`tests/test_culture_compensation_accounting.py` 의 PRESERVED_PROCEDURE_TEXT).
#    이 규칙이 노리는 것은 「회계처리 특례」·「회계처리 기준」·「회계정책 변경의
#    방법」 같은 «주석 제목 꼴»이다.
ACCOUNTING_TREATMENT_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(
    r"회계처리|회계정책|회계추정"
)
ACCOUNTING_TREATMENT_CONTEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"특례|기준|방법|정책"
)

# ── ⑪ 유동성 관리 상용구 ─────────────────────────────────────────────
# 「부채상환을 포함하여 합리적으로 예상되는 영업자금수요를 충당할 수 있는
# 유동성을 예측하고 관리하고 있습니다」는 DART 서식이 회사·업종을 가리지 않고
# 그대로 싣는 문장이다.
LIQUIDITY_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(r"유동성")
LIQUIDITY_BOILERPLATE_RE: Final[re.Pattern[str]] = re.compile(
    r"영업자금수요|예측하고관리|부채상환|자금수요를충당|유동성위험"
)

#: 범주 이름 → (대상어, 처리 표지). 한 절이 어느 한 쌍을 «둘 다» 만족하면
#: 그 절은 회계정책 상용구다. 범주 이름은 로그·시험에서 어느 규칙이 걸렸는지
#: 되짚는 데만 쓴다 — 사유 코드는 언제나 하나다.
ACCOUNTING_POLICY_RULES: Final[tuple[tuple[str, re.Pattern[str], re.Pattern[str]], ...]] = (
    ("금융상품측정", FINANCIAL_INSTRUMENT_SUBJECT_RE, FINANCIAL_INSTRUMENT_MEASURE_RE),
    ("대손충당금", BAD_DEBT_SUBJECT_RE, BAD_DEBT_TREATMENT_RE),
    ("현금성자산정의", CASH_EQUIVALENT_SUBJECT_RE, CASH_EQUIVALENT_MATURITY_RE),
    ("정부보조금", SUBSIDY_SUBJECT_RE, SUBSIDY_TREATMENT_RE),
    ("재고자산평가", INVENTORY_SUBJECT_RE, INVENTORY_METHOD_RE),
    ("이연법인세", DEFERRED_TAX_SUBJECT_RE, DEFERRED_TAX_TREATMENT_RE),
    ("회계기준적용", ACCOUNTING_STANDARD_SUBJECT_RE, ACCOUNTING_STANDARD_CONTEXT_RE),
    ("재무제표표시", FINANCIAL_STATEMENT_SUBJECT_RE, FINANCIAL_STATEMENT_PRESENTATION_RE),
    ("수익인식기준", REVENUE_RECOGNITION_ACT_RE, REVENUE_RECOGNITION_CRITERION_RE),
    ("회계처리방법", ACCOUNTING_TREATMENT_SUBJECT_RE, ACCOUNTING_TREATMENT_CONTEXT_RE),
    ("유동성관리", LIQUIDITY_SUBJECT_RE, LIQUIDITY_BOILERPLATE_RE),
)
