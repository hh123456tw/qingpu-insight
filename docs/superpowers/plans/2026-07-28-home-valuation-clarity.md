# Home and Valuation Clarity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Qingpu Insight easier to read and explain by using 萬-based prices, calibrated comparable similarity, compact evidence-backed AI answers, and a visually connected, shorter homepage.

**Architecture:** Keep database and HTTP price contracts as exact TWD numbers, and add small presentation modules at the Python and JavaScript boundaries. Replace relative comparable normalization inside `valuation.py` without changing the valuation response schema. Restructure only the homepage and assistant presentation layers; retain report services, APIs, benchmark code, model release logic, and historical data.

**Tech Stack:** Python 3.11+, Flask, Pandas, Pydantic, vanilla JavaScript UMD modules, Leaflet, Chart.js, Pytest, Node.js contract tests, Ruff.

## Global Constraints

- API, database, artifacts, and model inputs keep exact TWD numeric values.
- Visible total prices use `萬`; visible unit prices use `萬／坪`; non-integral values use at most one decimal.
- Visible confidence uses `高／中／低`; API confidence remains `high/medium/low`.
- The prediction-interval width threshold remains 30%.
- High-similarity evidence still requires at least 3 comparables with `similarity_score >= 0.60`.
- 591 asking-price premium is not a confidence failure and is never treated as a transaction price.
- Market endpoints still load at most 100 recent rows for the homepage; the map continues to use grouped map data.
- The homepage removes only the report form and its JavaScript caller; report backend APIs, providers, data, benchmark, CLI, and tests remain.
- Do not add dependencies, authentication, AutoML, SaaS features, or cloud deployment.
- All implementation tasks use TDD; each commit must leave its focused tests passing.

---

## File Structure

### New files

- `src/qingpu_insight/presentation.py` — Python-only monetary and confidence labels for EvidenceFact text.
- `src/qingpu_insight/static/display_format.js` — browser/CommonJS monetary, confidence, and legacy-message formatting.
- `src/qingpu_insight/static/market_results.js` — pure recent-row and active-filter presentation functions.
- `tests/test_presentation.py` — Python presentation contract.
- `tests/js/display_format_contract.cjs` — JavaScript presentation contract.
- `tests/js/market_results_contract.cjs` — market-result state contract.

### Existing files with focused changes

- `src/qingpu_insight/valuation.py` — absolute weighted similarity and missing-value weight normalization.
- `src/qingpu_insight/conversation_evidence.py` — 萬-formatted facts and asking-price comparison facts.
- `src/qingpu_insight/conversation_providers.py` — compact Rule summary and concise provider instruction.
- `src/qingpu_insight/conversation_validation.py` — validated visible answer without inline fact IDs.
- `src/qingpu_insight/static/assistant.js` — price-position visualization, compact citations, legacy rendering.
- `src/qingpu_insight/static/home_assistant.js` — accessible recent-conversation dropdown.
- `src/qingpu_insight/static/app.js` — shared formatting, market-result state, price-position rendering, and report-UI removal.
- `src/qingpu_insight/static/app.css` — common result shell, sticky context, dropdown, price-position, and form groups.
- `src/qingpu_insight/templates/index.html` — page order, market-result wrapper, report-form removal, and valuation fieldsets.
- `src/qingpu_insight/templates/assistant.html` — shared formatter script.
- `tests/test_valuation.py` — similarity and confidence regressions.
- `tests/test_conversation_evidence.py` — asking-price evidence.
- `tests/test_conversation_providers.py` — compact Rule claims.
- `tests/test_conversation_validation.py` — separate visible answer and citations.
- `tests/test_web.py` — homepage hierarchy/removal contract and report API retention.
- `tests/js/assistant_contract.cjs` — assistant rendering.
- `tests/js/home_assistant_contract.cjs` — history dropdown.
- `tests/js/admin_contract.cjs` and `tests/js/model_admin_contract.cjs` — verify existing admin monetary presentation remains compliant.
- `README.md` — current homepage flow, confidence meaning, and retained report backend.

---

### Task 1: Shared price and confidence presentation

**Files:**
- Create: `src/qingpu_insight/presentation.py`
- Create: `src/qingpu_insight/static/display_format.js`
- Create: `tests/test_presentation.py`
- Create: `tests/js/display_format_contract.cjs`

