"""
stat_make_review_report 의 해석 견고성 취합 테스트 (SPEC §7-8).

필수 검사 미완료 → None, 그 외에는 뒤집힘 > 취약 > 견고.
아래 자료는 모두 테스트용 가상 값이다. source=cache 는 판정 분기를 시험하기 위한 모의 주입이다.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tools import stat_make_review_report as report_module


def _survey(strata):
    return {"source": "cache", "strata": strata}


def _reference(h1, h2):
    return {"source": "cache", "strata": [{"id": "h1", "share": h1}, {"id": "h2", "share": h2}]}


# 응답률 100% → 무응답 가정을 바꿔도 값이 같다
FULL_RESPONSE = [
    {"id": "h1", "n": 80, "success": 64, "invited": 80},
    {"id": "h2", "n": 20, "success": 4, "invited": 20},
]
# 응답률 50% → d=-20%p 에서 68% 가 58% 로 내려간다
HALF_RESPONSE = [
    {"id": "h1", "n": 80, "success": 64, "invited": 160},
    {"id": "h2", "n": 20, "success": 4, "invited": 40},
]


def _run(monkeypatch, datasets, **overrides):
    monkeypatch.setattr(
        report_module, "_load_cache",
        lambda base, id_: (datasets[id_], datasets[id_]["source"]) if id_ in datasets else (None, None),
    )
    monkeypatch.setattr(report_module, "_save_artifact", lambda base, content: ("test.json", "sha256:test"))
    claim = {
        "id": "T01", "text": "테스트 주장", "kind": "proportion",
        "dataset_id": "survey", "reference_id": "reference",
        "reported_value": "68", "criterion_threshold": "0.60", "criterion_direction": "이상",
    }
    claim.update(overrides)
    tool = report_module.stat_make_review_report
    response = getattr(tool, "fn", tool)(json.dumps([claim], ensure_ascii=False))
    return response["result"]["rows"][0]


@pytest.mark.parametrize(
    "strata, shares, threshold, robustness, verdict",
    [
        # 재가중 견고(68%) + 무응답 견고
        (FULL_RESPONSE, ("0.8", "0.2"), "0.60", "견고", "인용 가능"),
        # 재가중 견고(68%) + 무응답 취약 → 취약
        (HALF_RESPONSE, ("0.8", "0.2"), "0.60", "취약", "조건부 인용"),
        # 재가중 취약(여유 2%p) + 무응답 견고 → 취약
        (FULL_RESPONSE, ("0.7", "0.3"), "0.60", "취약", "조건부 인용"),
        # 재가중 뒤집힘(50%) + 무응답 취약 → 뒤집힘
        (HALF_RESPONSE, ("0.5", "0.5"), "0.60", "뒤집힘", "사용 보류"),
        # 재가중 견고(74%) + 무응답 d=0 에서 이미 미달(68%) → 뒤집힘
        (FULL_RESPONSE, ("0.9", "0.1"), "0.70", "뒤집힘", "사용 보류"),
    ],
)
def test_report_takes_strictest_check(monkeypatch, strata, shares, threshold, robustness, verdict):
    row = _run(
        monkeypatch,
        {"survey": _survey(strata), "reference": _reference(*shares)},
        criterion_threshold=threshold,
    )
    assert row["robustness"] == robustness
    assert row["verdict"] == verdict


def test_report_holds_when_reference_missing(monkeypatch):
    """지정한 기준 구성표가 없으면 무응답이 견고여도 판정 미완료 → 사용 보류."""
    row = _run(monkeypatch, {"survey": _survey(FULL_RESPONSE)})
    assert row["robustness"] is None
    assert row["verdict"] == "사용 보류"


def test_report_holds_when_reweight_not_identifiable(monkeypatch):
    """양의 기준 비중 층에 응답이 없으면 재가중 불가 → 사용 보류."""
    strata = [
        {"id": "h1", "n": 100, "success": 68, "invited": 100},
        {"id": "h2", "n": 0, "success": 0, "invited": 0},
    ]
    row = _run(monkeypatch, {"survey": _survey(strata), "reference": _reference("0.5", "0.5")})
    assert row["robustness"] is None
    assert row["verdict"] == "사용 보류"
