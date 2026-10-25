from pathlib import Path

import pytest

from kube_triage.bundle import Bundle

SCENARIOS = Path(__file__).resolve().parent.parent / "scenarios"


@pytest.fixture
def load():
    def _load(name: str) -> Bundle:
        return Bundle.load(SCENARIOS / f"{name}.json")

    return _load
