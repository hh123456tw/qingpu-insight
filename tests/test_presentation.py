from qingpu_insight.presentation import (
    format_total_price_wan,
    format_unit_price_wan,
    localize_confidence,
)


def test_total_price_uses_wan_and_one_decimal():
    assert format_total_price_wan(22_980_000) == "2,298 萬"
    assert format_total_price_wan(15_377_250) == "1,537.7 萬"


def test_unit_price_uses_wan_per_ping():
    assert format_unit_price_wan(586_700) == "58.7 萬／坪"


def test_invalid_money_and_confidence_labels():
    assert format_total_price_wan(None) == "—"
    assert format_total_price_wan(float("nan")) == "—"
    assert localize_confidence("high") == "高"
    assert localize_confidence("medium") == "中"
    assert localize_confidence("low") == "低"
