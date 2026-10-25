"""ルールベースの障害判定.

各ルールは Bundle を受け取り、該当すれば Finding を返す。
複数のルールが該当した場合は confidence の高い順に並べ、先頭を主判定とする。
例: CrashLoopBackOff でも直前の終了理由が OOMKilled なら OOM_KILLED を優先する。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from kube_triage.bundle import Bundle
from kube_triage.models import Category, Evidence, Finding

Rule = Callable[[Bundle], "Finding | None"]


def _kubectl(bundle: Bundle, args: str) -> str:
    return f"kubectl -n {bundle.namespace} {args}"


def _events(bundle: Bundle, reason: str | None = None, contains: str | None = None):
    for e in bundle.pod_events():
        if reason and e.get("reason") != reason:
            continue
        if contains and contains.lower() not in e.get("message", "").lower():
            continue
        yield e


def _event_evidence(e: dict) -> Evidence:
    count = e.get("count", 1)
    return Evidence(f"event/{e.get('reason')} (x{count})", e.get("message", ""))


# ---------------------------------------------------------------- OOMKilled


def rule_oom_killed(bundle: Bundle) -> Finding | None:
    for cs in bundle.container_statuses():
        for key in ("lastState", "state"):
            term = cs.get(key, {}).get("terminated")
            if term and term.get("reason") == "OOMKilled":
                name = cs["name"]
                limits = bundle.container_spec(name).get("resources", {}).get("limits", {})
                evidence = [
                    Evidence(
                        f"status.containerStatuses[{name}].{key}",
                        f"terminated.reason=OOMKilled, exitCode={term.get('exitCode')}",
                    )
                ]
                if "memory" in limits:
                    evidence.append(
                        Evidence(
                            f"spec.containers[{name}].resources.limits",
                            f"memory={limits['memory']}",
                        )
                    )
                return Finding(
                    category=Category.OOM_KILLED,
                    title="メモリ上限超過によりコンテナが強制終了されています",
                    confidence=0.95,
                    evidence=evidence,
                    actions=[
                        "resources.limits.memory が実際の使用量に対して小さすぎないか確認する",
                        "メモリ使用量の推移を確認し、リーク（右肩上がり）か一時的なピークかを切り分ける",
                        "JVM 等はヒープ上限をコンテナの limits に合わせて設定する",
                    ],
                    commands=[
                        _kubectl(bundle, f"describe pod {bundle.name}"),
                        _kubectl(bundle, f"top pod {bundle.name} --containers"),
                    ],
                )
    return None


# ---------------------------------------------------------------- Image pull

_IMAGE_PULL_REASONS = {"ErrImagePull", "ImagePullBackOff", "InvalidImageName"}
_IMAGE_SUBCAUSES = [
    ("image_not_found", re.compile(r"not found|manifest unknown|does not exist", re.I)),
    (
        "registry_auth",
        re.compile(r"unauthorized|authentication required|pull access denied|denied", re.I),
    ),
]


def rule_image_pull(bundle: Bundle) -> Finding | None:
    for cs in bundle.container_statuses():
        waiting = cs.get("state", {}).get("waiting", {})
        if waiting.get("reason") not in _IMAGE_PULL_REASONS:
            continue
        name = cs["name"]
        image = bundle.container_spec(name).get("image", cs.get("image", "?"))
        evidence = [
            Evidence(
                f"status.containerStatuses[{name}].state", f"waiting.reason={waiting['reason']}"
            ),
            Evidence(f"spec.containers[{name}].image", image),
        ]
        messages = [waiting.get("message", "")]
        for e in _events(bundle, reason="Failed", contains="pull"):
            evidence.append(_event_evidence(e))
            messages.append(e.get("message", ""))
        subcause = _match_first(_IMAGE_SUBCAUSES, " ".join(messages))
        actions = {
            "image_not_found": [
                "イメージ名・タグのタイプミスがないか確認する",
                "レジストリに該当タグが push 済みか確認する",
            ],
            "registry_auth": [
                "imagePullSecrets が Pod / ServiceAccount に設定されているか確認する",
                "Secret の認証情報が有効期限切れでないか確認する",
            ],
        }.get(subcause or "", ["Event のメッセージでレジストリ側のエラー内容を確認する"])
        return Finding(
            category=Category.IMAGE_PULL,
            title="コンテナイメージを取得できていません",
            confidence=0.9,
            subcause=subcause,
            evidence=evidence,
            actions=actions,
            commands=[
                _kubectl(bundle, f"describe pod {bundle.name}"),
                _kubectl(bundle, f"get pod {bundle.name} -o jsonpath='{{.spec.imagePullSecrets}}'"),
            ],
        )
    return None


# ---------------------------------------------------------------- ConfigMap / Secret

_CONFIG_SUBCAUSES = [
    ("missing_key", re.compile(r"couldn't find key", re.I)),
    ("missing_configmap", re.compile(r'configmap "[^"]+" not found', re.I)),
    ("missing_secret", re.compile(r'secret "[^"]+" not found', re.I)),
]


def rule_config_error(bundle: Bundle) -> Finding | None:
    for cs in bundle.container_statuses():
        waiting = cs.get("state", {}).get("waiting", {})
        if waiting.get("reason") != "CreateContainerConfigError":
            continue
        name = cs["name"]
        message = waiting.get("message", "")
        evidence = [
            Evidence(
                f"status.containerStatuses[{name}].state",
                f"waiting.reason=CreateContainerConfigError, message={message}",
            )
        ]
        subcause = _match_first(_CONFIG_SUBCAUSES, message)
        return Finding(
            category=Category.CONFIG_ERROR,
            title="参照している ConfigMap / Secret が存在しないためコンテナを作成できません",
            confidence=0.9,
            subcause=subcause,
            evidence=evidence,
            actions=[
                "Pod と同じ namespace に該当の ConfigMap / Secret が存在するか確認する",
                "名前・キー名のタイプミス、デプロイ順序（参照先が後から作られる）を確認する",
            ],
            commands=[
                _kubectl(bundle, "get configmap,secret"),
                _kubectl(bundle, f"get pod {bundle.name} -o yaml"),
            ],
        )
    return None


# ---------------------------------------------------------------- Unschedulable

_SCHEDULE_SUBCAUSES = [
    ("pvc_unbound", re.compile(r"unbound .*PersistentVolumeClaim", re.I)),
    ("insufficient_resources", re.compile(r"Insufficient (cpu|memory|nvidia\.com/gpu)", re.I)),
    ("taint", re.compile(r"untolerated taint", re.I)),
    ("node_selector", re.compile(r"didn't match Pod's node affinity/selector", re.I)),
]

_SCHEDULE_ACTIONS = {
    "pvc_unbound": [
        "PVC の状態（Pending）と StorageClass の有無・プロビジョナーを確認する",
        "静的 PV の場合は容量・accessModes が PVC と一致しているか確認する",
    ],
    "insufficient_resources": [
        "requests の値が過大でないか確認する",
        "ノードの空き容量を確認し、必要ならノード追加やスケールを検討する",
    ],
    "taint": [
        "ノードの taint と Pod の tolerations が対応しているか確認する",
    ],
    "node_selector": [
        "nodeSelector / affinity のラベルを持つノードが存在するか確認する",
    ],
}


def rule_unschedulable(bundle: Bundle) -> Finding | None:
    cond = bundle.conditions().get("PodScheduled")
    if not cond or cond.get("status") != "False" or cond.get("reason") != "Unschedulable":
        return None
    message = cond.get("message", "")
    evidence = [Evidence("status.conditions[PodScheduled]", message)]
    for e in _events(bundle, reason="FailedScheduling"):
        evidence.append(_event_evidence(e))
        message += " " + e.get("message", "")
    subcause = _match_first(_SCHEDULE_SUBCAUSES, message)
    return Finding(
        category=Category.UNSCHEDULABLE,
        title="Pod をスケジュールできるノードがありません",
        confidence=0.9,
        subcause=subcause,
        evidence=evidence,
        actions=_SCHEDULE_ACTIONS.get(subcause or "", ["FailedScheduling のメッセージを確認する"]),
        commands=[
            _kubectl(bundle, "get pvc") if subcause == "pvc_unbound" else "kubectl describe nodes",
            _kubectl(bundle, f"describe pod {bundle.name}"),
        ],
    )


# ---------------------------------------------------------------- Probe


def rule_probe_failure(bundle: Bundle) -> Finding | None:
    liveness = list(_events(bundle, reason="Unhealthy", contains="Liveness probe failed"))
    readiness = list(_events(bundle, reason="Unhealthy", contains="Readiness probe failed"))
    if liveness:
        kills = list(_events(bundle, reason="Killing", contains="liveness"))
        return Finding(
            category=Category.PROBE_FAILURE,
            title="Liveness Probe の失敗によりコンテナが再起動されています",
            confidence=0.9,
            subcause="liveness",
            evidence=[_event_evidence(e) for e in liveness + kills],
            actions=[
                "アプリの起動時間に対して initialDelaySeconds / startupProbe が足りているか確認する",
                "Probe のパス・ポートがアプリの実装と一致しているか確認する",
                "高負荷時に応答が遅れて timeoutSeconds を超えていないか確認する",
            ],
            commands=[
                _kubectl(
                    bundle,
                    f"get pod {bundle.name} -o jsonpath='{{.spec.containers[*].livenessProbe}}'",
                ),
                _kubectl(bundle, f"logs {bundle.name} --previous"),
            ],
        )
    if readiness:
        return Finding(
            category=Category.PROBE_FAILURE,
            title="Readiness Probe が失敗しており、Service からトラフィックを受けられません",
            confidence=0.85,
            subcause="readiness",
            evidence=[_event_evidence(e) for e in readiness],
            actions=[
                "Readiness の判定先（DB などの依存先）が正常か確認する",
                "Probe のパス・ポート・タイムアウト設定を確認する",
            ],
            commands=[
                _kubectl(bundle, f"describe pod {bundle.name}"),
                _kubectl(bundle, "get endpoints"),
            ],
        )
    return None


# ---------------------------------------------------------------- CrashLoop

# 上から順に評価する. 原因に近い具体的なパターンを先に置く.
_CRASH_LOG_PATTERNS = [
    (
        "missing_env",
        re.compile(
            r"KeyError: '[A-Z0-9_]+'|environment variable \S+ (is )?(not set|required)"
            r"|missing required env",
            re.I,
        ),
    ),
    (
        "dependency_unreachable",
        re.compile(
            r"connection refused|could not connect|timed out connecting"
            r"|name or service not known|no such host",
            re.I,
        ),
    ),
    ("permission", re.compile(r"permission denied|operation not permitted", re.I)),
    (
        "bad_command",
        re.compile(r"executable file not found|exec format error|command not found", re.I),
    ),
]
_EXIT_CODE_SUBCAUSES = {126: "permission", 127: "bad_command"}

_CRASH_ACTIONS = {
    "missing_env": ["Deployment の env / envFrom に必要な環境変数が定義されているか確認する"],
    "dependency_unreachable": [
        "接続先 Service 名・ポートが正しいか、接続先 Pod が起動しているか確認する",
        "NetworkPolicy で通信が遮断されていないか確認する",
    ],
    "permission": [
        "securityContext（runAsUser / readOnlyRootFilesystem）とファイル権限を確認する",
        "OpenShift の場合は割り当てられた SCC で実行ユーザーが変わっていないか確認する",
    ],
    "bad_command": ["command / args のスペルとイメージ内の実行ファイルの有無を確認する"],
}


def rule_crash_loop(bundle: Bundle) -> Finding | None:
    for cs in bundle.container_statuses():
        waiting = cs.get("state", {}).get("waiting", {})
        last = cs.get("lastState", {}).get("terminated")
        if waiting.get("reason") != "CrashLoopBackOff" and not (
            last and cs.get("restartCount", 0) > 0
        ):
            continue
        name = cs["name"]
        evidence = [
            Evidence(
                f"status.containerStatuses[{name}]",
                f"state={waiting.get('reason', 'Running')}, restartCount={cs.get('restartCount', 0)}",
            )
        ]
        exit_code = None
        if last:
            exit_code = last.get("exitCode")
            evidence.append(
                Evidence(
                    f"status.containerStatuses[{name}].lastState",
                    f"terminated.reason={last.get('reason')}, exitCode={exit_code}",
                )
            )

        subcause, log_line = _match_log(bundle.logs.get(name, ""))
        if log_line:
            evidence.append(Evidence(f"logs/{name} (previous)", log_line))
        elif exit_code in _EXIT_CODE_SUBCAUSES:
            subcause = _EXIT_CODE_SUBCAUSES[exit_code]

        return Finding(
            category=Category.CRASH_LOOP,
            title="コンテナが起動直後に異常終了し、再起動を繰り返しています",
            confidence=0.8 if subcause else 0.6,
            subcause=subcause,
            evidence=evidence,
            actions=_CRASH_ACTIONS.get(
                subcause or "",
                ["直前のログ（--previous）全体を確認し、最初に出ているエラーを特定する"],
            ),
            commands=[_kubectl(bundle, f"logs {bundle.name} -c {name} --previous")],
        )
    return None


# ---------------------------------------------------------------- helpers


def _match_first(patterns: list[tuple[str, re.Pattern]], text: str) -> str | None:
    for label, pattern in patterns:
        if pattern.search(text):
            return label
    return None


def _match_log(log: str) -> tuple[str | None, str | None]:
    """ログを行単位で見て、最初にマッチしたサブ原因とその行を返す."""
    lines = log.splitlines()
    for label, pattern in _CRASH_LOG_PATTERNS:
        for line in lines:
            if pattern.search(line):
                return label, line.strip()
    return None, None


RULES: list[Rule] = [
    rule_oom_killed,
    rule_image_pull,
    rule_config_error,
    rule_unschedulable,
    rule_probe_failure,
    rule_crash_loop,
]


def diagnose(bundle: Bundle) -> list[Finding]:
    """該当した Finding を確度の高い順に返す. 先頭が主判定."""
    findings = [f for rule in RULES if (f := rule(bundle)) is not None]
    if findings:
        return sorted(findings, key=lambda f: f.confidence, reverse=True)

    statuses = bundle.container_statuses()
    if bundle.phase == "Running" and statuses and all(cs.get("ready") for cs in statuses):
        return [Finding(Category.HEALTHY, "異常は検出されませんでした", confidence=0.9)]
    return [
        Finding(
            Category.UNKNOWN,
            "既知のパターンに該当しませんでした",
            confidence=0.0,
            evidence=[Evidence("status.phase", bundle.phase)],
            actions=["describe とイベント、ログを確認して手動で切り分ける"],
            commands=[_kubectl(bundle, f"describe pod {bundle.name}")],
        )
    ]
