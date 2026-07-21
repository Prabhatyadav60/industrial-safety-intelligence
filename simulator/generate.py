"""
Background data simulator for the Industrial Safety Intelligence platform.

Writes realistic-range sensor_readings, permits, shift_logs, and
maintenance_jobs to MongoDB every few seconds. Two modes:

  --mode=random    background "normal plant noise" across all zones
  --mode=scenario   the scripted Vizag-pattern compound-risk incident
                    (delegates to scenario.run_scenario)

Both can run concurrently (in separate processes) as long as --mode=random
excludes the zone(s) that --mode=scenario is driving, via --exclude-zones.
"""

import argparse
import asyncio
import json
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import os

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

ZONES_PATH = Path(__file__).resolve().parent / "zones.json"
THRESHOLDS_PATH = REPO_ROOT / "thresholds.json"


def load_zones() -> list[dict]:
    return json.loads(ZONES_PATH.read_text())


def load_thresholds() -> dict:
    return json.loads(THRESHOLDS_PATH.read_text())


def get_db():
    uri = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")
    db_name = os.environ.get("MONGODB_DB", "industrial_safety")
    client = AsyncIOMotorClient(uri)
    return client[db_name]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def gen_reading(zone: dict, thresholds: dict) -> dict:
    """One noisy-but-realistic sensor reading for a zone, drawn from its
    hazard-class baseline (see thresholds.json -> hazard_class_baseline)."""
    hc = thresholds["hazard_class_baseline"][zone["hazard_class"]]
    gas_pct_lel = max(0.0, random.gauss(hc["gas_pct_lel_mean"], hc["gas_pct_lel_std"]))
    temp_celsius = max(15.0, random.gauss(hc["temp_mean"], hc["temp_std"]))
    pressure_kpa = random.gauss(
        thresholds["pressure"]["ambient_baseline_kpa"], hc["pressure_std_kpa"]
    )
    return {
        "zone_id": zone["zone_id"],
        "gas_pct_lel": round(gas_pct_lel, 2),
        "temp_celsius": round(temp_celsius, 1),
        "pressure_kpa": round(pressure_kpa, 2),
        "timestamp": now_utc(),
    }


class RandomNoiseSimulator:
    """Background 'plant is operating normally' generator.

    Occasionally opens/closes permits, logs shift changeovers, and toggles
    maintenance jobs, in addition to a steady stream of sensor readings.
    """

    def __init__(self, db, zones: list[dict], thresholds: dict):
        self.db = db
        self.zones = zones
        self.thresholds = thresholds
        self.open_permits: dict[str, list[dict]] = {z["zone_id"]: [] for z in zones}
        self.active_maintenance: dict[str, dict | None] = {z["zone_id"]: None for z in zones}

    async def tick(self):
        for zone in self.zones:
            zid = zone["zone_id"]
            reading = gen_reading(zone, self.thresholds)
            await self.db.sensor_readings.insert_one(reading)

            # ~3% chance per tick to open a short-lived permit
            if random.random() < 0.03 and not self.open_permits[zid]:
                permit_type = random.choice(["hot_work", "confined_space", "general"])
                permit = {
                    "permit_id": str(uuid.uuid4()),
                    "zone_id": zid,
                    "type": permit_type,
                    "status": "active",
                    "start_time": now_utc(),
                    "end_time": None,
                    "description": f"Routine {permit_type} permit",
                }
                await self.db.permits.insert_one(permit)
                self.open_permits[zid].append(permit)

            # ~2% chance per tick to close an open permit
            for permit in list(self.open_permits[zid]):
                if random.random() < 0.02:
                    await self.db.permits.update_one(
                        {"permit_id": permit["permit_id"]},
                        {"$set": {"status": "closed", "end_time": now_utc()}},
                    )
                    self.open_permits[zid].remove(permit)

            # ~1% chance per tick to log a shift changeover
            if random.random() < 0.01:
                await self.db.shift_logs.insert_one(
                    {
                        "zone_id": zid,
                        "event": "changeover",
                        "timestamp": now_utc(),
                        "note": "Routine shift handover",
                    }
                )

            # ~1.5% chance to toggle a maintenance job on/off
            if self.active_maintenance[zid] is None and random.random() < 0.015:
                job = {
                    "job_id": str(uuid.uuid4()),
                    "zone_id": zid,
                    "active": True,
                    "description": "Routine equipment maintenance",
                    "started_at": now_utc(),
                }
                await self.db.maintenance_jobs.insert_one(job)
                self.active_maintenance[zid] = job
            elif self.active_maintenance[zid] is not None and random.random() < 0.02:
                job = self.active_maintenance[zid]
                await self.db.maintenance_jobs.update_one(
                    {"job_id": job["job_id"]}, {"$set": {"active": False}}
                )
                self.active_maintenance[zid] = None

    async def run(self, interval_seconds: float):
        print(f"[simulator] random mode: {len(self.zones)} zone(s), every {interval_seconds}s")
        while True:
            await self.tick()
            await asyncio.sleep(interval_seconds)


async def main():
    parser = argparse.ArgumentParser(description="Industrial safety data simulator")
    parser.add_argument("--mode", choices=["random", "scenario"], default="random")
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between ticks (random mode)")
    parser.add_argument(
        "--exclude-zones",
        type=str,
        default="",
        help="Comma-separated zone_ids to skip (e.g. the zone a concurrent --mode=scenario run owns)",
    )
    args = parser.parse_args()

    db = get_db()
    zones = load_zones()
    thresholds = load_thresholds()
    excluded = {z.strip() for z in args.exclude_zones.split(",") if z.strip()}

    if args.mode == "random":
        active_zones = [z for z in zones if z["zone_id"] not in excluded]
        sim = RandomNoiseSimulator(db, active_zones, thresholds)
        await sim.run(args.interval)
    else:
        import scenario

        await scenario.run_scenario(db, thresholds, zones)


if __name__ == "__main__":
    asyncio.run(main())
