"""Тести аналізу в звітах лаби 8 (loadtests/soak_report.py, stress_report.py).

Перевіряють логіку висновків на невеликих штучних даних: витік пам'яті,
плато, деградація, час відновлення, оновлення README між маркерами.
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "loadtests"))

import report_common  # noqa: E402
import soak_report  # noqa: E402
import stress_report  # noqa: E402

TIMES = [i * 0.5 for i in range(0, 121)]  # 60 хвилин, вимір кожні 30 с


def test_constant_memory_is_stable():
    result = soak_report.analyze_series(TIMES, [100.0] * len(TIMES), warmup=5)
    assert result["verdict"] == "стабільно"
    assert result["ok"] is True


def test_steady_growth_is_reported_as_leak():
    values = [100.0 + 1.5 * t for t in TIMES]  # +90 МБ за годину
    result = soak_report.analyze_series(TIMES, values, warmup=5)
    assert result["ok"] is False
    assert "витік" in result["verdict"]
    assert result["slope_per_hour"] == pytest.approx(90.0, rel=0.01)


def test_growth_that_levels_off_is_not_a_leak():
    values = [100.0 + 80.0 * (1 - math.exp(-t / 6.0)) for t in TIMES]
    result = soak_report.analyze_series(TIMES, values, warmup=0)
    assert result["ok"] is True


def test_short_series_is_not_judged():
    result = soak_report.analyze_series([0, 1, 2], [1, 2, 3], warmup=0)
    assert result["ok"] is None


def test_db_memory_growth_with_data_is_explained():
    memory = {"db": {"ok": False, "verdict": "росте весь час (підозра на витік)"}}
    soak_report.explain_data_growth(memory, [100_000, 200_000, 320_000])
    assert memory["db"]["ok"] is True
    assert memory["db"]["explained"] is True
    assert "×3.2" in memory["db"]["verdict"]


def test_db_memory_growth_without_data_growth_stays_a_leak():
    memory = {"db": {"ok": False, "verdict": "росте весь час (підозра на витік)"}}
    soak_report.explain_data_growth(memory, [100_000, 101_000, 102_000])
    assert memory["db"]["ok"] is False


def test_web_memory_growth_is_never_explained_by_data():
    memory = {"web1": {"ok": False, "verdict": "росте весь час (підозра на витік)"}}
    soak_report.explain_data_growth(memory, [100_000, 400_000])
    assert memory["web1"]["ok"] is False


def test_latency_degradation_is_detected():
    minutes = [
        {"minute": m + 0.5, "avg": 20 + m, "p95": 100 + 5 * m, "p99": 150 + 6 * m}
        for m in range(60)
    ]
    assert soak_report.analyze_latency(minutes, warmup=5)["ok"] is False


def test_flat_latency_is_not_degradation():
    minutes = [
        {"minute": m + 0.5, "avg": 20, "p95": 100, "p99": 150} for m in range(60)
    ]
    assert soak_report.analyze_latency(minutes, warmup=5)["ok"] is True


def test_per_minute_uses_cumulative_counters():
    rows = []
    count = 0
    total = 0.0
    for second in range(120):
        count += 10
        # перша хвилина 10 мс, друга 30 мс
        total += 10 * (10 if second < 60 else 30)
        rows.append(
            {
                "ts": 1000 + second,
                "users": 5,
                "rps": 10.0,
                "fps": 0.0,
                "p50": 10.0,
                "p95": 20.0,
                "p99": 30.0,
                "count": count,
                "fails": 0,
                "avg": total / count,
            }
        )
    result = report_common.per_minute(rows, 1000)
    assert [round(m["avg"]) for m in result] == [10, 30]
    assert result[0]["requests"] == 600


def _rows(p95_by_second, errors_by_second=None):
    return [
        {
            "ts": 5000 + s,
            "users": 100,
            "rps": 50.0,
            "fps": 50.0 * (errors_by_second[s] if errors_by_second else 0.0),
            "p95": p95,
            "p99": p95 * 2,
        }
        for s, p95 in enumerate(p95_by_second)
    ]


def test_recovery_time_is_measured_from_drop():
    # 40 с перевантаження, потім норма
    rows = _rows([3000] * 40 + [100] * 120)
    seconds, _ = stress_report.find_recovery(rows, 5000, baseline_p95=100)
    assert seconds == 40


def test_no_recovery_when_latency_stays_high():
    rows = _rows([3000] * 100)
    seconds, _ = stress_report.find_recovery(rows, 5000, baseline_p95=100)
    assert seconds is None


def test_errors_block_recovery():
    errors = [0.5] * 60 + [0.0] * 60
    rows = _rows([100] * 120, errors)
    seconds, _ = stress_report.find_recovery(rows, 5000, baseline_p95=100)
    assert seconds == 60


def test_update_readme_block_replaces_only_marked_text(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "до\n<!-- X:START -->\nстаре\n<!-- X:END -->\nпісля\n", encoding="utf-8"
    )
    report_common.update_readme_block(readme, "X", "нове\n")
    assert (
        readme.read_text(encoding="utf-8")
        == "до\n<!-- X:START -->\nнове\n<!-- X:END -->\nпісля\n"
    )


def test_update_readme_block_requires_markers(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("без маркерів", encoding="utf-8")
    with pytest.raises(SystemExit):
        report_common.update_readme_block(readme, "X", "текст")
