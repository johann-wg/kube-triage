"""LLM 判定のテスト. API は呼ばず、リポジトリにコミットしたキャッシュだけを使う."""

from pathlib import Path

import pytest
from conftest import SCENARIOS

from kube_triage.bundle import Bundle
from kube_triage.evaluate import evaluate
from kube_triage.llm import HybridDiagnoser, LLMDiagnoser, Usage, compact_bundle, needs_llm
from kube_triage.models import Category, Finding
from kube_triage.rules import diagnose

ROOT = SCENARIOS.parent
HOLDOUT = ROOT / "scenarios-holdout"
CACHE = ROOT / "llm-cache"
MODEL = "claude-haiku-5-5"


def offline(model: str = MODEL) -> LLMDiagnoser:
    return LLMDiagnoser(model=model, cache_dir=CACHE, offline=True)


def test_compact_bundle_drops_noise_and_truncates_logs(load):
    bundle = load("13_crash_missing_env")
    bundle.pod["metadata"]["managedFields"] = [{"manager": "kubelet"}]
    bundle.logs["app"] = "\n".join(f"line {i}" for i in range(100))
    compact = compact_bundle(bundle, log_lines=10)
    assert "managedFields" not in str(compact)
    assert compact["logs"]["app"].splitlines() == [f"line {i}" for i in range(90, 100)]
    assert compact["status"]["containerStatuses"][0]["restartCount"] == 6


def test_needs_llm_only_when_rules_are_inconclusive():
    assert needs_llm([Finding(Category.UNKNOWN, "", 0.0)])
    assert needs_llm([Finding(Category.CRASH_LOOP, "", 0.6, subcause=None)])
    assert not needs_llm([Finding(Category.CRASH_LOOP, "", 0.8, subcause="permission")])
    assert not needs_llm([Finding(Category.OOM_KILLED, "", 0.95)])


def test_offline_mode_raises_without_cache(tmp_path, load):
    llm = LLMDiagnoser(model=MODEL, cache_dir=tmp_path, offline=True)
    with pytest.raises(LookupError):
        llm.diagnose(load("01_healthy"))


def test_cached_response_is_replayed_without_api(load):
    llm = offline()
    main = llm.diagnose(load("17_crash_invalid_config"))[0]
    assert (main.category, main.subcause) == (Category.CRASH_LOOP, "invalid_config")
    assert llm.usage.calls == 0 and llm.usage.cache_hits == 1
    assert llm.usage.cost_usd == 0
    assert llm.usage.estimated_cost_usd > 0


def test_hybrid_keeps_rule_result_when_rules_are_confident(load):
    hybrid = HybridDiagnoser(LLMDiagnoser(model=MODEL, cache_dir=Path("missing"), offline=True))
    # ルールで確定できるため LLM (キャッシュなし) を呼ばない
    assert hybrid.diagnose(load("02_oom_killed"))[0].category == Category.OOM_KILLED


def test_hybrid_improves_holdout_over_rules():
    rules = evaluate(HOLDOUT, diagnose)
    hybrid = evaluate(HOLDOUT, HybridDiagnoser(offline()).diagnose)
    assert sum(r.subcause_ok for r in hybrid) > sum(r.subcause_ok for r in rules)


def test_usage_cost_uses_model_prices():
    usage = Usage()
    usage.add("claude-haiku-5-5", 1_000_000, 1_000_000)
    usage.add("claude-opus-5-5", 1_000_000, 0, cached=True)
    assert usage.cost_usd == pytest.approx(0.10 + 0.50)
    assert usage.estimated_cost_usd == pytest.approx(0.60 + 4.00)


def test_bundle_from_holdout_is_valid():
    for path in HOLDOUT.glob("*.json"):
        assert Bundle.load(path).name
