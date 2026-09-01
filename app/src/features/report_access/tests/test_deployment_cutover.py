"""legacy 접근 snapshot이 의존하는 Render 단일-writer 배포 계약."""

from __future__ import annotations

from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
# ★ 배포 교체 순서는 날짜 인수인계가 아니라 «계약 문서»가 소유한다.
#   문서를 옮겨도 이 시험이 계약 문장을 계속 붙잡는다.
DEPLOYMENT_CONTRACT = (
    REPOSITORY_ROOT / "docs" / "architecture" / "deployment-contract.md"
)


def test_Render_cutover는_영속disk_단일instance라서_old_new_writer가_겹치지않는다():
    blueprint = yaml.safe_load(
        (REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8")
    )
    web = next(
        service
        for service in blueprint["services"]
        if service.get("type") == "web"
        and service.get("name") == "company-analysis-beta"
    )

    assert web["numInstances"] == 1
    assert web["disk"] == {
        "name": "company-analysis-data",
        "mountPath": "/var/data",
        "sizeGB": 1,
    }
    # ★ Render 는 디스크가 붙은 서비스에 이 값을 «거부»한다 (2026-08-29 실측).
    #   넣으면 Blueprint 동기화가 실패해 배포 자체가 막힌다.
    assert "maxShutdownDelaySeconds" not in web


def test_cutover문서는_stop_drain_startup순서와_구성변경조건을_숨기지않는다():
    handoff = DEPLOYMENT_CONTRACT.read_text(encoding="utf-8")
    normalized = " ".join(handoff.split())

    assert "기존 인스턴스를 완전히 멈춘 뒤 새 인스턴스를 시작" in normalized
    assert "graceful drain → 기존 프로세스 종료 → 새 프로세스 시작" in normalized
    assert "공유 DB 또는 다중 instance" in handoff
    assert "bare report INSERT를 DB에서 막는 intent fence" in handoff
    assert "확인 못 함" in handoff
