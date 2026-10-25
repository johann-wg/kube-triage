"""シナリオ集に対する判定精度の評価.

各シナリオ JSON は Bundle の各フィールドに加えて
`expected: {"category": ..., "subcause": ...}` を持つ。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kube_triage.bundle import Bundle
from kube_triage.models import Finding
from kube_triage.rules import diagnose


@dataclass
class CaseResult:
    scenario: str
    expected_category: str
    expected_subcause: str | None
    predicted_category: str
    predicted_subcause: str | None

    @property
    def category_ok(self) -> bool:
        return self.expected_category == self.predicted_category

    @property
    def subcause_ok(self) -> bool:
        return self.category_ok and self.expected_subcause == self.predicted_subcause


def evaluate(
    scenario_dir: str | Path,
    engine: Callable[[Bundle], list[Finding]] = diagnose,
) -> list[CaseResult]:
    results = []
    for path in sorted(Path(scenario_dir).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        main = engine(Bundle.from_dict(data))[0]
        expected = data["expected"]
        results.append(
            CaseResult(
                scenario=path.stem,
                expected_category=expected["category"],
                expected_subcause=expected.get("subcause"),
                predicted_category=main.category,
                predicted_subcause=main.subcause,
            )
        )
    return results


def summarize(results: list[CaseResult]) -> str:
    """Markdown の表として結果をまとめる."""
    n = len(results)
    cat = sum(r.category_ok for r in results)
    sub = sum(r.subcause_ok for r in results)
    lines = [
        "| 指標 | 正解数 | 正解率 |",
        "|---|---|---|",
        f"| カテゴリ | {cat}/{n} | {cat / n:.1%} |",
        f"| カテゴリ + 原因 | {sub}/{n} | {sub / n:.1%} |",
        "",
        "| シナリオ | 正解 | 判定 | 結果 |",
        "|---|---|---|---|",
    ]
    for r in results:
        exp = r.expected_category + (f" / {r.expected_subcause}" if r.expected_subcause else "")
        pred = r.predicted_category + (f" / {r.predicted_subcause}" if r.predicted_subcause else "")
        mark = "OK" if r.subcause_ok else ("原因NG" if r.category_ok else "NG")
        lines.append(f"| {r.scenario} | {exp} | {pred} | {mark} |")
    return "\n".join(lines)
