"""Property-based tests for SyncForge using Hypothesis."""

from hypothesis import given, strategies as st
from syncforge.core.path_validator import PathValidator
from syncforge.rules.rule_engine import RuleEngine, Rule, RuleType


@given(st.lists(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_/-", min_size=1, max_size=30), min_size=1, max_size=20))
def test_hypothesis_path_normalization(paths):
    """Path normalization must never crash or produce backslashes."""
    for p in paths:
        normalized = PathValidator.normalize_rel_path(p)
        assert "\\" not in normalized
        assert not normalized.startswith("/")


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz.", min_size=1, max_size=15), st.integers(min_value=0, max_value=10_000_000))
def test_hypothesis_rule_engine_size_and_globs(filename, size):
    """Rule engine evaluates without crashing for any valid file name and size."""
    rule1 = Rule(id="r1", rule_type=RuleType.MAX_SIZE, pattern="5000000")
    rule2 = Rule(id="r2", rule_type=RuleType.EXCLUDE_GLOB, pattern="*.tmp")
    engine = RuleEngine([rule1, rule2])

    included, reason = engine.should_include(filename, size)
    assert isinstance(included, bool)
    assert isinstance(reason, str)
