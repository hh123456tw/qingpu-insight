from types import SimpleNamespace

from qingpu_insight.conversation_presentation import (
    project_citation_details,
    project_price_summary,
)


def _pack(*, asking="1,350 萬", low=14_034_000, point=17_546_000, high=21_058_000):
    return SimpleNamespace(
        facts=[
            {
                "id": "listing.price",
                "label": "開價總價",
                "value": asking,
                "source": "591",
            },
            {
                "id": "market.sample_size",
                "label": "樣本數",
                "value": "10 筆",
                "source": "實價登錄",
            },
        ],
        valuation={
            "low_estimate_twd": low,
            "point_estimate_twd": point,
            "high_estimate_twd": high,
            "confidence": "low",
            "confidence_reasons": ["估價區間較寬"],
        },
    )


def test_projects_boundary_based_price_summary():
    summary = project_price_summary(_pack())

    assert summary == {
        "asking_twd": 13_500_000,
        "low_twd": 14_034_000,
        "point_twd": 17_546_000,
        "high_twd": 21_058_000,
        "position": "below",
        "gap_twd": 534_000,
        "gap_percent": 3.8,
        "confidence": "low",
        "confidence_reason": "估價區間較寬",
    }


def test_projects_inside_above_and_inconsistent_states():
    assert project_price_summary(_pack(asking="1,800 萬"))["position"] == "inside"
    above = project_price_summary(_pack(asking="2,200 萬"))
    assert above["position"] == "above"
    assert above["gap_twd"] == 942_000
    assert above["gap_percent"] == 4.5

    inconsistent = project_price_summary(_pack(point=22_000_000))
    assert inconsistent["position"] == "inconsistent"
    assert inconsistent["gap_twd"] is None
    assert inconsistent["gap_percent"] is None


def test_missing_or_invalid_values_omit_summary():
    assert project_price_summary(_pack(asking="—")) is None
    assert project_price_summary(_pack(low=0)) is None
    assert project_price_summary(SimpleNamespace(facts=[], valuation=None)) is None


def test_projects_localized_citation_details_and_unknown_fallback():
    details = project_citation_details(
        _pack(), ["listing.price", "market.sample_size", "unknown.fact"]
    )

    assert details[0] == {
        "id": "listing.price",
        "label": "591 開價",
        "value": "1,350 萬",
        "source": "591",
    }
    assert details[1]["label"] == "相似成交案例數"
    assert details[2] == {
        "id": "unknown.fact",
        "label": "unknown.fact",
        "value": "",
        "source": "",
    }
