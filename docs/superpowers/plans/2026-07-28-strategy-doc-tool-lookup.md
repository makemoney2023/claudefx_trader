# Strategy Doc Tool-Lookup (Replay) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop dumping ~33k tokens of strategy markdown into every Claude replay analysis call; keep docs available via an on-demand `lookup_strategy_doc` tool while finishing with `submit_trade_analysis`.

**Architecture:** Replay analysis uses a slim system stack (identity + `ANALYSIS_RULES` + doc index + tone) and a small multi-turn tool loop (max 2 doc lookups, then trade tool). Live analysis keeps the current full `strategy_context` path until a later flip. Truncation hardening (16k budget + retry on any `max_tokens`) ships in the same change set.

**Tech Stack:** Python 3, Anthropic Messages API (Opus 5, adaptive thinking, tools, prompt cache), pytest/asyncio, existing `ContextBuilder` + `ClaudeClient` + `ClaudeReplayBacktester`.

## Global Constraints

- Replay-only for the new tool loop; live `analyze_chart_async` default path unchanged.
- Never include `website_documentation` or `phase2_100k_plan` in lookup allowlists.
- Max **2** `lookup_strategy_doc` calls per analysis; then force completion via `submit_trade_analysis`.
- Adaptive thinking requires `tool_choice=auto` on the primary turn; forced tool only on thinking-disabled truncation retry.
- Doc lookup responses capped (~12k chars / ~3k tokens) to avoid blowing the next turn.
- TDD: failing test first for each behavior; no new root-level files outside `Trading/`.
- Do not commit `.env.local` or secrets.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `trading_bot/llm/context_builder.py` | Doc allowlist, index text, safe lookup by name/query, junk exclusion |
| `trading_bot/llm/claude_client.py` | `LOOKUP_STRATEGY_DOC_TOOL`, slim system messages, multi-turn analysis loop for `strategy_mode="replay"`, truncation retry |
| `trading_bot/backtesting/replay.py` | Pass slim/empty strategy context + `strategy_mode="replay"` |
| `trading_bot/config.py` | Align `get_claude_config()["max_tokens"]` with 16k analysis budget |
| `tests/test_context_builder_lookup.py` | Unit tests for index/lookup/allowlist |
| `tests/test_gap_fixes.py` (extend) | Truncation retry + replay tool-loop / slim system stack |
| `trading_bot/docs/website_documentation.md` | One-line note on replay tool-lookup |

No new markdown strategy docs. No RAG/embeddings in this plan.

---

### Task 0: Land truncation hardening already staged locally

**Files:**
- Modify: `trading_bot/llm/claude_client.py` (max_tokens 16k, retry on any `max_tokens`)
- Modify: `trading_bot/config.py`, `trading_bot/main.py`, `trading_bot/docs/website_documentation.md`
- Modify: `tests/test_gap_fixes.py` (Opus5Everywhere expectations)

**Interfaces:**
- Produces: analysis default `max_tokens=16000`; on `stop_reason=="max_tokens"` always one thinking-disabled forced-`submit_trade_analysis` retry

- [ ] **Step 1: Confirm staged tests fail on clean HEAD without the staged diff** (or run current suite if already applied)

Run: `./venv/bin/python -m pytest tests/test_gap_fixes.py::TestOpus5Everywhere -q`

- [ ] **Step 2: Ensure implementation matches plan** (16k budget; retry ignores partial tool blocks)

- [ ] **Step 3: Commit**

```bash
git add trading_bot/llm/claude_client.py trading_bot/config.py trading_bot/main.py \
  trading_bot/docs/website_documentation.md tests/test_gap_fixes.py
git commit -m "$(cat <<'EOF'
Cap analysis at 16k and always retry on max_tokens truncation.

Partial tool_use blocks were skipping the thinking-disabled retry and wasting ~$2 per snapshot.
EOF
)"
```

---

### Task 1: ContextBuilder allowlist, index, and lookup helpers

**Files:**
- Modify: `trading_bot/llm/context_builder.py`
- Create: `tests/test_context_builder_lookup.py`

**Interfaces:**
- Produces:
  - `STRATEGY_DOC_ALLOWLIST: tuple[str, ...]` — allowed stems
  - `STRATEGY_DOC_BLOCKLIST: tuple[str, ...]` — at least `website_documentation`, `phase2_100k_plan`
  - `ContextBuilder.get_strategy_doc_index() -> str` — name + one-line summary per allowlisted doc that exists
  - `ContextBuilder.lookup_strategy_doc(doc_name: str | None = None, query: str | None = None, max_chars: int = 12000) -> dict`  
    Returns `{"doc_name": str, "content": str, "truncated": bool}` or `{"error": str}`
  - `ContextBuilder.get_ict_context()` — keep behavior for live, but **exclude blocklist** from the “remaining documents” loop so junk never ships

