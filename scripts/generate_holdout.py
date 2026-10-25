"""評価専用シナリオ (holdout) を scenarios-holdout/ に生成する.

ルールとプロンプトの修正には使わない。正解ラベルは判定を実行する前に決めて固定した。
既存シナリオと文言・状況を変え、ルールが知らない表現や紛らわしいケースを含める。

    python scripts/generate_holdout.py
"""

from __future__ import annotations

import json
from pathlib import Path

from generate_scenarios import (
    IMAGE,
    condition,
    container,
    event,
    pod,
    scenario,
    status,
    terminated,
    waiting,
)

OUT = Path(__file__).resolve().parent.parent / "scenarios-holdout"


def crash(name: str, exit_code: int, restarts: int = 5):
    return pod(
        name,
        statuses=[
            status(
                restarts=restarts,
                state=waiting("CrashLoopBackOff"),
                last=terminated("Error", exit_code),
            )
        ],
    )


def build() -> dict[str, dict]:
    s: dict[str, dict] = {}

    s["h01_go_required_config"] = scenario(
        "Go アプリ. 必須の環境変数が空で panic (英語の別表現)",
        "CRASH_LOOP",
        "missing_env",
        crash("billing-api", 2),
        logs={
            "app": "2026/10/26 09:00:01 starting billing-api\n"
            "panic: required setting DB_DSN is empty; set it via environment\n"
            "goroutine 1 [running]:\nmain.main()\n\t/src/main.go:42 +0x1a5\n"
        },
    )
    s["h02_java_unknown_host"] = scenario(
        "Java アプリ. 接続先 Service 名の DNS 解決失敗",
        "CRASH_LOOP",
        "dependency_unreachable",
        crash("order-service", 1),
        logs={
            "app": "INFO  Starting OrderServiceApplication\n"
            "ERROR HikariPool-1 - Exception during pool initialization.\n"
            "java.net.UnknownHostException: postgres-primary.shop.svc.cluster.local\n"
            "\tat java.base/java.net.InetAddress.getAllByName0(InetAddress.java:1631)\n"
        },
    )
    s["h03_dial_timeout"] = scenario(
        "接続先への TCP 接続がタイムアウト",
        "CRASH_LOOP",
        "dependency_unreachable",
        crash("inventory-worker", 1),
        logs={
            "app": 'level=error msg="db init failed" err="dial tcp 10.96.14.7:5432: i/o timeout"\n'
        },
    )
    s["h04_yaml_parse_error"] = scenario(
        "設定ファイル (ConfigMap マウント) の YAML 構文エラー",
        "CRASH_LOOP",
        "invalid_config",
        crash("gateway", 1),
        logs={
            "app": "loading /etc/gateway/routes.yaml\n"
            "Error: failed to parse config: yaml: line 12: did not find expected key\n"
        },
    )
    s["h05_readonly_fs"] = scenario(
        "readOnlyRootFilesystem によりキャッシュディレクトリへ書き込めない",
        "CRASH_LOOP",
        "permission",
        crash("thumbnailer", 1),
        logs={
            "app": "Traceback (most recent call last):\n"
            '  File "/app/worker.py", line 10, in <module>\n'
            "    os.makedirs(CACHE_DIR, exist_ok=True)\n"
            "OSError: [Errno 30] Read-only file system: '/app/cache'\n"
        },
    )
    s["h06_script_not_found"] = scenario(
        "起動スクリプトのパス誤り (終了コード 127)",
        "CRASH_LOOP",
        "bad_command",
        crash("legacy-batch", 127),
        logs={"app": "/bin/sh: 1: ./bin/start.sh: not found\n"},
    )
    s["h07_image_x509"] = scenario(
        "社内レジストリの証明書エラー. 定義済みの原因 (存在しない / 認証) のどちらでもない",
        "IMAGE_PULL",
        None,
        pod("api-internal", phase="Pending", statuses=[status(state=waiting("ImagePullBackOff"))]),
        [
            event(
                "api-internal",
                "Failed",
                f'Failed to pull image "{IMAGE}": rpc error: code = Unknown desc = failed to pull '
                "and unpack image: failed to resolve reference: failed to do request: "
                "Head https://registry.example.com/v2/shop/api/manifests/1.4.2: "
                "tls: failed to verify certificate: x509: certificate signed by unknown authority",
                count=5,
            )
        ],
    )
    s["h08_init_oom"] = scenario(
        "initContainer (データ展開) がメモリ上限を超えて OOMKilled",
        "OOM_KILLED",
        None,
        {
            **pod("search-indexer", phase="Pending"),
            "status": {
                "phase": "Pending",
                "conditions": [
                    condition("PodScheduled", "True"),
                    condition("Initialized", "False", "ContainersNotInitialized"),
                ],
                "initContainerStatuses": [
                    {
                        "name": "load-index",
                        "ready": False,
                        "restartCount": 3,
                        "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                        "lastState": {"terminated": terminated("OOMKilled", 137)},
                    }
                ],
                "containerStatuses": [status(state=waiting("PodInitializing"))],
            },
        },
    )
    s["h09_liveness_timeout"] = scenario(
        "高負荷で Liveness Probe がタイムアウト",
        "PROBE_FAILURE",
        "liveness",
        crash("report-api", 137, restarts=3),
        [
            event(
                "report-api",
                "Unhealthy",
                'Liveness probe failed: Get "http://10.128.4.21:8080/actuator/health": '
                "context deadline exceeded (Client.Timeout exceeded while awaiting headers)",
                count=9,
            ),
            event(
                "report-api",
                "Killing",
                "Container app failed liveness probe, will be restarted",
                type_="Normal",
                count=3,
            ),
        ],
        {"app": "INFO generating monthly report batch=2026-09 rows=1830212\n"},
    )
    s["h10_recovered_restart"] = scenario(
        "過去に 1 回だけ異常終了したが、その後は Ready で稼働中 (現在は正常)",
        "HEALTHY",
        None,
        pod(
            "notification-api",
            statuses=[
                status(
                    ready=True,
                    restarts=1,
                    state={"running": {"startedAt": "2026-10-19T03:12:00Z"}},
                    last=terminated("Error", 1),
                )
            ],
            conditions=[condition("PodScheduled", "True"), condition("Ready", "True")],
        ),
        logs={"app": "INFO listening on :8080\nINFO health check ok\n"},
    )
    s["h11_secret_missing_key"] = scenario(
        "Secret は存在するがキー名が違う",
        "CONFIG_ERROR",
        "missing_key",
        pod(
            "payment-api",
            phase="Pending",
            statuses=[
                status(
                    state=waiting(
                        "CreateContainerConfigError",
                        "couldn't find key db-password in Secret shop/db-credentials",
                    )
                )
            ],
        ),
    )
    s["h12_module_not_found"] = scenario(
        "イメージに依存パッケージが入っていない. 定義済みのどの原因にも当てはまらない",
        "CRASH_LOOP",
        None,
        crash("ml-scorer", 1),
        logs={
            "app": "Traceback (most recent call last):\n"
            '  File "/app/serve.py", line 3, in <module>\n'
            "    import fastapi\n"
            "ModuleNotFoundError: No module named 'fastapi'\n"
        },
    )
    s["h13_gpu_insufficient"] = scenario(
        "GPU を要求したが GPU ノードがない",
        "UNSCHEDULABLE",
        "insufficient_resources",
        pod(
            "llm-inference",
            phase="Pending",
            containers=[container(**{"resources": {"limits": {"nvidia.com/gpu": "1"}}})],
            conditions=[
                condition(
                    "PodScheduled",
                    "False",
                    "Unschedulable",
                    "0/4 nodes are available: 4 Insufficient nvidia.com/gpu. preemption: 0/4 "
                    "nodes are available: 4 No preemption victims found for incoming pod.",
                )
            ],
        ),
    )
    s["h14_oom_java_heap"] = scenario(
        "JVM ヒープ不足でアプリ自身が終了. コンテナの OOMKilled ではない (exitCode 1)",
        "CRASH_LOOP",
        None,
        crash("analytics-batch", 1),
        logs={
            "app": "INFO loading 12000000 records\n"
            'Exception in thread "main" java.lang.OutOfMemoryError: Java heap space\n'
            "\tat java.base/java.util.Arrays.copyOf(Arrays.java:3512)\n"
        },
    )
    return s


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    scenarios = build()
    for name, data in scenarios.items():
        (OUT / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"generated {len(scenarios)} holdout scenarios in {OUT}")


if __name__ == "__main__":
    main()
