"""コマンドラインインターフェース."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kube_triage import report
from kube_triage.bundle import Bundle
from kube_triage.rules import diagnose


def _build_engine(args: argparse.Namespace):
    """(判定関数, LLM の使用量 or None) を返す."""
    if args.engine == "rules":
        return diagnose, None
    from kube_triage.llm import HybridDiagnoser, LLMDiagnoser

    llm = LLMDiagnoser(
        model=args.model, effort=args.effort, cache_dir=args.cache_dir, offline=args.offline
    )
    runner = llm if args.engine == "llm" else HybridDiagnoser(llm)
    return runner.diagnose, llm.usage


def _print_usage(usage) -> None:
    if usage is None:
        return
    print(
        f"LLM: API 呼び出し {usage.calls} 回 / キャッシュ利用 {usage.cache_hits} 回 / "
        f"入力 {usage.input_tokens:,} トークン / 出力 {usage.output_tokens:,} トークン / "
        f"費用 約 ${usage.cost_usd:.4f} (キャッシュなしの場合 約 ${usage.estimated_cost_usd:.4f})",
        file=sys.stderr,
    )


def _cmd_analyze(args: argparse.Namespace) -> int:
    bundle = Bundle.load(args.bundle)
    engine, usage = _build_engine(args)
    findings = engine(bundle)
    print(report.to_json(bundle, findings) if args.json else report.to_text(bundle, findings))
    _print_usage(usage)
    return 0


def _cmd_collect(args: argparse.Namespace) -> int:
    from kube_triage.collect import collect

    data = collect(args.pod, args.namespace)
    out = Path(args.output or f"{args.pod}.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {out}")
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    from kube_triage.evaluate import evaluate, summarize

    engine, usage = _build_engine(args)
    results = evaluate(args.scenarios, engine)
    table = summarize(results)
    print(table)
    _print_usage(usage)
    if args.report:
        Path(args.report).write_text(table + "\n", encoding="utf-8")
    if args.min_accuracy is not None:
        accuracy = sum(r.category_ok for r in results) / len(results)
        if accuracy < args.min_accuracy:
            print(f"category accuracy {accuracy:.1%} < {args.min_accuracy:.1%}", file=sys.stderr)
            return 1
    return 0


def _add_engine_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--engine",
        choices=["rules", "llm", "hybrid"],
        default="rules",
        help="rules: ルールのみ / llm: Claude のみ / hybrid: ルールで判定できない場合だけ Claude",
    )
    p.add_argument("--model", default="claude-opus-5-5", help="Claude のモデル ID")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--cache-dir", default="llm-cache", help="API 応答のキャッシュ先")
    p.add_argument(
        "--offline", action="store_true", help="API を呼ばずキャッシュのみ使う (CI・再現用)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kube-triage", description="Kubernetes Pod 障害の一次切り分け"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="Bundle(JSON) を診断する")
    p.add_argument("bundle")
    p.add_argument("--json", action="store_true", help="JSON で出力")
    _add_engine_args(p)
    p.set_defaults(func=_cmd_analyze)

    p = sub.add_parser("collect", help="kubectl で実クラスタから Bundle を収集する")
    p.add_argument("pod")
    p.add_argument("-n", "--namespace", default="default")
    p.add_argument("-o", "--output")
    p.set_defaults(func=_cmd_collect)

    p = sub.add_parser("eval", help="シナリオ集で判定精度を評価する")
    p.add_argument("scenarios", nargs="?", default="scenarios")
    p.add_argument("--report", help="結果を Markdown で保存するパス")
    p.add_argument("--min-accuracy", type=float, help="カテゴリ正解率がこれ未満なら終了コード 1")
    _add_engine_args(p)
    p.set_defaults(func=_cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
