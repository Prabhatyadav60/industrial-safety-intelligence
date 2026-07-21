"""
Naive single-sensor baseline, for comparison against the compound engine.

This is deliberately dumb: it only looks at one sensor value against its
published high-alarm threshold, exactly what a conventional gas-detector
panel would do. It never looks at permits, maintenance, or shift state.
It exists so eval.py can measure how much earlier the compound engine
catches the same incident.
"""


def naive_baseline_flag(latest_reading: dict | None, thresholds: dict) -> bool:
    """True if gas_pct_lel alone has crossed the published high-alarm threshold."""
    if latest_reading is None:
        return False
    return latest_reading.get("gas_pct_lel", 0.0) >= thresholds["gas"]["high_alarm_pct_lel"]
