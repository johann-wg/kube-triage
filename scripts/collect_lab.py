"""kind 上で再現した障害 Pod から評価用シナリオを収集する.

各 Pod の annotation `triage-lab/expected` ("CATEGORY" または "CATEGORY/subcause")
を正解ラベルとして、scenarios-real/<pod>.json に保存する。

    python scripts/collect_lab.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kube_triage.collect import collect

NAMESPACE = "triage-lab"
OUT = Path(__file__).resolve().parent.parent / "scenarios-real"


def lab_pods() -> list[dict]:
    proc = subprocess.run(
        [
            "kubectl", "-n", NAMESPACE, "get", "pods",
            "-l", "app.kubernetes.io/part-of=triage-lab", "-o", "json",
        ],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )  # fmt: skip
    return json.loads(proc.stdout)["items"]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    pods = lab_pods()
    for pod in pods:
        name = pod["metadata"]["name"]
        label = pod["metadata"].get("annotations", {}).get("triage-lab/expected", "UNKNOWN")
        category, _, subcause = label.partition("/")
        data = collect(name, NAMESPACE)
        data = {
            "description": f"kind で再現 ({name})",
            "expected": {"category": category, "subcause": subcause or None},
            **data,
        }
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"saved {path.name}  expected={label}")
    print(f"collected {len(pods)} pods into {OUT}")


if __name__ == "__main__":
    main()
