"""
Scripted compound-risk incident, modeled on the Vizag Steel Plant (RINL)
Gas Cleaning Plant pattern (Jan 2025): a confined-space entry permit and a
hot-work permit are both active while an extraction-fan maintenance job
degrades ventilation, gas slowly accumulates, pressure drifts abnormal, and
a shift changeover happens mid-way -- none of which alone crosses a
single-sensor alarm threshold, but which together are lethal.

Sim-time is decoupled from wall-clock time (see TIME_SCALE) so a ~3 minute
demo run represents a ~1 hour real-world incident buildup, giving eval.py a
meaningful lead-time-in-minutes number while staying fast to demo live.
"""

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

SCENARIO_ZONE_ID = "Z1"  # Gas Cleaning Plant

TOTAL_TICKS = 90
TICK_SLEEP_SECONDS = 2.0  # wall-clock seconds between writes
TIME_SCALE = 20  # 1 wall-clock second of a tick = 20 simulated seconds

GAS_START_PCT_LEL = 5.0
GAS_END_PCT_LEL = 65.0
GAS_NOISE_STD = 0.4

PRESSURE_DEV_START_KPA = 0.5
PRESSURE_DEV_END_KPA = 6.0
PRESSURE_NOISE_STD = 0.15


def sim_clock(start: datetime, tick: int) -> datetime:
    return start + timedelta(seconds=tick * TICK_SLEEP_SECONDS * TIME_SCALE)


async def run_scenario(db, thresholds: dict, zones: list[dict], realtime: bool = True):
    zone = next(z for z in zones if z["zone_id"] == SCENARIO_ZONE_ID)
    ambient = thresholds["pressure"]["ambient_baseline_kpa"]
    hc = thresholds["hazard_class_baseline"][zone["hazard_class"]]

    sim_start = datetime.now(timezone.utc)
    print(
        f"[scenario] Playing scripted incident in {zone['name']} ({SCENARIO_ZONE_ID}) - "
        f"{TOTAL_TICKS} ticks, {TICK_SLEEP_SECONDS}s/tick, {TIME_SCALE}x sim-time "
        f"(~{TOTAL_TICKS * TICK_SLEEP_SECONDS * TIME_SCALE / 60:.0f} simulated minutes)"
    )

    # t=0: confined-space entry permit AND hot-work permit both issued for
    # repair work inside the gas-cleaning duct.
    confined_permit = {
        "permit_id": str(uuid.uuid4()),
        "zone_id": SCENARIO_ZONE_ID,
        "type": "confined_space",
        "status": "active",
        "start_time": sim_clock(sim_start, 0),
        "end_time": None,
        "description": "Confined-space entry: gas-cleaning duct inspection access",
    }
    hot_work_permit = {
        "permit_id": str(uuid.uuid4()),
        "zone_id": SCENARIO_ZONE_ID,
        "type": "hot_work",
        "status": "active",
        "start_time": sim_clock(sim_start, 0),
        "end_time": None,
        "description": "Hot-work repair: cutting/welding on duct flange",
    }
    await db.permits.insert_many([confined_permit, hot_work_permit])

    # t=0: extraction-fan maintenance job begins (this is *why* ventilation
    # degrades and pressure drifts through the scenario).
    maintenance_job = {
        "job_id": str(uuid.uuid4()),
        "zone_id": SCENARIO_ZONE_ID,
        "active": True,
        "description": "Extraction fan servicing - suspected partial blockage",
        "started_at": sim_clock(sim_start, 0),
    }
    await db.maintenance_jobs.insert_one(maintenance_job)

    changeover_tick = TOTAL_TICKS // 4  # roughly a quarter of the way through
    scenario_log = []
    baseline_fired = False

    for tick in range(TOTAL_TICKS):
        frac = tick / (TOTAL_TICKS - 1)
        ts = sim_clock(sim_start, tick)

        gas_pct_lel = max(
            0.0,
            GAS_START_PCT_LEL
            + (GAS_END_PCT_LEL - GAS_START_PCT_LEL) * frac
            + random.gauss(0, GAS_NOISE_STD),
        )
        pressure_dev = max(
            0.0,
            PRESSURE_DEV_START_KPA
            + (PRESSURE_DEV_END_KPA - PRESSURE_DEV_START_KPA) * frac
            + random.gauss(0, PRESSURE_NOISE_STD),
        )
        # Ventilation blockage -> pressure drops below ambient in the duct.
        pressure_kpa = ambient - pressure_dev
        temp_celsius = hc["temp_mean"] + 0.05 * gas_pct_lel + random.gauss(0, hc["temp_std"] * 0.5)

        reading = {
            "zone_id": SCENARIO_ZONE_ID,
            "gas_pct_lel": round(gas_pct_lel, 2),
            "temp_celsius": round(temp_celsius, 1),
            "pressure_kpa": round(pressure_kpa, 2),
            "timestamp": ts,
        }
        await db.sensor_readings.insert_one(reading)

        if tick == changeover_tick:
            await db.shift_logs.insert_one(
                {
                    "zone_id": SCENARIO_ZONE_ID,
                    "event": "changeover",
                    "timestamp": ts,
                    "note": "Shift handover mid-repair; incoming crew briefed verbally only",
                }
            )
            scenario_log.append((ts, "shift changeover logged"))

        high_alarm = thresholds["gas"]["high_alarm_pct_lel"]
        if reading["gas_pct_lel"] >= high_alarm and not baseline_fired:
            baseline_fired = True
            scenario_log.append(
                (ts, f"naive single-sensor baseline WOULD fire (gas {reading['gas_pct_lel']:.1f}% LEL >= {high_alarm}% LEL)")
            )

        if realtime:
            await asyncio.sleep(TICK_SLEEP_SECONDS)

    # Close everything out so the zone doesn't stay "active" forever for
    # anyone polling after the scenario finishes.
    end_ts = sim_clock(sim_start, TOTAL_TICKS - 1)
    await db.permits.update_many(
        {"permit_id": {"$in": [confined_permit["permit_id"], hot_work_permit["permit_id"]]}},
        {"$set": {"status": "closed", "end_time": end_ts}},
    )
    await db.maintenance_jobs.update_one(
        {"job_id": maintenance_job["job_id"]}, {"$set": {"active": False, "ended_at": end_ts}}
    )

    print("[scenario] Complete. Key sim-time events:")
    for ts, note in scenario_log:
        print(f"    {ts.isoformat()}  {note}")
    print(
        "[scenario] Run engine/eval.py against this window to measure compound-engine "
        "vs. naive-baseline detection lead time."
    )


if __name__ == "__main__":
    import json
    from pathlib import Path

    from dotenv import load_dotenv
    import os
    from motor.motor_asyncio import AsyncIOMotorClient

    REPO_ROOT = Path(__file__).resolve().parents[1]
    load_dotenv(REPO_ROOT / ".env")
    zones = json.loads((Path(__file__).resolve().parent / "zones.json").read_text())
    thresholds = json.loads((REPO_ROOT / "thresholds.json").read_text())
    client = AsyncIOMotorClient(os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017"))
    db = client[os.environ.get("MONGODB_DB", "industrial_safety")]
    asyncio.run(run_scenario(db, thresholds, zones))