- [ ] **Step 1: Write failing tests**

```python
# tests/test_context_builder_lookup.py
def test_index_excludes_blocklisted_docs():
    cb = ContextBuilder()
    index = cb.get_strategy_doc_index()
    assert "website_documentation" not in index
    assert "phase2_100k_plan" not in index
    assert "market_structure" in index  # if file present

def test_lookup_by_name_returns_content():
    cb = ContextBuilder()
    result = cb.lookup_strategy_doc(doc_name="fair_value_gap")
    assert "error" not in result
    assert "FVG" in result["content"] or len(result["content"]) > 100

def test_lookup_rejects_blocklisted_name():
    cb = ContextBuilder()
    result = cb.lookup_strategy_doc(doc_name="website_documentation")
    assert "error" in result

def test_full_ict_context_excludes_blocklist():
    cb = ContextBuilder()
    full = cb.get_ict_context()
    assert "### Website Documentation" not in full
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `./venv/bin/python -m pytest tests/test_context_builder_lookup.py -q`

- [ ] **Step 3: Implement helpers on `ContextBuilder`**

Allowlist (initial):
`ict_strategy`, `market_structure`, `fair_value_gap`, `order_blocks`, `liquidity_concepts`, `optimal_trade_entry`, `kill_zones`, `swing_validation`, `precious_metals`, `risk_management`, `amd_cycle`, `volume_concepts`

Index line format: `- fair_value_gap: Fair Value Gaps — formation and trading`

Lookup: exact allowlisted stem first; else case-insensitive substring match on allowlisted names only; truncate with `truncated=True`.

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add trading_bot/llm/context_builder.py tests/test_context_builder_lookup.py
git commit -m "$(cat <<'EOF'
Add strategy doc index/lookup helpers and exclude junk docs from ICT context.
EOF
)"
```

---

### Task 2: Define `lookup_strategy_doc` tool schema

**Files:**
- Modify: `trading_bot/llm/claude_client.py` (near `TRADE_SIGNAL_TOOL`)
- Modify: `tests/test_gap_fixes.py` (or new test in `tests/test_context_builder_lookup.py`)

**Interfaces:**
- Produces: `LOOKUP_STRATEGY_DOC_TOOL: dict` with `strict: True`, `additionalProperties: False`
- Input properties: `doc_name` (`string|null`), `query` (`string|null`); require at least one via description (strict schema cannot easily express XOR — validate in handler)
- Required array: both keys present (nullable), matching existing strict-tool style