**Interfaces:**
- Produces Python:
  - `format_total_price_wan(value: object) -> str`
  - `format_unit_price_wan(value: object) -> str`
  - `localize_confidence(value: object) -> str`
- Produces JavaScript:
  - `formatTotalWan(value): string`
  - `formatUnitWan(value): string`
  - `localizeConfidence(value): string`
  - `normalizeLegacyMoneyText(value): string`
  - `pricePositionState(low, point, high, asking): object`

- [ ] **Step 1: Write failing Python presentation tests**

```python
from qingpu_insight.presentation import (
    format_total_price_wan,
    format_unit_price_wan,
    localize_confidence,
)


def test_total_price_uses_wan_and_one_decimal() -> None:
    assert format_total_price_wan(22_980_000) == "2,298 萬"
    assert format_total_price_wan(15_377_250) == "1,537.7 萬"


def test_unit_price_uses_wan_per_ping() -> None:
    assert format_unit_price_wan(586_700) == "58.7 萬／坪"


def test_invalid_money_and_confidence_labels() -> None:
    assert format_total_price_wan(None) == "—"
    assert format_total_price_wan(float("nan")) == "—"
    assert localize_confidence("high") == "高"
    assert localize_confidence("medium") == "中"
    assert localize_confidence("low") == "低"
```

- [ ] **Step 2: Run Python tests and verify import failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_presentation.py -q
```

Expected: FAIL because `qingpu_insight.presentation` does not exist.

- [ ] **Step 3: Implement the Python formatter**

Use `Decimal(str(value))`, reject non-finite values, divide by `Decimal("10000")`, quantize to one decimal with `ROUND_HALF_UP`, strip a trailing `.0`, then add a `zh-TW`-style comma separator. Do not return `$` or raw `元`.

```python
_CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}


def format_total_price_wan(value: object) -> str:
    amount = _finite_decimal(value)
    if amount is None or amount <= 0:
        return "—"
    return f"{_format_wan_number(amount)} 萬"


def format_unit_price_wan(value: object) -> str:
    amount = _finite_decimal(value)
    if amount is None or amount <= 0:
        return "—"
    return f"{_format_wan_number(amount)} 萬／坪"
```

- [ ] **Step 4: Write the failing JavaScript contract**

```javascript
const assert = require("node:assert/strict");
const display = require("../../src/qingpu_insight/static/display_format.js");

assert.equal(display.formatTotalWan(22980000), "2,298 萬");
assert.equal(display.formatTotalWan(15377250), "1,537.7 萬");
assert.equal(display.formatUnitWan(586700), "58.7 萬／坪");
assert.equal(display.formatTotalWan(null), "—");
assert.equal(display.localizeConfidence("low"), "低");
assert.equal(
  display.normalizeLegacyMoneyText("開價 22,980,000 元，單價 586,700 元/坪"),
  "開價 2,298 萬，單價 58.7 萬／坪"
);
assert.deepEqual(
  display.pricePositionState(15380000, 19890000, 24410000, 22980000),
  { pointPercent: 50, askingPercent: 84.35, askingPosition: "inside" }
);
```

- [ ] **Step 5: Run the JavaScript contract and verify module failure**

Run:

```powershell
node tests/js/display_format_contract.cjs
```

Expected: FAIL because `display_format.js` does not exist.

- [ ] **Step 6: Implement the UMD JavaScript formatter**

Use the repository’s existing UMD pattern. `normalizeLegacyMoneyText` must match only `([\d,]+(?:\.\d+)?)\s*元(?:\s*[/／]\s*坪)?`, preserving all other numbers.

```javascript
function formatWanNumber(value) {
  var numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return null;
  return new Intl.NumberFormat("zh-TW", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 1,
  }).format(numeric / 10000);
}
```

`pricePositionState` returns clamped `0..100` marker percentages and `askingPosition` as `inside`, `below`, `above`, or `missing`. A zero-width or invalid interval returns a centered point marker and `missing` asking state without throwing.

- [ ] **Step 7: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_presentation.py -q
node tests/js/display_format_contract.cjs
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/presentation.py tests/test_presentation.py
```

Expected: all pass.

- [ ] **Step 8: Commit**

