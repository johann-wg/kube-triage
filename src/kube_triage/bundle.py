"""kubectl の出力 (Pod / Event / ログ) をまとめた「バンドル」."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Bundle:
    """1 つの Pod を診断するための入力一式.

    - pod: `kubectl get pod <name> -o json` の結果
    - events: `kubectl get events -o json` の items
    - logs: コンテナ名 -> ログ本文 (再起動している場合は --previous のログ)
    """

    pod: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    logs: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Bundle:
        return cls(
            pod=data["pod"],
            events=data.get("events", []),
            logs=data.get("logs", {}),
        )

    @classmethod
    def load(cls, path: str | Path) -> Bundle:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def name(self) -> str:
        return self.pod.get("metadata", {}).get("name", "<unknown>")

    @property
    def namespace(self) -> str:
        return self.pod.get("metadata", {}).get("namespace", "default")

    @property
    def phase(self) -> str:
        return self.pod.get("status", {}).get("phase", "Unknown")

    def container_statuses(self) -> list[dict[str, Any]]:
        return self.pod.get("status", {}).get("containerStatuses", [])

    def conditions(self) -> dict[str, dict[str, Any]]:
        return {c["type"]: c for c in self.pod.get("status", {}).get("conditions", [])}

    def container_spec(self, name: str) -> dict[str, Any]:
        for c in self.pod.get("spec", {}).get("containers", []):
            if c.get("name") == name:
                return c
        return {}

    def pod_events(self) -> list[dict[str, Any]]:
        """この Pod に紐づくイベントだけを返す."""
        result = []
        for e in self.events:
            obj = e.get("involvedObject", {})
            if obj.get("name", self.name) == self.name:
                result.append(e)
        return result
