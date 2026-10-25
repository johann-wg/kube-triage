from kube_triage.bundle import Bundle
from kube_triage.models import Category
from kube_triage.rules import _match_texts, diagnose


def _main(bundle: Bundle):
    return diagnose(bundle)[0]


def test_healthy(load):
    assert _main(load("01_healthy")).category == Category.HEALTHY


def test_oom_takes_priority_over_crash_loop(load):
    findings = diagnose(load("02_oom_killed"))
    assert findings[0].category == Category.OOM_KILLED
    assert Category.CRASH_LOOP in [f.category for f in findings[1:]]
    assert any("memory=128Mi" in e.text for e in findings[0].evidence)


def test_liveness_takes_priority_over_crash_loop(load):
    # exitCode 137 でも OOMKilled ではなく Liveness Probe による kill
    main = _main(load("11_liveness_probe"))
    assert (main.category, main.subcause) == (Category.PROBE_FAILURE, "liveness")


def test_image_pull_subcause_from_event_message(load):
    assert _main(load("03_image_not_found")).subcause == "image_not_found"
    assert _main(load("04_image_registry_auth")).subcause == "registry_auth"


def test_unschedulable_subcauses(load):
    expected = {
        "07_insufficient_memory": "insufficient_resources",
        "08_pvc_unbound": "pvc_unbound",
        "09_taint": "taint",
        "10_node_selector": "node_selector",
    }
    for name, subcause in expected.items():
        main = _main(load(name))
        assert (main.category, main.subcause) == (Category.UNSCHEDULABLE, subcause), name


def test_crash_subcause_from_exit_code_without_logs(load):
    main = _main(load("15_crash_bad_command"))
    assert main.subcause == "bad_command"
    assert not any(e.source.startswith("logs/") for e in main.evidence)


def test_crash_evidence_contains_matched_log_line(load):
    main = _main(load("13_crash_missing_env"))
    assert main.subcause == "missing_env"
    assert any("KeyError: 'DATABASE_URL'" in e.text for e in main.evidence)


def test_match_texts_prefers_specific_pattern():
    log = "connecting to db\nKeyError: 'DB_HOST'\nconnection refused\n"
    assert _match_texts([("logs/app", log)]) == ("missing_env", "logs/app", "KeyError: 'DB_HOST'")


def test_match_texts_uses_termination_message_when_logs_are_empty():
    # 実クラスタでは存在しないコマンドはログが空で、終了メッセージにだけ原因が残る
    texts = [
        ("logs/app", ""),
        ("lastState.terminated.message", 'exec: "pythn": executable file not found in $PATH'),
    ]
    assert _match_texts(texts)[:2] == ("bad_command", "lastState.terminated.message")


def test_init_container_failure_is_detected(load):
    main = _main(load("18_init_container_failure"))
    assert (main.category, main.subcause) == (Category.CRASH_LOOP, "dependency_unreachable")
    assert "migrate" in main.title


def test_unknown_when_nothing_matches():
    bundle = Bundle(pod={"metadata": {"name": "x"}, "status": {"phase": "Pending"}})
    main = _main(bundle)
    assert main.category == Category.UNKNOWN
    assert main.confidence == 0.0


def test_events_of_other_pods_are_ignored(load):
    bundle = load("12_readiness_probe")
    for e in bundle.events:
        e["involvedObject"]["name"] = "another-pod"
    assert _main(bundle).category != Category.PROBE_FAILURE
