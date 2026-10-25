"""Claude API による障害判定.

ルールと同じ Finding を返すため、評価・レポートの仕組みをそのまま使える。
API の応答は入力のハッシュをキーにキャッシュし、同じ評価を再実行するときは
API を呼ばない (CI や第三者は費用ゼロで結果を再現できる)。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kube_triage.bundle import Bundle
from kube_triage.models import Category, Evidence, Finding

DEFAULT_MODEL = "claude-opus-5-5"
# プロンプトや入力の整形を変えたら上げる. キャッシュキーに含まれるため古い応答は使われなくなる
PROMPT_VERSION = "1"

# 100 万トークンあたりの料金 (USD, 入力 / 出力). Anthropic 公式の API 料金
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
# 拒否時にサーバー側で別モデルに切り替える機能 (fallbacks) が使えるモデル
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}

SUBCAUSES = {
    Category.IMAGE_PULL: ["image_not_found", "registry_auth"],
    Category.CONFIG_ERROR: ["missing_configmap", "missing_secret", "missing_key"],
    Category.UNSCHEDULABLE: ["insufficient_resources", "pvc_unbound", "taint", "node_selector"],
    Category.PROBE_FAILURE: ["liveness", "readiness"],
    Category.CRASH_LOOP: [
        "missing_env",
        "dependency_unreachable",
        "permission",
        "bad_command",
        "invalid_config",
    ],
}
CATEGORIES = [
    Category.HEALTHY,
    Category.OOM_KILLED,
    Category.IMAGE_PULL,
    Category.CONFIG_ERROR,
    Category.UNSCHEDULABLE,
    Category.PROBE_FAILURE,
    Category.CRASH_LOOP,
    Category.UNKNOWN,
]

SYSTEM_PROMPT = """\
あなたは Kubernetes / OpenShift の運用エンジニアです。
与えられた Pod の状態・イベント・ログから、障害の主原因を 1 つ判定してください。

カテゴリと原因 (subcause) の定義:
- HEALTHY: 異常なし (subcause は null)
- OOM_KILLED: メモリ上限超過で強制終了 (subcause は null)
- IMAGE_PULL: イメージ取得失敗. image_not_found = 名前・タグが存在しない / registry_auth = 認証エラー
- CONFIG_ERROR: コンテナ作成前の設定参照エラー. missing_configmap / missing_secret / missing_key
- UNSCHEDULABLE: スケジュール不可. insufficient_resources / pvc_unbound / taint / node_selector
- PROBE_FAILURE: ヘルスチェック失敗. liveness = 再起動される / readiness = トラフィックを受けられない
- CRASH_LOOP: 起動後の異常終了の繰り返し (initContainer を含む).
  missing_env = 必須環境変数の未定義 / dependency_unreachable = 接続先に到達できない /
  permission = 権限不足 / bad_command = コマンドや実行ファイルの誤り /
  invalid_config = 設定ファイルや設定値の誤り
- UNKNOWN: 上記のどれにも当てはまらない、または情報が不足している

