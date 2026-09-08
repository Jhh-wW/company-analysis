"""무료 DART 응답의 모호성을 추측으로 확정하지 않는지 검사한다."""

from tools.confirm_evaluation_manifest import confirm_cases


def test_only_unique_matching_official_identity_is_confirmed():
    proposal = {"schema_version": "company-evaluation-manifest-v1", "cases": [
        {"case_id": "A", "input_name": "예제", "expected_legal_name": "예제", "corp_code": ""},
        {"case_id": "B", "input_name": "모호", "expected_legal_name": "모호", "corp_code": ""},
    ]}
    corporations = [
        {"corp_code": "12345678", "corp_name": "예제"},
        {"corp_code": "12345679", "corp_name": "모호"},
        {"corp_code": "12345670", "corp_name": "모호"},
    ]
    calls = []

    def lookup(code):
        calls.append(code)
        return {"status": "000", "corp_code": code, "corp_name": "예제", "adres": "공식 주소"}

    result = confirm_cases(proposal, corporations, lookup)
    assert calls == ["12345678"]
    assert len(result["cases"]) == len(result["held_cases"]) == 1
    assert result["cases"][0]["identity_confirmed"] is True
    assert result["cases"][0]["address_hint"] == "공식 주소"
    assert result["cases"][0]["identity_evidence"]["availability_assessed"] is False
