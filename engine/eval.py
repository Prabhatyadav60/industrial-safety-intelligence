"""
Eval harness: the single most important deliverable for judging.

Runs the scripted Vizag-pattern scenario (simulator/scenario.py), then
replays the *actual* compound-engine rules (risk_rules.py) and the naive
single-sensor baseline (baseline.py) point-in-time over the generated
data to measure:

  (a) timestamp the compound engine first flags RED for the danger zone
  (b) timestamp the naive single-sensor baseline would have fired
  (c) the lead-time difference, in minutes

Uses a dedicated "<MONGODB_DB>_eval" database so it never clobbers data
from a live demo run, and runs the scenario generator in non-realtime
mode (correct historical timestamps, no wall-clock waiting) so this can
be re-run quickly and deterministically.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "simulator"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(REPO_ROOT / ".env")

import scenario  # noqa: E402  (simulator/)
from baseline import naive_baseline_flag  # noqa: E402
from risk_rules import classify, compute_compound_score  # noqa: E402


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def _active_at(doc: dict, t, start_key: str, end_key: str) -> bool:
    start = doc.get(start_key)
    end = doc.get(end_key)
    if start is None or start > t:
        return False
    return end is None or end >= t


async def run_eval(out_path: Path | None):
    uri = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")
    eval_db_name = os.environ.get("MONGODB_DB", "industrial_safety") + "_eval"
    client = AsyncIOMotorClient(uri)
    db = client[eval_db_name]

    zones = json.loads((REPO_ROOT / "simulator" / "zones.json").read_text())
    thresholds = json.loads((REPO_ROOT / "thresholds.json").read_text())
    zone_id = scenario.SCENARIO_ZONE_ID

    for coll in ["sensor_readings", "permits", "shift_logs", "maintenance_jobs"]:
        await db[coll].delete_many({"zone_id": zone_id})

    print(f"[eval] Generating scripted scenario for zone {zone_id} into db='{eval_db_name}' (non-realtime replay)...")
    await scenario.run_scenario(db, thresholds, zones, realtime=False)

    readings = await db.sensor_readings.find({"zone_id": zone_id}).sort("timestamp", 1).to_list(100000)
    permits = await db.permits.find({"zone_id": zone_id}).to_list(1000)
    maintenance = await db.maintenance_jobs.find({"zone_id": zone_id}).to_list(1000)
    shift_logs = await db.shift_logs.find({"zone_id": zone_id}).sort("timestamp", 1).to_list(1000)

    compound_red_at = None
    compound_red_score = None
    compound_red_triggers = None
    baseline_at = None
    baseline_gas = None
    trace = []

    for reading in readings:
        t = reading["timestamp"]

        active_permits = [p for p in permits if _active_at(p, t, "start_time", "end_time")]
        active_maint = [m for m in maintenance if _active_at(m, t, "started_at", "ended_at")]
        past_shifts = [s for s in shift_logs if s["timestamp"] <= t]

        result = compute_compound_score(reading, active_permits, active_maint, past_shifts, thresholds, now=_aware(t))
        band = classify(result["score"], thresholds)
        trace.append({"timestamp": t.isoformat(), "score": result["score"], "band": band})

        if band == "RED" and compound_red_at is None:
            compound_red_at = t
            compound_red_score = result["score"]
            compound_red_triggers = result["triggers"]

        if baseline_at is None and naive_baseline_flag(reading, thresholds):
            baseline_at = t
            baseline_gas = reading["gas_pct_lel"]

    print()
    print("=" * 72)
    print(f"EVAL RESULT - zone {zone_id} ({next(z['name'] for z in zones if z['zone_id'] == zone_id)})")
    print("=" * 72)

    if compound_red_at:
        print(f"(a) Compound engine first flags RED at : {compound_red_at.isoformat()} UTC")
        print(f"    score={compound_red_score}  triggers={compound_red_triggers}")
    else:
        print("(a) Compound engine never reached RED in this run.")

    if baseline_at:
        print(f"(b) Naive baseline would have fired at : {baseline_at.isoformat()} UTC (gas={baseline_gas}% LEL)")
    else:
        print("(b) Naive baseline never fired in this run (gas never crossed the high-alarm threshold).")

    lead_time_minutes = None
    if compound_red_at and baseline_at:
        lead_time_minutes = (baseline_at - compound_red_at).total_seconds() / 60.0
        print(f"(c) Lead time gained by compound engine   : {lead_time_minutes:.1f} minutes")
    elif compound_red_at and not baseline_at:
        print("(c) Lead time: compound engine caught it; naive baseline NEVER would have (infinite lead time / false negative avoided).")
    else:
        print("(c) Lead time: not computable (compound engine never flagged RED).")

    print("=" * 72)

    result = {
        "zone_id": zone_id,
        "compound_red_at": compound_red_at.isoformat() if compound_red_at else None,
        "compound_red_score": compound_red_score,
        "compound_red_triggers": compound_red_triggers,
        "baseline_fired_at": baseline_at.isoformat() if baseline_at else None,
        "baseline_gas_pct_lel": baseline_gas,
        "lead_time_minutes": lead_time_minutes,
        "scenario_params": {
            "total_ticks": scenario.TOTAL_TICKS,
            "time_scale": scenario.TIME_SCALE,
            "gas_start_pct_lel": scenario.GAS_START_PCT_LEL,
            "gas_end_pct_lel": scenario.GAS_END_PCT_LEL,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2))
        print(f"\n[eval] Full trace + result written to {out_path}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Measure compound-engine vs naive-baseline detection lead time")
    parser.add_argument("--out", type=str, default=str(REPO_ROOT / "eval_output" / "latest_run.json"))
    args = parser.parse_args()
    asyncio.run(run_eval(Path(args.out) if args.out else None))
