"""ルール / LLM / ハイブリッドとモデルごとの精度・費用を比較し、Markdown にまとめる.

キャッシュ済みの応答だけを使う (--offline 相当) ため、API を呼ばず費用もかからない。
新しい組み合わせを測るときは先に `kube-triage eval ... --engine llm --model ...` を実行する。

    python scripts/compare_engines.py > docs/engine-comparison.md
"""

from __future__ import annotations

import sys

from kube_triage.evaluate import evaluate
from kube_triage.llm import HybridDiagnoser, LLMDiagnoser
from kube_triage.rules import diagnose

SETS = [
    ("scenarios-holdout", "評価専用（holdout・14 件）"),
    ("scenarios-real", "実データ（kind・15 件）"),
    ("scenarios", "合成データ（18 件）"),
]
MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]


def row(label: str, results, usage=None) -> str:
    n = len(results)
    cat = sum(r.category_ok for r in results)
    sub = sum(r.subcause_ok for r in results)
    if usage is None:
        calls, cost = "0", "$0"
    else:
        calls = str(usage.calls + usage.cache_hits)
        cost = f"${usage.estimated_cost_usd / n * 100:.2f}"
    return f"| {label} | {cat}/{n} ({cat / n:.0%}) | {sub}/{n} ({sub / n:.0%}) | {calls} | {cost} |"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print("# 判定エンジンの比較\n")
    print(
        "`scripts/compare_engines.py` で生成。費用はキャッシュした実際の API 使用量から算出した、"
    )
    print("Pod 100 件あたりの推定額（USD）。LLM の effort はすべて `medium`。\n")
    for directory, title in SETS:
        print(f"## {title}\n")
        print("| エンジン | カテゴリ | カテゴリ + 原因 | LLM 呼び出し | 100 件あたり費用 |")
        print("|---|---|---|---|---|")
        print(row("ルールのみ", evaluate(directory, diagnose)))
        for model in MODELS:
            for mode in ("llm", "hybrid"):
                llm = LLMDiagnoser(model=model, offline=True)
                runner = llm if mode == "llm" else HybridDiagnoser(llm)
                try:
                    results = evaluate(directory, runner.diagnose)
                except LookupError:
                    continue  # この組み合わせは未測定
                label = f"{'LLM のみ' if mode == 'llm' else 'ハイブリッド'}（{model}）"
                print(row(label, results, llm.usage))
        print()


if __name__ == "__main__":
    main()