```powershell
git add src/qingpu_insight/presentation.py src/qingpu_insight/static/display_format.js tests/test_presentation.py tests/js/display_format_contract.cjs
git commit -m "feat(ui): add shared price presentation"
```

---

### Task 2: Calibrated comparable similarity

**Files:**
- Modify: `src/qingpu_insight/valuation.py:150-230`
- Modify: `tests/test_valuation.py`

**Interfaces:**
- Produces:
  - `_component_similarity(left: object, right: object, tolerance: float) -> float | None`
  - `_comparable_similarity(input_row: pd.Series, candidate: pd.Series, max_date: pd.Timestamp) -> float`
- Preserves: `similar_transactions(...) -> {"comparables": list[dict], "comparable_scope": str}`

- [ ] **Step 1: Add failing absolute-score regression tests**

Create tests that assert:

```python
def test_comparable_similarity_is_absolute_not_batch_relative() -> None:
    close = _candidate(area=31, distance=520, bedrooms=3, months_old=6)
    far = _candidate(area=80, distance=1900, bedrooms=1, months_old=35)
    base = similar_transactions(bundle, input_row, _market([close]))
    with_outlier = similar_transactions(bundle, input_row, _market([close, far]))
    assert base["comparables"][0]["similarity_score"] == (
        with_outlier["comparables"][0]["similarity_score"]
    )


def test_missing_optional_age_renormalizes_weights() -> None:
    candidate = _candidate(age=float("nan"))
    score = _comparable_similarity(input_row_without_age, candidate, max_date)
    assert 0 <= score <= 1


def test_typical_close_cases_clear_point_six() -> None:
    result = similar_transactions(bundle, input_row, market_with_three_close_cases)
    assert sum(c["similarity_score"] >= 0.60 for c in result["comparables"]) >= 3
```

Use at least three rows so the existing insufficient-data guard is not triggered.

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_valuation.py -k "absolute or renormalizes or close_cases" -q
```

Expected: FAIL because the score still depends on `max_dist`.

- [ ] **Step 3: Implement component and weighted similarity**

Implement the spec’s exact weights and tolerances:

```python
components = [
    (0.25, _component_similarity(ref_area, area, 20)),
    (0.20, _component_similarity(ref_distance, distance, 1000)),
    (0.15, _layout_similarity(input_row, candidate)),
    (0.10, _component_similarity(ref_age, age, 20)),
    (0.10, _component_similarity(ref_floor_ratio, floor_ratio, 0.5)),
    (0.10, 1.0 if ref_type == candidate_type else 0.0),
    (0.10, max(0.0, 1.0 - months_old / 60)),
]
available = [(weight, score) for weight, score in components if score is not None]
return sum(weight * score for weight, score in available) / sum(
    weight for weight, _ in available
)
```

Sort with `key=(-similarity_score, -transaction_timestamp)` and return the first five. Remove `max_dist` normalization and the old mixed-unit distance.

- [ ] **Step 4: Add confidence-boundary assertions**

Keep existing tests for interval width and degraded models. Add one integration assertion that a typical row with three calibrated comparables is not low solely because of comparable scoring, while a row with only dissimilar cases contains `高相似度成交案例不足`.

- [ ] **Step 5: Run valuation tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_valuation.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/valuation.py tests/test_valuation.py
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/valuation.py tests/test_valuation.py
git commit -m "fix(models): calibrate comparable similarity"
```

---

### Task 3: Compact asking-price evidence and validated answers

**Files:**
- Modify: `src/qingpu_insight/conversation_evidence.py`
- Modify: `src/qingpu_insight/conversation_providers.py`
- Modify: `src/qingpu_insight/conversation_validation.py`
- Modify: `tests/test_conversation_evidence.py`
- Modify: `tests/test_conversation_providers.py`
- Modify: `tests/test_conversation_validation.py`

**Interfaces:**
- Consumes Task 1: Python presentation functions.
- Produces EvidenceFact IDs:
  - `valuation.asking_gap_amount`
  - `valuation.asking_gap_percent`
  - `valuation.asking_position`
- Preserves citations in `ValidatedChatAnswer.citations`.

- [ ] **Step 1: Write failing EvidenceFact tests**

