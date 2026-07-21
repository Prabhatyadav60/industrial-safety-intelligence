"""
Alert Orchestrator.

Watches the `red_events` collection the risk engine writes to (see
engine/main.py -- one doc per GREEN/YELLOW -> RED transition). For each
new event it:

  (a) logs a structured alert
  (b) simulates multi-channel notification (no real SMS/email integration)
  (c) calls the RAG safety-knowledge agent for an explanation + citation
  (d) auto-drafts a preliminary incident report with an LLM call

...and records the full "risk detected -> report drafted" timeline so we
can show chaos-to-coordinated-response latency in the demo.
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "rag"))
load_dotenv(REPO_ROOT / ".env")

from query_agent import query_safety_agent  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("orchestrator")

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
POLL_INTERVAL_SECONDS = 2.0

NOTIFICATION_CHANNELS = [
    "Plant Safety Control Room (Dashboard alert)",
    "Shift Supervisor (SMS)",
    "Site Safety Officer (SMS)",
    "Emergency Response Team (Pager)",
]


def get_db():
    uri = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")
    db_name = os.environ.get("MONGODB_DB", "industrial_safety")
    return AsyncIOMotorClient(uri)[db_name]


def load_zones() -> list[dict]:
    return json.loads((REPO_ROOT / "simulator" / "zones.json").read_text())


def simulate_notifications(zone_name: str) -> list[str]:
    return [f"{channel} -- notified re: {zone_name}" for channel in NOTIFICATION_CHANNELS]


async def draft_incident_report(zone_snapshot: dict, rag_result: dict, detected_at: datetime, client: AsyncAnthropic) -> str:
    prompt = f"""Draft a preliminary incident report in the style of a regulatory safety
incident report, based on the following detected compound risk. Use exactly these section
headers: Zone, Time Detected, Conditions Detected, Regulation Matched, Similar Past Incident,
Recommended Immediate Action. Be concise and factual -- this is a preliminary auto-draft for
a human safety officer to review and correct, not a final report.

Zone: {zone_snapshot['zone_name']} ({zone_snapshot['zone_id']}), hazard class: {zone_snapshot['hazard_class']}
Time detected (UTC): {detected_at.isoformat()}
Compound risk score: {zone_snapshot['score']}/100
Triggered conditions: {"; ".join(zone_snapshot['triggers'])}
Safety analysis: {rag_result.get('explanation', 'n/a')}
Cited regulation: {rag_result.get('cited_regulation', 'n/a')}
Similar past incident: {rag_result.get('similar_past_incident') or 'none identified'}
"""
    response = await client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


async def handle_red_event(db, zones: list[dict], event: dict, client: AsyncAnthropic):
    t0 = datetime.now(timezone.utc)
    zone = next((z for z in zones if z["zone_id"] == event["zone_id"]), None)
    zone_snapshot = {
        "zone_id": event["zone_id"],
        "zone_name": event.get("zone_name") or (zone["name"] if zone else event["zone_id"]),
        "hazard_class": zone["hazard_class"] if zone else "unknown",
        "score": event["score"],
        "band": "RED",
        "triggers": event["triggers"],
    }

    alert_id = str(uuid.uuid4())
    notifications = simulate_notifications(zone_snapshot["zone_name"])
    for n in notifications:
        log.info("[ALERT %s] %s", alert_id[:8], n)
    t_alert_logged = datetime.now(timezone.utc)

    rag_result, report = None, None
    try:
        rag_result = await query_safety_agent(zone_snapshot, client=client)
        t_rag = datetime.now(timezone.utc)
        report = await draft_incident_report(zone_snapshot, rag_result, event["timestamp"], client)
        t_report = datetime.now(timezone.utc)
    except Exception as exc:
        log.exception("RAG/report generation failed for zone %s", event["zone_id"])
        t_rag = t_report = datetime.now(timezone.utc)
        rag_result = rag_result or {"error": str(exc)}
        report = report or f"[report generation failed: {exc}]"

    timeline = {
        "risk_detected_at": event["timestamp"],
        "alert_logged_at": t_alert_logged,
        "rag_explanation_at": t_rag,
        "report_drafted_at": t_report,
        "total_response_seconds": (t_report - t0).total_seconds(),
    }

    alert_doc = {
        "alert_id": alert_id,
        "zone_id": zone_snapshot["zone_id"],
        "zone_name": zone_snapshot["zone_name"],
        "score": zone_snapshot["score"],
        "triggers": zone_snapshot["triggers"],
        "notifications": notifications,
        # Keyed as "rag" (not "rag_result") to match dashboard/app.js's
        # pollOrchestratorAlerts(), which reads doc.rag directly.
        "rag": rag_result,
        "incident_report": report,
        "timeline": timeline,
        # Top-level timestamp mirrors the risk-detection time so the
        # dashboard can match this alert to the client-side RED entry it
        # already pushed (it matches on zone_id + timestamp proximity).
        "timestamp": event["timestamp"],
        "created_at": t0,
    }
    await db.alerts.insert_one(alert_doc)

    log.info(
        "Alert %s for %s: chaos -> coordinated response in %.2fs",
        alert_id[:8],
        zone_snapshot["zone_name"],
        timeline["total_response_seconds"],
    )
    return alert_doc


async def orchestrator_loop():
    db = get_db()
    zones = load_zones()
    client = AsyncAnthropic()
    await db.alerts.create_index([("zone_id", 1), ("created_at", -1)])

    log.info("Alert orchestrator started. Watching red_events every %ss.", POLL_INTERVAL_SECONDS)
    try:
        while True:
            pending = (
                await db.red_events.find({"orchestrated": {"$ne": True}})
                .sort("timestamp", 1)
                .to_list(50)
            )
            for event in pending:
                await handle_red_event(db, zones, event, client)
                await db.red_events.update_one({"_id": event["_id"]}, {"$set": {"orchestrated": True}})
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(orchestrator_loop())
