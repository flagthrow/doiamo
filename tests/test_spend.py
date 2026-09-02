"""The daily routing budget.

A loop search spends six to twelve directions calls out of a free allowance of
2000, so an afternoon of testing can empty the day. Discovering that as a 403
from upstream is too late.
"""
import json

import pytest

from backend.spend import DailyBudget


def test_counts_down_from_the_limit(tmp_path):
    budget = DailyBudget(100, str(tmp_path / "b.json"))
    assert budget.remaining() == 100
    assert budget.take(12) is True
    assert budget.remaining() == 88


def test_refuses_rather_than_going_over(tmp_path):
    budget = DailyBudget(20, str(tmp_path / "b.json"))
    assert budget.take(12) is True
    assert budget.take(12) is False       # would reach 24
    assert budget.remaining() == 8
    assert budget.take(8) is True         # exactly fits
    assert budget.remaining() == 0


def test_a_restart_does_not_hand_out_a_fresh_allowance(tmp_path):
    """The upstream service has no idea the process restarted."""
    path = str(tmp_path / "b.json")
    DailyBudget(100, path).take(60)
    assert DailyBudget(100, path).remaining() == 40


def test_a_new_day_resets_it(tmp_path):
    path = tmp_path / "b.json"
    DailyBudget(100, str(path)).take(60)
    saved = json.loads(path.read_text())
    saved["day"] = "2000-01-01"
    path.write_text(json.dumps(saved))
    assert DailyBudget(100, str(path)).remaining() == 100


def test_zero_disables_the_guard(tmp_path):
    budget = DailyBudget(0, str(tmp_path / "b.json"))
    assert budget.take(10_000) is True


def test_works_without_a_file(tmp_path):
    budget = DailyBudget(50, None)
    assert budget.take(50) is True
    assert budget.take(1) is False


def test_a_corrupt_file_does_not_crash(tmp_path):
    path = tmp_path / "b.json"
    path.write_text("{not json")
    assert DailyBudget(100, str(path)).remaining() == 100


@pytest.mark.asyncio
async def test_the_engine_refuses_once_the_budget_is_spent(tmp_path, monkeypatch):
    """A refusal must arrive before the calls are made, not after."""
    from backend import config
    from backend.routing.ors import ORSEngine
    from backend.routing.base import RoutingError

    monkeypatch.setattr(config, "ORS_DAILY_BUDGET", 10)
    monkeypatch.setattr(config, "ORS_BUDGET_FILE", str(tmp_path / "b.json"))
    engine = ORSEngine(api_key="test")

    seeds = [(i, 4) for i in range(12)]
    with pytest.raises(RoutingError) as caught:
        await engine.round_trips(45.4, 9.1, 10000, "running", "asphalt", seeds)
    assert "budget" in str(caught.value).lower()
    assert engine.budget.status()["used"] == 0      # nothing was spent