For an asking price of `22_980_000`, point estimate `19_890_000`, and interval `15_380_000..24_410_000`, assert:

```python
facts = {fact.id: fact.value for fact in evidence.facts}
assert facts["listing.price"] == "2,298 萬"
assert facts["listing.unit_price"] == "58.7 萬／坪"
assert facts["valuation.point"] == "1,989 萬"
assert facts["valuation.asking_gap_amount"] == "高於估值中心 309 萬"
assert facts["valuation.asking_gap_percent"] == "高於估值中心 15.5%"
assert facts["valuation.asking_position"] == "仍在合理區間內"
assert facts["valuation.confidence"] == "低"
```

Also cover below-range, above-range, and absent asking price.

- [ ] **Step 2: Run evidence tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_evidence.py -q
```

Expected: FAIL because current facts use raw 元 and do not include asking comparison facts.

- [ ] **Step 3: Implement formatted and derived evidence**

Use Task 1 formatters for listing price, unit price, valuation point/interval, comparable price, and market median. Compute the percentage with `(asking - point) / point * 100`, one decimal. Determine position using inclusive interval bounds.

- [ ] **Step 4: Write failing compact Rule and validation tests**

```python
def test_rule_summary_prioritizes_six_decision_facts() -> None:
    draft = RuleConversationProvider().reply(
        model="rule", question="摘要", context=context_with_asking_analysis
    )
    assert len(draft.property_claims) <= 6
    cited = {fid for claim in draft.property_claims for fid in claim.fact_ids}
    assert "listing.price" in cited
    assert "valuation.point" in cited
    assert "valuation.asking_position" in cited
    assert not any(fid.startswith("comparable.") for fid in cited)


def test_validated_answer_keeps_citations_out_of_visible_text() -> None:
    result = validate_chat_answer(draft, available_fact_ids={"listing.price"}, evidence_revision=1)
    assert result.citations == ["listing.price"]
    assert "依據：" not in result.answer
```

- [ ] **Step 5: Run provider and validation tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_providers.py tests/test_conversation_validation.py -q
```

Expected: FAIL because Rule returns broad evidence and visible answers include inline IDs.

- [ ] **Step 6: Implement compact answer composition**

Use this Rule priority:

```python
priority_ids = (
    "listing.price",
    "valuation.point",
    "valuation.asking_gap_percent",
    "valuation.low",
    "valuation.high",
    "valuation.asking_position",
    "valuation.confidence",
)
```

Select at most six present facts, prefer `valuation.asking_position` over a second low-value listing field, and keep at most one general-guidance line. Add to `_SYSTEM_PROMPT`: `Use 3 to 4 concise property_claims unless the evidence is missing.` Keep the Pydantic maximum at 30.

Change `_render_validated_answer` to render claim text without fact IDs. Do not weaken `_reject_unknown_fact_ids`, duplicate checks, or citation collection.

- [ ] **Step 7: Run focused suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_evidence.py tests/test_conversation_providers.py tests/test_conversation_validation.py tests/test_conversation_service.py -q
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/conversation_evidence.py src/qingpu_insight/conversation_providers.py src/qingpu_insight/conversation_validation.py
```

Expected: all pass.

- [ ] **Step 8: Commit**

```powershell
git add src/qingpu_insight/conversation_evidence.py src/qingpu_insight/conversation_providers.py src/qingpu_insight/conversation_validation.py tests/test_conversation_evidence.py tests/test_conversation_providers.py tests/test_conversation_validation.py
git commit -m "feat(assistant): summarize asking price evidence"
```

---

### Task 4: Assistant workbench presentation

**Files:**
- Modify: `src/qingpu_insight/templates/assistant.html`
- Modify: `src/qingpu_insight/static/assistant.js`
- Modify: `src/qingpu_insight/static/app.css`
- Modify: `tests/js/assistant_contract.cjs`

**Interfaces:**
- Consumes Task 1: `window.QingpuDisplayFormat`.
- Produces:
  - `stripLegacyInlineCitations(content): string`
  - `renderCitationDetails(citations): HTMLElement | null`

- [ ] **Step 1: Add failing assistant contracts**

Assert:

```javascript
const display = require("../../src/qingpu_insight/static/display_format.js");

