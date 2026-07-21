"""
Risk Correlation Engine - FastAPI app.

Polls MongoDB every RISK_POLL_INTERVAL_SECONDS, computes each zone's
compound risk score (engine/risk_rules.py) and the naive single-sensor
baseline flag (engine/baseline.py), persists both to `zone_risk`, and
pushes live updates over a WebSocket.

When a zone transitions into RED (edge-triggered, not on every tick while
it stays RED), a doc is inserted into `red_events`. That collection is the
integration point for the RAG agent (/rag) and Alert Orchestrator
(/orchestrator), which watch it independently -- the engine itself has no
direct dependency on either.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware

from baseline import naive_baseline_flag
from config import ENGINE_HOST, ENGINE_PORT, POLL_INTERVAL_SECONDS, get_db, load_thresholds, load_zones
from risk_rules import classify, compute_compound_score

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("engine")


async def build_zone_snapshot(db, zone: dict, thresholds: dict, now: datetime) -> dict:
    zid = zone["zone_id"]

    latest_reading = await db.sensor_readings.find_one(
        {"zone_id": zid}, sort=[("timestamp", -1)]
    )
    active_permits = await db.permits.find({"zone_id": zid, "status": "active"}).to_list(50)
    active_maintenance = await db.maintenance_jobs.find({"zone_id": zid, "active": True}).to_list(20)
    recent_shift_events = await db.shift_logs.find({"zone_id": zid}).sort("timestamp", -1).to_list(10)

    result = compute_compound_score(
        latest_reading, active_permits, active_maintenance, recent_shift_events, thresholds, now
    )
    band = classify(result["score"], thresholds)
    baseline_flag = naive_baseline_flag(latest_reading, thresholds)

    return {
        "zone_id": zid,
        "zone_name": zone["name"],
        "hazard_class": zone["hazard_class"],
        "score": result["score"],
        "band": band,
        "triggers": result["triggers"],
        "baseline_flag": baseline_flag,
        "latest_reading": _strip_id(latest_reading),
        "active_permits": [_strip_id(p) for p in active_permits],
        "active_maintenance": [_strip_id(m) for m in active_maintenance],
        "timestamp": now,
    }


def _strip_id(doc):
    if doc is None:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


async def ensure_indexes(db):
    await db.sensor_readings.create_index([("zone_id", 1), ("timestamp", -1)])
    await db.permits.create_index([("zone_id", 1), ("status", 1)])
    await db.maintenance_jobs.create_index([("zone_id", 1), ("active", 1)])
    await db.shift_logs.create_index([("zone_id", 1), ("timestamp", -1)])
    await db.zone_risk.create_index([("zone_id", 1), ("timestamp", -1)])
    await db.red_events.create_index([("zone_id", 1), ("timestamp", -1)])


async def risk_poll_loop(app: FastAPI):
    db = app.state.db
    zones = app.state.zones
    thresholds = app.state.thresholds
    last_band: dict[str, str] = {}

    while True:
        try:
            now = datetime.now(timezone.utc)
            snapshots = []
            for zone in zones:
                snap = await build_zone_snapshot(db, zone, thresholds, now)
                snapshots.append(snap)

                await db.zone_risk.insert_one(
                    {
                        "zone_id": snap["zone_id"],
                        "score": snap["score"],
                        "band": snap["band"],
                        "triggers": snap["triggers"],
                        "baseline_flag": snap["baseline_flag"],
                        "timestamp": snap["timestamp"],
                    }
                )

                previous = last_band.get(snap["zone_id"], "GREEN")
                if snap["band"] == "RED" and previous != "RED":
                    log.info("RED triggered for %s: %s", snap["zone_id"], snap["triggers"])
                    await db.red_events.insert_one(
                        {
                            "zone_id": snap["zone_id"],
                            "zone_name": snap["zone_name"],
                            "score": snap["score"],
                            "triggers": snap["triggers"],
                            "timestamp": snap["timestamp"],
                        }
                    )
                last_band[snap["zone_id"]] = snap["band"]

            await broadcast(app, {"type": "risk_update", "zones": snapshots})
        except Exception:
            log.exception("risk_poll_loop iteration failed")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def broadcast(app: FastAPI, message: dict):
    payload = jsonable_encoder(message)
    dead = set()
    for ws in app.state.ws_connections:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.add(ws)
    app.state.ws_connections -= dead


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = get_db()
    app.state.zones = load_zones()
    app.state.thresholds = load_thresholds()
    app.state.ws_connections = set()

    await ensure_indexes(app.state.db)
    task = asyncio.create_task(risk_poll_loop(app))
    log.info("Risk engine started. Polling every %ss across %d zones.", POLL_INTERVAL_SECONDS, len(app.state.zones))
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Industrial Safety Risk Correlation Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/zones")
async def get_zones():
    return app.state.zones


@app.get("/risk/live")
async def get_risk_live():
    now = datetime.now(timezone.utc)
    snapshots = [
        await build_zone_snapshot(app.state.db, zone, app.state.thresholds, now)
        for zone in app.state.zones
    ]
    return snapshots


@app.get("/risk/history/{zone_id}")
async def get_risk_history(zone_id: str, limit: int = 500):
    docs = (
        await app.state.db.zone_risk.find({"zone_id": zone_id})
        .sort("timestamp", -1)
        .limit(limit)
        .to_list(limit)
    )
    docs = [_strip_id(d) for d in docs]
    docs.reverse()  # oldest -> newest
    return docs


@app.websocket("/ws/risk")
async def ws_risk(websocket: WebSocket):
    await websocket.accept()
    app.state.ws_connections.add(websocket)
    try:
        now = datetime.now(timezone.utc)
        snapshots = [
            await build_zone_snapshot(app.state.db, zone, app.state.thresholds, now)
            for zone in app.state.zones
        ]
        await websocket.send_json(jsonable_encoder({"type": "risk_update", "zones": snapshots}))
        while True:
            # We don't expect inbound messages; this just keeps the
            # connection open and lets us detect disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        app.state.ws_connections.discard(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=ENGINE_HOST, port=ENGINE_PORT, reload=False)
