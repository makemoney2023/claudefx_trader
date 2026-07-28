"""Tests for strategy doc index/lookup helpers on ContextBuilder."""

from trading_bot.llm.context_builder import ContextBuilder


def test_index_excludes_blocklisted_docs():
    cb = ContextBuilder()
    index = cb.get_strategy_doc_index()
    assert "website_documentation" not in index
    assert "phase2_100k_plan" not in index
    assert "market_structure" in index


def test_lookup_by_name_returns_content():
    cb = ContextBuilder()
    result = cb.lookup_strategy_doc(doc_name="fair_value_gap")
    assert "error" not in result
    assert "FVG" in result["content"] or len(result["content"]) > 100
    assert result["doc_name"] == "fair_value_gap"
    assert "truncated" in result


def test_lookup_rejects_blocklisted_name():
    cb = ContextBuilder()
    result = cb.lookup_strategy_doc(doc_name="website_documentation")
    assert "error" in result


def test_full_ict_context_excludes_blocklist():
    cb = ContextBuilder()
    full = cb.get_ict_context()
    assert "### Website Documentation" not in full
    assert "### Phase2 100K Plan" not in full


def test_lookup_truncates_long_docs():
    cb = ContextBuilder()
    result = cb.lookup_strategy_doc(doc_name="risk_management", max_chars=200)
    assert "error" not in result
    assert len(result["content"]) <= 200
    assert result["truncated"] is True