assert.deepEqual(
  display.pricePositionState(15380000, 19890000, 24410000, 22980000),
  { pointPercent: 50, askingPercent: 84.35, askingPosition: "inside" }
);
assert.equal(
  asst.stripLegacyInlineCitations("開價 2,298 萬（依據：listing.price）"),
  "開價 2,298 萬"
);
```

Create an assistant message with two citations and assert that `renderMessage` contains a closed `<details>` with label `查看資料依據（2）`, while `.message-content` does not contain `listing.price`.

- [ ] **Step 2: Run assistant contract and verify failure**

Run:

```powershell
node tests/js/assistant_contract.cjs
```

Expected: FAIL because the new functions and details element do not exist.

- [ ] **Step 3: Load the formatter and implement assistant rendering**

Load `display_format.js` before `assistant.js`. Replace `formatMoney` calls with `formatTotalWan` or `formatUnitWan`. Render confidence with `localizeConfidence`.

Implement the price-position component with the clamped percentages returned by `QingpuDisplayFormat.pricePositionState`.

Include text fallback showing low, point, high, asking, and asking position.

- [ ] **Step 4: Implement citation and legacy display**

Run visible content through `normalizeLegacyMoneyText(stripLegacyInlineCitations(msg.content))`. Build `<details class="message-citations">` from the already validated `msg.citations`; never parse fact IDs from text.

- [ ] **Step 5: Add CSS and accessibility states**

Add `.price-position`, `.price-range-track`, `.price-marker`, `.message-citations`, and narrow-screen rules. Mark the chart wrapper with an accessible label; price markers may not be the only source of values.

- [ ] **Step 6: Run assistant tests**

Run:

```powershell
node tests/js/display_format_contract.cjs
node tests/js/assistant_contract.cjs
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_web.py tests/test_conversation_service.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```powershell
git add src/qingpu_insight/templates/assistant.html src/qingpu_insight/static/assistant.js src/qingpu_insight/static/app.css tests/js/assistant_contract.cjs
git commit -m "feat(assistant): simplify evidence presentation"
```

---

### Task 5: Homepage hierarchy, report-form removal, and valuation grouping

**Files:**
- Create: `src/qingpu_insight/static/valuation_form.js`
- Create: `tests/js/valuation_form_contract.cjs`
- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/app.js:300-640`
- Modify: `src/qingpu_insight/static/app.css`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes Task 1: `window.QingpuDisplayFormat`.
- Produces `firstErrorControlId(fields, fieldMap): string | null`.
- Preserves: `/api/reports` backend behavior.

- [ ] **Step 1: Write failing homepage structure tests**

Using BeautifulSoup on `/`, assert:

```python
home = BeautifulSoup(client.get("/").get_data(as_text=True), "html.parser")
assert home.find(["h1", "h2"]).name == "h1"
assert home.select_one("#assistant-starter") is not None
assert home.select_one("#report-form") is None
assert home.select_one("#report-result") is None
assert home.select_one("#valuation-form fieldset.basic-valuation-fields") is not None
assert home.select_one("#valuation-form fieldset.detailed-valuation-fields") is not None
```

Keep or add a separate API test proving `POST /api/reports` is still registered and protected by its existing validation/auth behavior.

- [ ] **Step 2: Run the web tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -k "home or report" -q
```

Expected: FAIL because the report form still exists and the page starts with an H2.

- [ ] **Step 3: Write and run the failing valuation-form contract**

```javascript
const assert = require("node:assert/strict");
const ui = require("../../src/qingpu_insight/static/valuation_form.js");

assert.equal(
  ui.firstErrorControlId(
    { total_floors: "must be >= floor", building_area_ping: "required" },
    {
      building_area_ping: "valuation-area",
      total_floors: "valuation-total-floors",
    }
  ),
  "valuation-total-floors"
);
assert.equal(ui.firstErrorControlId({}, {}), null);
```

Run:

```powershell
node tests/js/valuation_form_contract.cjs
```

Expected: FAIL because `valuation_form.js` does not exist.

- [ ] **Step 4: Implement the valuation-form helper**

Use a UMD module. Preserve the server’s field order by iterating `Object.keys(fields)`, return the first mapped control ID, and never access the DOM inside the helper.

- [ ] **Step 5: Restructure the template**

