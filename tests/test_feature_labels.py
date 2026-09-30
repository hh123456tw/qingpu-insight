"""The shared feature label module covers every model feature the UI can show."""

from __future__ import annotations

import re
from pathlib import Path

from qingpu_insight.model_features import FEATURE_COLUMNS, PARKING_FEATURE_COLUMNS

STATIC = Path(__file__).parents[1] / "src" / "qingpu_insight" / "static"
TEMPLATES = Path(__file__).parents[1] / "src" / "qingpu_insight" / "templates"


def _labelled_features() -> set[str]:
    source = (STATIC / "feature_labels.js").read_text(encoding="utf-8")
    block = source[source.index("var FEATURE_LABELS = {") : source.index("};")]
    return set(re.findall(r"^\s+([a-z0-9_]+):", block, flags=re.MULTILINE))


def test_every_model_feature_has_a_chinese_label() -> None:
    missing = set(FEATURE_COLUMNS + PARKING_FEATURE_COLUMNS) - _labelled_features()
    assert missing == set()


def test_pages_that_show_features_load_the_label_module() -> None:
    for template in ("index.html", "admin.html"):
        html = (TEMPLATES / template).read_text(encoding="utf-8")
        assert "feature_labels.js" in html, template
