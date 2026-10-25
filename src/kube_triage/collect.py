"""kubectl を呼び出して実クラスタから Bundle を収集する."""

from __future__ import annotations

import json
import subprocess
from typing import Any


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, encoding="utf-8")


def _json(args: list[str]) -> Any:
    proc = _run(args)
    if proc.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def collect(pod: str, namespace: str = "default", tail: int = 200) -> dict[str, Any]:
    """Pod / イベント / ログを取得して Bundle 形式の dict を返す.

    initContainer も対象. ログは直前の異常終了時 (--previous) と現在の両方を取る。
    """
    pod_json = _json(["-n", namespace, "get", "pod", pod, "-o", "json"])
    events = _json(
        [
            "-n",
            namespace,
            "get",
            "events",
            "--field-selector",
            f"involvedObject.name={pod}",
            "-o",
            "json",
        ]
    ).get("items", [])

    status = pod_json.get("status", {})
    logs: dict[str, str] = {}
    for cs in status.get("initContainerStatuses", []) + status.get("containerStatuses", []):
        name = cs["name"]
        base = ["-n", namespace, "logs", pod, "-c", name, f"--tail={tail}"]
        # 直前に終了したコンテナと現在のコンテナの両方を取る.
        # 古いコンテナが削除済みだと --previous は終了コード 0 のまま
        # "unable to retrieve container logs" を返すため、本文でも判定する.
        parts = []
        for args in ([*base, "--previous"], base):
            proc = _run(args)
            if proc.returncode == 0 and not proc.stdout.startswith("unable to retrieve"):
                parts.append(proc.stdout)
        if parts:
            logs[name] = "\n".join(parts)

    return {"pod": pod_json, "events": events, "logs": logs}