Move the product hero with H1 above `#assistant-starter`. Remove only `.report-panel`. Keep the assistant, market controls, market modules, method note, and valuation form.

Wrap valuation fields exactly as:

```html
<fieldset class="basic-valuation-fields">
  <legend>基本條件</legend>
  <!-- market, station, area, building type, bedrooms, age, asking price -->
</fieldset>
<fieldset class="detailed-valuation-fields">
  <legend>詳細條件</legend>
  <!-- distance, living rooms, bathrooms, floor, total floors, parking -->
</fieldset>
```

Do not hide required fields in `<details>`.

- [ ] **Step 6: Remove the homepage report caller**

Delete the `report-form` DOM lookup, provider notice handler, submit handler, response rendering, and report-only helper branches from `app.js`. Do not modify Flask report routes or report Python modules.

- [ ] **Step 7: Use 萬 formatting and price position in valuation results**

Load `display_format.js` before `app.js`. Replace visible total and unit monetary values. Add the same low/point/high/asking price-position component contract used by the assistant, plus a text statement that 591 asking price may include negotiation room.

- [ ] **Step 8: Add form grouping, hierarchy CSS, and error focus**

Use two responsive fieldset grids, visible legends, and error focus styling. Preserve the existing green/cream visual system. Load `valuation_form.js` before `app.js`. Before submission, call `form.reportValidity()`; for an API `error.fields` response, call `firstErrorControlId`, focus the returned element, and scroll it into view. Do not leave the user at a status message while the invalid control is off-screen.

- [ ] **Step 9: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -k "home or report or valuation" -q
node tests/js/display_format_contract.cjs
node tests/js/valuation_form_contract.cjs
.\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/web.py tests/test_web.py
```

Expected: all pass.

- [ ] **Step 10: Commit**

```powershell
git add src/qingpu_insight/static/valuation_form.js tests/js/valuation_form_contract.cjs src/qingpu_insight/templates/index.html src/qingpu_insight/static/app.js src/qingpu_insight/static/app.css tests/test_web.py
git commit -m "refactor(home): focus the valuation workflow"
```

---

### Task 6: Connected market results and eight-row recent table

**Files:**
- Create: `src/qingpu_insight/static/market_results.js`
- Create: `tests/js/market_results_contract.cjs`
- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/app.js:1-300`
- Modify: `src/qingpu_insight/static/app.css`

**Interfaces:**
- Produces:
  - `filterSummary(state): string[]`
  - `visibleRecent(items, expanded, collapsedLimit = 8): object[]`
  - `recentToggleLabel(total, expanded): string`
  - `loadSection(url, fetchImpl, onSuccess, onError): Promise<void>`
- Preserves: one `/api/transactions?...&limit=100` request per filter update.

- [ ] **Step 1: Write the failing market-result contract**

```javascript
const assert = require("node:assert/strict");
const ui = require("../../src/qingpu_insight/static/market_results.js");
const rows = Array.from({ length: 100 }, (_, index) => ({ id: index }));

assert.equal(ui.visibleRecent(rows, false).length, 8);
assert.equal(ui.visibleRecent(rows, true).length, 100);
assert.equal(ui.recentToggleLabel(100, false), "顯示更多成交（100）");
assert.equal(ui.recentToggleLabel(100, true), "收合近期成交");
assert.deepEqual(
  ui.filterSummary({
    transactionTypeLabel: "中古屋",
    stations: ["A17", "A18", "A19"],
    areaMin: "",
    areaMax: "",
  }),
  ["中古屋", "A17～A19", "全部坪數"]
);

async function testIndependentSections() {
  var successes = [];
  var failures = [];
  await Promise.all([
    ui.loadSection("/summary", async () => ({ ok: true, json: async () => ({ count: 1 }) }),
      (value) => successes.push(value), (error) => failures.push(error.message)),
    ui.loadSection("/trends", async () => ({ ok: false, status: 503 }),
      (value) => successes.push(value), (error) => failures.push(error.message)),
  ]);
  assert.deepEqual(successes, [{ count: 1 }]);
  assert.deepEqual(failures, ["request 503"]);
}

testIndependentSections().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
```

- [ ] **Step 2: Run the contract and verify module failure**

Run:

```powershell
node tests/js/market_results_contract.cjs
```

