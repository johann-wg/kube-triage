"""評価用シナリオ (合成データ) を scenarios/ に生成する.

kubectl get pod -o json / get events -o json の構造を模した最小限のデータ。
実クラスタ (kind) で再現・収集したデータに順次置き換えていく前提。

    python scripts/generate_scenarios.py
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "scenarios"
NS = "shop"
IMAGE = "registry.example.com/shop/api:1.4.2"


def container(name="app", image=IMAGE, mem_limit="256Mi", **extra):
    spec = {
        "name": name,
        "image": image,
        "resources": {"limits": {"memory": mem_limit, "cpu": "500m"}},
    }
    spec.update(extra)
    return spec


def status(name="app", image=IMAGE, ready=False, restarts=0, state=None, last=None):
    cs = {
        "name": name,
        "image": image,
        "ready": ready,
        "restartCount": restarts,
        "state": state or {"running": {"startedAt": "2026-10-25T09:00:00Z"}},
        "lastState": {"terminated": last} if last else {},
    }
    return cs


def terminated(reason, exit_code):
    return {"reason": reason, "exitCode": exit_code, "finishedAt": "2026-10-25T09:00:00Z"}


def waiting(reason, message=""):
    return {"waiting": {"reason": reason, "message": message}}


def condition(type_, status_, reason=None, message=None):
    c = {"type": type_, "status": status_}
    if reason:
        c["reason"] = reason
    if message:
        c["message"] = message
    return c


def pod(name, phase="Running", statuses=(), conditions=None, containers=None):
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": NS},
        "spec": {"containers": containers or [container()]},
        "status": {
            "phase": phase,
            "conditions": conditions
            or [condition("PodScheduled", "True"), condition("Ready", "False")],
            "containerStatuses": list(statuses),
        },
    }


def event(pod_name, reason, message, type_="Warning", count=1):
    return {
        "type": type_,
        "reason": reason,
        "message": message,
        "count": count,
        "involvedObject": {"kind": "Pod", "name": pod_name, "namespace": NS},
    }


def scenario(description, category, subcause, pod_json, events=(), logs=None):
    return {
        "description": description,
        "expected": {"category": category, "subcause": subcause},
        "pod": pod_json,
        "events": list(events),
        "logs": logs or {},
    }


def build() -> dict[str, dict]:
    s: dict[str, dict] = {}

    n = "api-healthy"
    s["01_healthy"] = scenario(
        "正常稼働",
        "HEALTHY",
        None,
        pod(
            n,
            statuses=[status(ready=True)],
            conditions=[condition("PodScheduled", "True"), condition("Ready", "True")],
        ),
    )

    n = "api-oom"
    s["02_oom_killed"] = scenario(
        "CrashLoopBackOff だが直前の終了理由は OOMKilled (OOM を優先すべき)",
        "OOM_KILLED",
        None,
        pod(
            n,
            statuses=[
                status(
                    restarts=5,
                    state=waiting("CrashLoopBackOff", "back-off 2m40s restarting failed container"),
                    last=terminated("OOMKilled", 137),
                )
            ],
            containers=[container(mem_limit="128Mi")],
        ),
        [event(n, "BackOff", "Back-off restarting failed container app", count=12)],
    )

    n = "api-badtag"
    s["03_image_not_found"] = scenario(
        "存在しないタグを指定",
        "IMAGE_PULL",
        "image_not_found",
        pod(
            n,
            phase="Pending",
            statuses=[
                status(
                    image=IMAGE + "x", state=waiting("ImagePullBackOff", "Back-off pulling image")
                )
            ],
            containers=[container(image=IMAGE + "x")],
        ),
        [
            event(
                n,
                "Failed",
                f'Failed to pull image "{IMAGE}x": rpc error: code = NotFound desc = '
                f"failed to pull and unpack image: {IMAGE}x: not found",
                count=4,
            )
        ],
    )

    n = "api-private"
    s["04_image_registry_auth"] = scenario(
        "プライベートレジストリで imagePullSecrets 未設定",
        "IMAGE_PULL",
        "registry_auth",
        pod(n, phase="Pending", statuses=[status(state=waiting("ErrImagePull"))]),
        [
            event(
                n,
                "Failed",
                f'Failed to pull image "{IMAGE}": failed to authorize: '
                "failed to fetch oauth token: unexpected status: 401 Unauthorized",
                count=3,
            )
        ],
    )

    n = "api-nocm"
    s["05_missing_configmap"] = scenario(
        "参照先 ConfigMap が未作成",
        "CONFIG_ERROR",
        "missing_configmap",
        pod(
            n,
            phase="Pending",
            statuses=[
                status(
                    state=waiting("CreateContainerConfigError", 'configmap "api-config" not found')
                )
            ],
        ),
    )

    n = "api-nosecret"
    s["06_missing_secret"] = scenario(
        "参照先 Secret が別 namespace にある",
        "CONFIG_ERROR",
        "missing_secret",
        pod(
            n,
            phase="Pending",
            statuses=[
                status(
                    state=waiting("CreateContainerConfigError", 'secret "db-credentials" not found')
                )
            ],
        ),
    )

    n = "batch-bigmem"
    s["07_insufficient_memory"] = scenario(
        "requests がノードの空き容量を超過",
        "UNSCHEDULABLE",
        "insufficient_resources",
        pod(
            n,
            phase="Pending",
            conditions=[
                condition(
                    "PodScheduled",
                    "False",
                    "Unschedulable",
                    "0/3 nodes are available: 3 Insufficient memory. preemption: 0/3 nodes are "
                    "available: 3 No preemption victims found for incoming pod.",
                )
            ],
        ),
        [event(n, "FailedScheduling", "0/3 nodes are available: 3 Insufficient memory.")],
    )

    n = "db-0"
    s["08_pvc_unbound"] = scenario(
        "StorageClass が存在せず PVC が Pending",
        "UNSCHEDULABLE",
        "pvc_unbound",
        pod(
            n,
            phase="Pending",
            conditions=[
                condition(
                    "PodScheduled",
                    "False",
                    "Unschedulable",
                    "0/3 nodes are available: pod has unbound immediate PersistentVolumeClaims.",
                )
            ],
        ),
    )

    n = "api-taint"
    s["09_taint"] = scenario(
        "全ノードに taint があり toleration 不足",
        "UNSCHEDULABLE",
        "taint",
        pod(
            n,
            phase="Pending",
            conditions=[
                condition(
                    "PodScheduled",
                    "False",
                    "Unschedulable",
                    "0/3 nodes are available: 3 node(s) had untolerated taint {dedicated: infra}.",
                )
            ],
        ),
    )

    n = "api-gpu-node"
    s["10_node_selector"] = scenario(
        "nodeSelector のラベルを持つノードがない",
        "UNSCHEDULABLE",
        "node_selector",
        pod(
            n,
            phase="Pending",
            conditions=[
                condition(
                    "PodScheduled",
                    "False",
                    "Unschedulable",
                    "0/3 nodes are available: 3 node(s) didn't match Pod's node affinity/selector.",
                )
            ],
        ),
    )

    n = "api-slowstart"
    s["11_liveness_probe"] = scenario(
        "起動が遅く Liveness Probe で kill される (exitCode 137 でも OOM ではない)",
        "PROBE_FAILURE",
        "liveness",
        pod(
            n,
            statuses=[
                status(
                    restarts=4,
                    state=waiting("CrashLoopBackOff"),
                    last=terminated("Error", 137),
                )
            ],
        ),
        [
            event(
                n,
                "Unhealthy",
                'Liveness probe failed: Get "http://10.128.2.15:8080/healthz": '
                "dial tcp 10.128.2.15:8080: connect: connection refused",
                count=12,
            ),
            event(
                n,
                "Killing",
                "Container app failed liveness probe, will be restarted",
                type_="Normal",
                count=4,
            ),
        ],
        {"app": "Starting application...\nLoading model weights (this may take a while)\n"},
    )

    n = "api-notready"
    s["12_readiness_probe"] = scenario(
        "依存先 DB 停止で Readiness のみ失敗",
        "PROBE_FAILURE",
        "readiness",
        pod(n, statuses=[status(ready=False)]),
        [
            event(
                n,
                "Unhealthy",
                "Readiness probe failed: HTTP probe failed with statuscode: 503",
                count=30,
            )
        ],
    )

    n = "api-noenv"
    s["13_crash_missing_env"] = scenario(
        "必須環境変数が未定義",
        "CRASH_LOOP",
        "missing_env",
        pod(
            n,
            statuses=[
                status(restarts=6, state=waiting("CrashLoopBackOff"), last=terminated("Error", 1))
            ],
        ),
        logs={
            "app": "Traceback (most recent call last):\n"
            '  File "/app/main.py", line 8, in <module>\n'
            '    DB_URL = os.environ["DATABASE_URL"]\n'
            "KeyError: 'DATABASE_URL'\n"
        },
    )

    n = "worker-redis"
    s["14_crash_dependency"] = scenario(
        "接続先 Redis の Service 名誤り",
        "CRASH_LOOP",
        "dependency_unreachable",
        pod(
            n,
            statuses=[
                status(restarts=3, state=waiting("CrashLoopBackOff"), last=terminated("Error", 1))
            ],
        ),
        logs={
            "app": "INFO worker starting\n"
            "ERROR Error 111 connecting to redis-master:6379. Connection refused.\n"
        },
    )

    n = "api-typo"
    s["15_crash_bad_command"] = scenario(
        "command のタイプミス (ログなし、exitCode 127)",
        "CRASH_LOOP",
        "bad_command",
        pod(
            n,
            statuses=[
                status(restarts=2, state=waiting("CrashLoopBackOff"), last=terminated("Error", 127))
            ],
        ),
    )

    n = "nginx-nonroot"
    s["16_crash_permission"] = scenario(
        "OpenShift の任意 UID で書き込み不可",
        "CRASH_LOOP",
        "permission",
        pod(
            n,
            statuses=[
                status(restarts=5, state=waiting("CrashLoopBackOff"), last=terminated("Error", 1))
            ],
        ),
        logs={
            "app": 'nginx: [emerg] mkdir() "/var/cache/nginx/client_temp" failed '
            "(13: Permission denied)\n"
        },
    )

    # --- 以下は現行ルールで取りこぼす既知ケース (改善対象) ---

    n = "api-badflag"
    s["17_crash_invalid_config"] = scenario(
        "設定値の誤り. ログの文言が汎用パターンに当たらない",
        "CRASH_LOOP",
        "invalid_config",
        pod(
            n,
            statuses=[
                status(restarts=4, state=waiting("CrashLoopBackOff"), last=terminated("Error", 2))
            ],
        ),
        logs={
            "app": 'level=info msg="loading config" path=/etc/api/config.yaml\n'
            "level=fatal msg=\"invalid value for log_level: 'verbose' "
            '(expected one of debug, info, warn, error)"\n'
        },
    )

    n = "api-migrate"
    s["18_init_container_failure"] = scenario(
        "initContainer (DB マイグレーション) の失敗. containerStatuses ではなく "
        "initContainerStatuses に現れる",
        "CRASH_LOOP",
        "dependency_unreachable",
        {
            **pod(n, phase="Pending", statuses=[status(state=waiting("PodInitializing"))]),
            "status": {
                "phase": "Pending",
                "conditions": [
                    condition("PodScheduled", "True"),
                    condition("Initialized", "False", "ContainersNotInitialized"),
                ],
                "initContainerStatuses": [
                    {
                        "name": "migrate",
                        "ready": False,
                        "restartCount": 3,
                        "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                        "lastState": {"terminated": terminated("Error", 1)},
                    }
                ],
                "containerStatuses": [status(state=waiting("PodInitializing"))],
            },
        },
        logs={
            "migrate": "psycopg2.OperationalError: could not connect to server: "
            "Connection refused\n"
        },
    )
    return s


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for name, data in build().items():
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {len(build())} scenarios in {OUT}")


if __name__ == "__main__":
    main()
