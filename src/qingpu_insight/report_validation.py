from __future__ import annotations

import re
from dataclasses import dataclass

from qingpu_insight.report_contracts import (
    BuyerReportDraft,
    EvidenceFact,
    EvidencePack,
    ReportClaim,
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    fact_id: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    issues: tuple[ValidationIssue, ...] = ()


SENSITIVE_PATTERNS: dict[str, re.Pattern] = {
    "phone": re.compile(r"0\d{1,3}[-.\s]?\d{6,8}"),
    "email": re.compile(r"\S+@\S+\.\S+"),
    "html_tag": re.compile(r"<[^>]+>"),
    "db_url": re.compile(
        r"(?:mysql|postgresql|sqlite|mongodb|redis)://\S+"
    ),
}

UNIT_KEYWORDS: dict[str, frozenset[str]] = {
    "twd": frozenset({"元", "萬", "價格", "總價", "開價"}),
    "twd_per_ping": frozenset({"/坪", "單價"}),
    "ping": frozenset({"坪"}),
    "years": frozenset({"年", "屋齡"}),
    "m": frozenset({"公尺", "米", "站"}),
    "count": frozenset({"筆", "件"}),
    "iso": frozenset(),
    "method": frozenset(),
}

NUMBER_RE = re.compile(r"\d[\d,.]*")


def _normalize_number(text: str) -> list[float]:
    results: list[float] = []
    for m in NUMBER_RE.finditer(text):
        start = m.start()
        if start > 0 and text[start - 1].isascii() and text[start - 1].isalpha():
            continue
        cleaned = m.group().replace(",", "")
        try:
            results.append(float(cleaned))
        except ValueError:
            continue
    return results


def _expand_with_wan_yi(numbers: list[float], text: str) -> list[float]:
    expanded = list(numbers)
    for n in numbers:
        pos = text.find(str(int(n)) if n == int(n) else str(n))
        if pos >= 0:
            after = text[pos + len(str(int(n)) if n == int(n) else str(n)):]
            if after.startswith("\u842c"):
                expanded.append(n * 10000)
            elif after.startswith("\u5104"):
                expanded.append(n * 100000000)
    return expanded


def _all_claims(draft: BuyerReportDraft) -> list[tuple[str, ReportClaim]]:
    entries: list[tuple[str, ReportClaim]] = [("summary", draft.summary)]
    for i, c in enumerate(draft.advantages):
        entries.append((f"advantages.{i}", c))
    for i, c in enumerate(draft.risks):
        entries.append((f"risks.{i}", c))
    for i, c in enumerate(draft.negotiation):
        entries.append((f"negotiation.{i}", c))
    for i, c in enumerate(draft.limitations):
        entries.append((f"limitations.{i}", c))
    return entries


def _check_fact_ids(
    path: str,
    claim: ReportClaim,
    valid_fact_ids: set[str],
    issues: list[ValidationIssue],
) -> None:
    for fid in claim.fact_ids:
        if fid not in valid_fact_ids:
            issues.append(
                ValidationIssue(
                    code="unknown_fact_id", path=path, fact_id=fid
                )
            )
    for fid in claim.numeric_fact_ids:
        if fid not in valid_fact_ids:
            issues.append(
                ValidationIssue(
                    code="unknown_fact_id", path=path, fact_id=fid
                )
            )


def _check_numbers(
    path: str,
    claim: ReportClaim,
    facts_by_id: dict[str, EvidenceFact],
    issues: list[ValidationIssue],
) -> None:
    if not claim.numeric_fact_ids:
        return
    text_numbers = _normalize_number(claim.text)
    text_numbers = _expand_with_wan_yi(text_numbers, claim.text)
    if not text_numbers:
        return
    allowed_values: set[float] = set()
    for fid in claim.numeric_fact_ids:
        fact = facts_by_id.get(fid)
        if fact is None:
            continue
        fact_nums = _normalize_number(fact.value)
        fact_nums = _expand_with_wan_yi(fact_nums, fact.value)
        allowed_values.update(fact_nums)
    for tn in text_numbers:
        if not any(abs(tn - av) < 0.01 for av in allowed_values):
            issues.append(
                ValidationIssue(
                    code="unsubstantiated_number", path=path
                )
            )
            return


def _check_sensitive_content(
    path: str, text: str, issues: list[ValidationIssue]
) -> None:
    for pat in SENSITIVE_PATTERNS.values():
        if pat.search(text):
            issues.append(
                ValidationIssue(code="sensitive_content", path=path)
            )
            return


def _check_unit_mismatch(
    path: str,
    claim: ReportClaim,
    facts_by_id: dict[str, EvidenceFact],
    issues: list[ValidationIssue],
) -> None:
    if not claim.numeric_fact_ids:
        return
    for fid in claim.numeric_fact_ids:
        fact = facts_by_id.get(fid)
        if fact is None:
            continue
        expected_kw = UNIT_KEYWORDS.get(fact.unit, frozenset())
        if not expected_kw:
            continue
        kw_found = any(kw in claim.text for kw in expected_kw)
        if not kw_found:
            issues.append(
                ValidationIssue(
                    code="unit_mismatch", path=path, fact_id=fid
                )
            )
            return


def validate_report(
    draft: BuyerReportDraft, pack: EvidencePack
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    valid_fact_ids = {f.fact_id for f in pack.facts}
    facts_by_id = {f.fact_id: f for f in pack.facts}

    for path, claim in _all_claims(draft):
        _check_fact_ids(path, claim, valid_fact_ids, issues)
        _check_numbers(path, claim, facts_by_id, issues)
        _check_sensitive_content(path, claim.text, issues)
        _check_unit_mismatch(path, claim, facts_by_id, issues)

    return ValidationResult(
        valid=len(issues) == 0, issues=tuple(issues)
    )