- [ ] **Step 1: Write failing test** asserting tool name, strict flag, no numeric min/max, required fields

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Add `LOOKUP_STRATEGY_DOC_TOOL` constant**

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
Add strict lookup_strategy_doc tool schema for on-demand ICT docs.
EOF
)"
```

---

### Task 3: Slim system messages for replay mode

**Files:**
- Modify: `trading_bot/llm/claude_client.py` — `_build_system_messages`
- Test: `tests/test_gap_fixes.py`

**Interfaces:**
- Consumes: `ContextBuilder.get_strategy_doc_index()`
- Produces: `_build_system_messages(strategy_context: str, *, strategy_mode: str = "full") -> list`
  - `full`: current behavior (identity + ANALYSIS_RULES + strategy_context + tone), but callers should already exclude junk via Task 1
  - `replay`: identity + ANALYSIS_RULES (cached) + **doc index block** (cached) + tone — **no full strategy markdown**

- [ ] **Step 1: Failing test**

```python
def test_replay_system_messages_use_index_not_full_docs():
    client = ...
    msgs = client._build_system_messages("HUGE STRATEGY DUMP", strategy_mode="replay")
    texts = [m["text"] for m in msgs]
    assert all("HUGE STRATEGY DUMP" not in t for t in texts)
    assert any("lookup_strategy_doc" in t or "Available strategy documents" in t for t in texts)
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement `strategy_mode` branch; inject short instruction: “Call lookup_strategy_doc for methodology details (max 2), then submit_trade_analysis.”**

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
Use a slim cached doc index in replay analysis system prompts.
EOF
)"
```

---

### Task 4: Multi-turn tool loop in `analyze_chart_async` for replay

**Files:**
- Modify: `trading_bot/llm/claude_client.py` — `analyze_chart_async`
- Test: `tests/test_gap_fixes.py::TestOpus5Everywhere`

**Interfaces:**
- Consumes: `LOOKUP_STRATEGY_DOC_TOOL`, `TRADE_SIGNAL_TOOL`, `ContextBuilder.lookup_strategy_doc`
- Produces: `analyze_chart_async(..., strategy_mode: str = "full", context_builder: ContextBuilder | None = None)`
- Replay loop behavior:
  1. Initial request with `tools=[LOOKUP_STRATEGY_DOC_TOOL, TRADE_SIGNAL_TOOL]`, `tool_choice=auto`, slim system messages
  2. While `stop_reason == "tool_use"` and tool is `lookup_strategy_doc` and lookups < 2: append assistant content + `tool_result`, continue
  3. If third lookup attempted: return tool_result error `"lookup limit reached; call submit_trade_analysis"`
  4. On `submit_trade_analysis`: validate/parse as today and return
  5. On `max_tokens` at any turn: existing thinking-disabled retry with **only** `TRADE_SIGNAL_TOOL` forced (no lookup tool)
  6. Cap loop iterations at 4 wall turns total (defense in depth)

- [ ] **Step 1: Write failing async test with mocked `_async_messages_create`**

Sequence:
1. First response: `lookup_strategy_doc` tool_use (`doc_name=fair_value_gap`)
2. Second response: `submit_trade_analysis` with `no_trade`
Assert: two API calls; second messages include a `tool_result`; final direction `no_trade`.

- [ ] **Step 2: Run — FAIL** (single-shot path ignores lookup)

- [ ] **Step 3: Implement loop only when `strategy_mode == "replay"`; `full` keeps single-shot + full strategy_context

Wire a default `ContextBuilder()` when `strategy_mode=="replay"` and none passed.

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
Add replay multi-turn loop for strategy doc lookup before trade submission.
EOF
)"
```

---

### Task 5: Wire Claude replay to `strategy_mode="replay"`

**Files:**
- Modify: `trading_bot/backtesting/replay.py` (~Claude call site)
- Test: extend `tests/test_replay_integration.py` or add a focused unit test that mocks `analyze_chart_async` and asserts kwargs

**Interfaces:**
- Produces: replay calls  
  `analyze_chart_async(..., strategy_context="", strategy_mode="replay", context_builder=context_builder)`  
  (empty string OK; slim path ignores full dump)

- [ ] **Step 1: Failing test** — mock client, run one snapshot path or assert call kwargs from a thin wrapper test

- [ ] **Step 2: Implement replay wiring; keep heartbeat logs

- [ ] **Step 3: Run targeted tests PASS**

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
Point Claude replay at on-demand strategy doc lookup mode.
EOF
)"
```

---

### Task 6: Docs + smoke checklist

**Files:**
- Modify: `trading_bot/docs/website_documentation.md` (prompt caching / replay note)
- Optional: one paragraph in `docs/WINDOWS_VPS_SETUP.md` only if replay ops change (skip if unchanged)

- [ ] **Step 1: Document** that replay uses tool-lookup; live still embeds strategy context; expected `[USAGE]` cache block much smaller

- [ ] **Step 2: Run full related suite**

```bash
./venv/bin/python -m pytest \
  tests/test_context_builder_lookup.py \
  tests/test_gap_fixes.py::TestOpus5Everywhere \
  tests/test_signal_normalizer.py \
  -q
```

- [ ] **Step 3: Commit + push** (only when user asks to push)

- [ ] **Step 4: VPS smoke**
  1. `git pull` + restart
  2. Short Claude replay (2–3 days, 4h interval)
  3. Confirm logs show optional `lookup_strategy_doc` then `submit_trade_analysis`
  4. Confirm `[USAGE]` `cache_write`/`cache_read` ≪ 59k (target ~5–15k)
  5. Confirm rare/no 16k truncation; if truncated, see retry warning then cheap completion

---

## Rollback

- Set replay back to `strategy_mode="full"` and pass `get_ict_context()` again.
- Or revert the replay wiring commit only; ContextBuilder blocklist exclusion can stay (still beneficial).

## Out of Scope

- Live bot switching to tool-lookup
- RAG embeddings / vector DB
- Skipping Asian session
- Changing ICT methodology content of the `.md` files themselves