Expected: FAIL because `market_results.js` does not exist.

- [ ] **Step 3: Implement pure result-state functions**

The module must not access `document`, global `fetch`, Leaflet, or Chart.js. It only transforms passed values and arrays, and `loadSection` uses the injected `fetchImpl`.

- [ ] **Step 4: Add market-result wrapper markup**

Wrap status, KPI, dashboard grid, and recent transactions in:

```html
<section id="market-results" class="market-results" aria-label="市場分析結果">
  <div id="active-market-filters" class="active-market-filters"></div>
  <!-- existing result modules -->
</section>
```

Add a `修改篩選` button that scrolls to `.controls` and focuses `#transaction-type`.

- [ ] **Step 5: Integrate result state into `app.js`**

Maintain:

```javascript
var recentItems = [];
var recentExpanded = false;
```

Replace the current `Promise.all` coupling with three independent `loadSection` calls for summary, trends, and transactions. A failed section shows its own concise error and must not clear successful sections. On every successful transactions fetch, set `recentItems`, reset `recentExpanded = false`, update the sticky filter chips, and render eight rows. The toggle only changes `recentExpanded` and re-renders from `recentItems`; it must not call `fetch`.

Use Task 1 formatters for KPI, map popup, and recent table. Empty results render `目前條件下沒有成交資料`.

- [ ] **Step 6: Add sticky result-shell CSS**

Use `position: sticky` for `.active-market-filters`, with a top offset that does not cover browser content. Use a shared border/accent around KPI, map, trend, and recent table. Preserve two-column desktop and one-column narrow layouts.

- [ ] **Step 7: Run market contracts**

Run:

