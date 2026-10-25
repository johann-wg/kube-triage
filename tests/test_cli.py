import json

from conftest import SCENARIOS

from kube_triage.cli import main
from kube_triage.evaluate import evaluate

# 現行ルールで取りこぼす既知ケース. 改善したらここから外す.
KNOWN_MISSES = {"17_crash_invalid_config", "18_init_container_failure"}


def test_analyze_text(capsys):
    assert main(["analyze", str(SCENARIOS / "02_oom_killed.json")]) == 0
    out = capsys.readouterr().out
    assert "判定: OOM_KILLED" in out
    assert "根拠:" in out


def test_analyze_json(capsys):
    assert main(["analyze", str(SCENARIOS / "05_missing_configmap.json"), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["findings"][0]["category"] == "CONFIG_ERROR"
    assert data["findings"][0]["subcause"] == "missing_configmap"


def test_eval_report_and_threshold(tmp_path, capsys):
    report = tmp_path / "result.md"
    assert main(["eval", str(SCENARIOS), "--report", str(report), "--min-accuracy", "0.9"]) == 0
    assert "| カテゴリ |" in report.read_text(encoding="utf-8")
    assert main(["eval", str(SCENARIOS), "--min-accuracy", "1.0"]) == 1


def test_only_known_misses_fail():
    misses = {r.scenario for r in evaluate(SCENARIOS) if not r.subcause_ok}
    assert misses == KNOWN_MISSES
