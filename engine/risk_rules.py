"""
Compound risk correlation rules.

Individually-normal signals (an active permit, a slightly-elevated gas
reading, a routine shift changeover) are each fine on their own. This
module looks for the *combinations* that a single-sensor threshold would
never catch -- the pattern behind the Vizag GCP incident.

Each rule below is independent and additive ("stacking"): a zone can
trigger any subset of them at once, and the score is the sum of the
weights for whichever rules are currently true, capped at 100.
"""

from datetime import datetime, timezone


def _abs_pressure_deviation(pressure_kpa: float, thresholds: dict) -> float:
    return abs(pressure_kpa - thresholds["pressure"]["ambient_baseline_kpa"])


def compute_compound_score(
    latest_reading: dict | None,
    active_permits: list[dict],
    active_maintenance: list[dict],
    recent_shift_events: list[dict],
    thresholds: dict,
    now: datetime | None = None,
) -> dict:
    """Compute the live compound risk score (0-100) for one zone.

    Args:
        latest_reading: most recent sensor_readings doc for the zone, or None.
        active_permits: permits docs with status == "active" for the zone.
        active_maintenance: maintenance_jobs docs with active == True for the zone.
        recent_shift_events: shift_logs docs for the zone (any recent window;
            recency filtering against changeover_recency_window_seconds happens here).
        thresholds: parsed thresholds.json.

    Returns:
        {"score": int, "triggers": [str, ...]} -- triggers lists which rules fired.
    """
    now = now or datetime.now(timezone.utc)
    weights = thresholds["risk_engine"]["rule_weights"]
    triggers: list[str] = []
    score = 0

    if latest_reading is None:
        return {"score": 0, "triggers": triggers}

    permit_types = {p["type"] for p in active_permits}

    # Rule 1: active hot-work permit + gas rising past a fraction of the
    # high-alarm threshold (well before the high alarm itself would fire).
    gas_pct_lel = latest_reading.get("gas_pct_lel", 0.0)
    high_alarm = thresholds["gas"]["high_alarm_pct_lel"]
    compound_gas_trigger = thresholds["gas"]["compound_trigger_pct_of_high_alarm"] * high_alarm
    if "hot_work" in permit_types and gas_pct_lel > compound_gas_trigger:
        score += weights["hot_work_gas_combo"]
        triggers.append(
            f"hot_work_permit_active + gas {gas_pct_lel:.1f}% LEL > {compound_gas_trigger:.1f}% LEL trigger"
        )

    # Rule 2: active confined-space permit + abnormal pressure (e.g. a
    # ventilation/extraction fault building up in an enclosed space).
    pressure_kpa = latest_reading.get("pressure_kpa")
    if "confined_space" in permit_types and pressure_kpa is not None:
        deviation = _abs_pressure_deviation(pressure_kpa, thresholds)
        if deviation > thresholds["pressure"]["abnormal_deviation_kpa"]:
            score += weights["confined_space_pressure_combo"]
            triggers.append(
                f"confined_space_permit_active + pressure deviation {deviation:.1f} kPa "
                f"> {thresholds['pressure']['abnormal_deviation_kpa']} kPa"
            )

    # Rule 3: active maintenance job + a recent shift changeover (loss of
    # continuity right when a job is mid-flight).
    window = thresholds["risk_engine"]["changeover_recency_window_seconds"]
    recent_changeover = any(
        e.get("event") == "changeover" and (now - _as_aware(e["timestamp"])).total_seconds() <= window
        for e in recent_shift_events
    )
    if active_maintenance and recent_changeover:
        score += weights["maintenance_changeover_combo"]
        triggers.append(
            f"active_maintenance + shift changeover within last {window // 60} min"
        )

    return {"score": min(score, 100), "triggers": triggers}


def _as_aware(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def classify(score: int, thresholds: dict) -> str:
    """RED / YELLOW / GREEN band for a score."""
    if score >= thresholds["risk_engine"]["red_threshold"]:
        return "RED"
    if score >= thresholds["risk_engine"]["yellow_threshold"]:
        return "YELLOW"
    return "GREEN"
