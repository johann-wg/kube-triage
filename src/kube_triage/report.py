"""診断結果の出力 (テキスト / JSON)."""

from __future__ import annotations

import json

from kube_triage.bundle import Bundle
from kube_triage.models import Finding


def to_text(bundle: Bundle, findings: list[Finding]) -> str:
    main, others = findings[0], findings[1:]
    label = main.category + (f" / {main.subcause}" if main.subcause else "")
    lines = [
        f"Pod: {bundle.namespace}/{bundle.name}  (phase: {bundle.phase})",
        f"判定: {label}  [確度 {main.confidence:.0%}]",
        f"  {main.title}",
    ]
    if main.evidence:
        lines.append("根拠:")
        lines += [f"  - [{e.source}] {e.text}" for e in main.evidence]
    if main.actions:
        lines.append("推奨対応:")
        lines += [f"  {i}. {a}" for i, a in enumerate(main.actions, 1)]
    if main.commands:
        lines.append("確認コマンド:")
        lines += [f"  $ {c}" for c in main.commands]
    if others:
        lines.append("その他の候補:")
        lines += [f"  - {f.category} [確度 {f.confidence:.0%}] {f.title}" for f in others]
    return "\n".join(lines)


def to_json(bundle: Bundle, findings: list[Finding]) -> str:
    return json.dumps(
        {
            "pod": f"{bundle.namespace}/{bundle.name}",
            "phase": bundle.phase,
            "findings": [f.to_dict() for f in findings],
        },
        ensure_ascii=False,
        indent=2,
    )