```powershell
node tests/js/market_results_contract.cjs
node tests/js/market_map_contract.mjs
.\.venv\Scripts\python.exe -m pytest tests/test_market_metrics.py tests/test_web.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```powershell
git add src/qingpu_insight/static/market_results.js tests/js/market_results_contract.cjs src/qingpu_insight/templates/index.html src/qingpu_insight/static/app.js src/qingpu_insight/static/app.css
git commit -m "feat(home): connect filters to market results"
```

---

### Task 7: Accessible recent-conversation dropdown

**Files:**
- Modify: `src/qingpu_insight/static/home_assistant.js:120-175`
- Modify: `src/qingpu_insight/static/app.css`
- Modify: `tests/js/home_assistant_contract.cjs`

**Interfaces:**
- Produces:
  - `truncateConversationTitle(value, maxLength = 28): string`
  - `renderRecentConversations(items): void`
- Uses existing `/api/conversations?limit=5`.

- [ ] **Step 1: Replace existing list expectations with failing dropdown expectations**

Assert the rendered root contains:

- one button with text `最近對話 3`
- `aria-expanded="false"`
- one hidden menu linked by `aria-controls`
- at most five links
- titles longer than 28 characters ending in `…`
- Taipei-formatted timestamps

Simulate button click, Escape, and outside click with the test DOM event harness; assert the menu closes and focus returns to the trigger after Escape.

- [ ] **Step 2: Run the contract and verify failure**

Run:

```powershell
node tests/js/home_assistant_contract.cjs
```

Expected: FAIL because current rendering uses a paragraph and list without disclosure state.

- [ ] **Step 3: Implement dropdown behavior**

Render nothing for an empty list. For non-empty results, create one pill button and one menu. Keep one document-level click handler and one keydown handler; remove them only if the module supports teardown. Use `hidden`, `aria-expanded`, `aria-controls`, and safe `textContent`.

- [ ] **Step 4: Add responsive styles**

Position the menu below the pill on desktop; use full container width on narrow screens. Limit title to one line with ellipsis and keep timestamps visible.

- [ ] **Step 5: Run assistant-home contracts**

Run:

```powershell
node tests/js/home_assistant_contract.cjs
node tests/js/job_polling_contract.cjs
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add src/qingpu_insight/static/home_assistant.js src/qingpu_insight/static/app.css tests/js/home_assistant_contract.cjs
git commit -m "feat(home): collapse recent conversations"
```

---

### Task 8: Documentation, regression gates, and browser acceptance

**Files:**
- Modify: `README.md`
- Verify: `src/qingpu_insight/static/models_admin.js`
- Verify: `tests/js/admin_contract.cjs`
- Verify: `tests/js/model_admin_contract.cjs`

**Interfaces:**
- No new interfaces.
- Confirms all tasks satisfy the approved spec.

- [ ] **Step 1: Update README**

Document:

- homepage order and single 591 AI entry
- report backend retained but homepage report form removed
- total prices displayed in 萬 and unit prices in 萬／坪
- confidence measures interval width, input range, and comparable quality
- 591 asking prices include negotiation room and are not transaction labels
- recent conversations and recent transactions are collapsed by default

Do not include API keys, local paths, raw data, or screenshots containing user data.

- [ ] **Step 2: Verify admin monetary formatting**

Confirm model admin already formats MAE as `萬元／坪`. Add assertions only if coverage is missing:

```javascript
assert.match(models.buildTrainingResultSummary(candidate).mae, /萬元／坪$/);
```

Do not convert MAPE or R² to monetary units. Downloaded JSON/Markdown reports remain exact.

- [ ] **Step 3: Run the full automated suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
$contracts = Get-ChildItem tests\js -File | Sort-Object Name
foreach ($contract in $contracts) {
  node $contract.FullName
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

Expected: all Python tests, Ruff, and every JavaScript contract pass.

- [ ] **Step 4: Restart the local Web process safely**

Resolve the exact PID listening on `127.0.0.1:5000`, confirm its command line contains `qingpu-web.exe`, stop only that PID, and start `.\.venv\Scripts\qingpu-web.exe` hidden. Never terminate unrelated Python or Chrome processes.

- [ ] **Step 5: Run browser acceptance on the homepage**

Verify:

1. H1 appears before the assistant starter.
2. The report form is absent.
3. Active filter chips update with market, station, dates, area, building type, and bedrooms.
4. The sticky context remains visible while scrolling through map, trend, and recent rows.
5. Recent rows show 8 initially; expand shows 100 without a network request; collapse returns to 8.
6. Recent-conversation pill opens, restores a conversation, closes on Escape/outside click, and does not overflow a narrow viewport.
7. Total and unit prices use 萬 formats without `$`.

- [ ] **Step 6: Run real valuation and 591 assistant acceptance**

Use a realistic A18 resale input and one supported 591 detail URL. Verify:

1. Similarity scores are stable and at least three typical close cases can clear 0.60.
2. Confidence is displayed in Chinese with explicit reasons.
3. Asking price does not reduce confidence.
4. Price-position chart and text agree.
5. Initial Rule summary has no more than six claims.
6. A Gemini 3.5 Flash-Lite question returns 3–4 concise evidence-backed points.
7. Fact IDs are hidden until `查看資料依據` is expanded.
8. 591 caveat states asking price is not final transaction price.

- [ ] **Step 7: Review git scope and secrets**

Run:

```powershell
git diff --check
git status --short
git diff --stat
```

Scan changed text files for `AQ.` and `AIza` key prefixes without printing matching secret values. Do not stage `.env`, `instance/secrets.env`, browser profiles, raw HTML, temporary screenshots, datasets, or unrelated runtime output.

- [ ] **Step 8: Commit documentation and final acceptance updates**

```powershell
git add README.md tests/js/admin_contract.cjs tests/js/model_admin_contract.cjs
git commit -m "docs: explain valuation confidence and display"
```

If the two admin contract files did not require changes, stage only `README.md`.

---

## Final Review Checklist

- [ ] Every price visible in the homepage and assistant uses 萬 or 萬／坪.
- [ ] No API, database, artifact, benchmark, or report numeric contract was reformatted.
- [ ] Similarity is absolute, stable across batches, missing-safe, and bounded to `0..1`.
- [ ] Confidence thresholds remain 30%, 3 cases, and 0.60.
- [ ] Asking price affects price-position interpretation, not model confidence.
- [ ] AI text remains fact-ID validated even though IDs are visually collapsed.
- [ ] Report backend remains tested and callable after homepage form removal.
- [ ] Map and chart positions are preserved inside the connected result shell.
- [ ] Recent table fetches 100 once and renders 8 by default.
- [ ] Recent conversations render at most 5 in an accessible dropdown.
- [ ] Full automated tests and real browser acceptance pass before branch completion.
