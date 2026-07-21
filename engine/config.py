"""Shared config/loading helpers for the engine service."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

ZONES_PATH = REPO_ROOT / "simulator" / "zones.json"
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


POLL_INTERVAL_SECONDS = float(os.environ.get("RISK_POLL_INTERVAL_SECONDS", "2.5"))
ENGINE_HOST = os.environ.get("ENGINE_HOST", "0.0.0.0")
ENGINE_PORT = int(os.environ.get("ENGINE_PORT", "8000"))
