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

    再起動しているコンテナは直前の異常終了時のログ (--previous) を優先する。
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

    logs: dict[str, str] = {}
    for cs in pod_json.get("status", {}).get("containerStatuses", []):
        name = cs["name"]
        base = ["-n", namespace, "logs", pod, "-c", name, f"--tail={tail}"]
        proc = _run([*base, "--previous"]) if cs.get("restartCount", 0) > 0 else None
        if proc is None or proc.returncode != 0:
            proc = _run(base)
        if proc.returncode == 0:
            logs[name] = proc.stdout

    return {"pod": pod_json, "events": events, "logs": logs}