判定のルール:
- 複数の症状がある場合は根本原因を選ぶ (例: CrashLoopBackOff でも直前の終了理由が OOMKilled なら OOM_KILLED)
- evidence には判断に使った入力の箇所と値を、入力からそのまま引用する。入力にない内容を書かない
- actions は運用者が次に行う確認・対応を日本語で 1〜3 件
- 原因が絞り切れない場合は subcause を null にし、confidence を下げる
"""


def _schema() -> dict[str, Any]:
    subcauses = sorted({s for values in SUBCAUSES.values() for s in values})
    return {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": CATEGORIES},
            "subcause": {"anyOf": [{"type": "string", "enum": subcauses}, {"type": "null"}]},
            "confidence": {"type": "number"},
            "title": {"type": "string", "description": "判定内容を表す日本語の一文"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "例: status.containerStatuses[app].lastState / event/BackOff / logs/app",
                        },
                        "text": {"type": "string"},
                    },
                    "required": ["source", "text"],
                    "additionalProperties": False,
                },
            },
            "actions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["category", "subcause", "confidence", "title", "evidence", "actions"],
        "additionalProperties": False,
    }


def compact_bundle(bundle: Bundle, log_lines: int = 40) -> dict[str, Any]:
    """判定に必要な情報だけを残し、トークン数を抑える."""
    pod = bundle.pod
    spec = pod.get("spec", {})
    keep = (
        "name",
        "image",
        "command",
        "args",
        "resources",
        "livenessProbe",
        "readinessProbe",
        "env",
        "envFrom",
        "volumeMounts",
    )
    containers = [
        {k: c[k] for k in keep if k in c}
        for c in spec.get("initContainers", []) + spec.get("containers", [])
    ]
    status = pod.get("status", {})

    def statuses(key: str) -> list[dict[str, Any]]:
        out = []
        for cs in status.get(key, []):
            out.append(
                {
                    k: cs[k]
                    for k in ("name", "ready", "restartCount", "state", "lastState")
                    if k in cs
                }
            )
        return out

    events = [
        {k: e.get(k) for k in ("type", "reason", "message", "count")} for e in bundle.pod_events()
    ]
    logs = {name: "\n".join(text.splitlines()[-log_lines:]) for name, text in bundle.logs.items()}
    return {
        "pod": {"name": bundle.name, "namespace": bundle.namespace, "phase": bundle.phase},
        "spec": {
            k: spec[k]
            for k in ("nodeSelector", "tolerations", "securityContext", "volumes")
            if k in spec
        }
        | {"containers": containers},
        "status": {
            "conditions": status.get("conditions", []),
            "initContainerStatuses": statuses("initContainerStatuses"),
            "containerStatuses": statuses("containerStatuses"),
        },
        "events": events,
        "logs": logs,
    }


@dataclass
class Usage:
    """API 呼び出しの集計.

    キャッシュから返した分も、実際に API を呼んだ場合の費用として estimated_cost_usd に含める。
    """

    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_model: dict[str, list[int]] = field(default_factory=dict)
    cached_by_model: dict[str, list[int]] = field(default_factory=dict)

    def add(self, model: str, input_tokens: int, output_tokens: int, cached: bool = False) -> None:
        if cached:
            self.cache_hits += 1
            target = self.cached_by_model
        else:
            self.calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            target = self.by_model
        tokens = target.setdefault(model, [0, 0])
        tokens[0] += input_tokens
        tokens[1] += output_tokens

    @staticmethod
    def _cost(by_model: dict[str, list[int]]) -> float:
        total = 0.0
        for model, (inp, out) in by_model.items():
            pin, pout = PRICES.get(model, (0.0, 0.0))
            total += inp / 1e6 * pin + out / 1e6 * pout
        return total

    @property
    def cost_usd(self) -> float:
        """今回実際に API で使った費用."""
        return self._cost(self.by_model)

    @property
    def estimated_cost_usd(self) -> float:
        """キャッシュがなかった場合の費用."""
        return self.cost_usd + self._cost(self.cached_by_model)


class LLMDiagnoser:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: str = "medium",
        cache_dir: str | Path = "llm-cache",
        offline: bool = False,
    ):
        self.model = model
        self.effort = effort
        self.cache_dir = Path(cache_dir)
        self.offline = offline
        self.usage = Usage()
        self._client = None

    def _client_or_create(self):
        if self._client is None:
            import anthropic  # 判定にルールだけを使う場合は SDK を不要にする

            self._client = anthropic.Anthropic()
        return self._client

    def _cache_path(self, payload: str) -> Path:
        key = hashlib.sha256(
            json.dumps(
                {
                    "model": self.model,
                    "effort": self.effort,
                    "prompt": PROMPT_VERSION,
                    "input": payload,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:24]
        return self.cache_dir / self.model / f"{key}.json"

    def _call(self, payload: str) -> dict[str, Any]:
        client = self._client_or_create()
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": f"次の Pod を判定してください。\n\n{payload}"}
            ],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": _schema()},
            },
        }
        if self.model in FALLBACK_MODELS:
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params
            )
        else:
            response = client.messages.create(**params)

        usage = response.usage
        self.usage.add(response.model, usage.input_tokens, usage.output_tokens)
        record: dict[str, Any] = {
            "model": response.model,
            "stop_reason": response.stop_reason,
            "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
        }
        if response.stop_reason == "refusal":
            record["result"] = None
        else:
            text = next(b.text for b in response.content if b.type == "text")
            record["result"] = json.loads(text)
        return record

    def diagnose(self, bundle: Bundle) -> list[Finding]:
        payload = json.dumps(compact_bundle(bundle), ensure_ascii=False, sort_keys=True, indent=1)
        path = self._cache_path(payload)
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            u = record["usage"]
            self.usage.add(self.model, u["input_tokens"], u["output_tokens"], cached=True)
        elif self.offline:
            raise LookupError(f"キャッシュがありません (offline): {bundle.name}")
        else:
            record = self._call(payload)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

        result = record["result"]
        if result is None:
            return [Finding(Category.UNKNOWN, "LLM が判定を拒否しました", confidence=0.0)]
        return [
            Finding(
                category=result["category"],
                title=result["title"],
                confidence=float(result["confidence"]),
                subcause=result["subcause"],
                evidence=[Evidence(e["source"], e["text"]) for e in result["evidence"]],
                actions=result["actions"],
                commands=[f"kubectl -n {bundle.namespace} describe pod {bundle.name}"],
            )
        ]


def needs_llm(findings: list[Finding]) -> bool:
    """ルールの判定が不十分なときだけ LLM に回す (hybrid 用)."""
    main = findings[0]
    if main.category == Category.UNKNOWN:
        return True
    return main.category in SUBCAUSES and main.subcause is None


class HybridDiagnoser:
    """ルールを先に実行し、取りこぼしたケースだけ LLM で判定する."""

    def __init__(self, llm: LLMDiagnoser):
        self.llm = llm

    @property
    def usage(self) -> Usage:
        return self.llm.usage

    def diagnose(self, bundle: Bundle) -> list[Finding]:
        from kube_triage.rules import diagnose as rule_diagnose

        findings = rule_diagnose(bundle)
        if not needs_llm(findings):
            return findings
        return self.llm.diagnose(bundle) + findings
