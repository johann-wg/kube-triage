"""診断結果のデータモデル."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


class Category:
    """障害カテゴリ. 評価データの expected.category と同じ文字列を使う."""

    HEALTHY = "HEALTHY"
    OOM_KILLED = "OOM_KILLED"
    IMAGE_PULL = "IMAGE_PULL"
    CONFIG_ERROR = "CONFIG_ERROR"
    UNSCHEDULABLE = "UNSCHEDULABLE"
    PROBE_FAILURE = "PROBE_FAILURE"
    CRASH_LOOP = "CRASH_LOOP"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Evidence:
    """判定根拠. どのフィールド / イベント / ログ行から判断したかを残す."""

    source: str
    text: str


@dataclass
class Finding:
    category: str
    title: str
    confidence: float
    subcause: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
