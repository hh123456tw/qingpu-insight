# AI 物件分析價格摘要 Implementation Plan

> **For agentic workers:** Execute these four tasks in order. The user explicitly waived TDD; add focused regression coverage after implementation and run the existing verification gates.

**Goal:** Make each assistant reply show a deterministic, readable price comparison before the AI narrative, with localized evidence details.

**Architecture:** Add a pure presentation projector that derives message display data from the immutable evidence revision. Decorate message API responses with that projection, then render it in the existing assistant card without changing persistence.

**Tech Stack:** Python 3.11, Flask, vanilla JavaScript, CSS, pytest, Node contract tests.

## Global Constraints

- Only change the right-side assistant reply card.
- Keep `查看資料依據` closed by default.
- Do not add a database migration or retrain a model.
- Price position uses interval boundaries, not the point estimate.
- Gemini remains explanatory; deterministic code owns displayed arithmetic.
- Preserve old conversations and API responses that lack the new fields.

---

### Task 1: Evidence presentation projector

**Files:**
- Create: `src/qingpu_insight/conversation_presentation.py`
- Modify: `tests/test_conversation_presentation.py`

- [ ] Implement `project_price_summary(evidence_pack)` with validated positive values, `low <= point <= high`, boundary-based gap and one-decimal percentage.
- [ ] Implement `project_citation_details(evidence_pack, citation_ids)` using evidence labels and values, with stable fallbacks.
- [ ] Add focused tests for below/inside/above/inconsistent/missing data and localized citation output.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_conversation_presentation.py -q`.

### Task 2: Message API projection

**Files:**
- Modify: `src/qingpu_insight/conversation_web.py`
- Modify: `tests/test_conversation_web.py`

- [ ] Load each distinct evidence revision at most once per message page.
- [ ] Add nullable `price_summary` and `citation_details` to assistant message JSON.
- [ ] Preserve user messages, missing evidence, pagination and all existing fields.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_conversation_web.py -q`.

### Task 3: Assistant card and provider wording

**Files:**
- Modify: `src/qingpu_insight/static/assistant.js`
- Modify: `src/qingpu_insight/static/assistant.css`
- Modify: `src/qingpu_insight/conversation_providers.py`
- Modify: `tests/js/assistant_contract.cjs`
- Modify: `tests/test_conversation_providers.py`

- [ ] Render the deterministic summary before AI content, including the 591 asking-price disclaimer and explicit status text.
- [ ] Render citation label, value and source while retaining raw-ID fallback and native closed `<details>`.
- [ ] Tell generated providers not to calculate or state asking-price gap percentages; remove the gap percentage from Rule summary priority.
- [ ] Verify responsive and keyboard-safe markup with the JS contract and provider tests.

### Task 4: Verification and delivery

**Files:**
- Modify: `README.md` only if the visible workflow description is now inaccurate.

- [ ] Run focused Python tests, all Node contracts, Ruff and `git diff --check`.
- [ ] Restart the local web process and inspect an existing assistant conversation in the browser/API.
- [ ] Commit the implementation atomically and push `main`.

