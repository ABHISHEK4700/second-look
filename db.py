"""SQLite storage: payments, known recipients, event log, chat history."""
import json
import sqlite3
import threading
from contextlib import contextmanager

import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    conversation_id TEXT,
    recipient_name TEXT NOT NULL,
    recipient_email TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    purpose TEXT,
    relationship TEXT,
    ai_risk INTEGER NOT NULL,
    rule_risk INTEGER NOT NULL,
    risk INTEGER NOT NULL,
    risk_level TEXT NOT NULL,          -- low | medium | high
    red_flags TEXT NOT NULL,           -- JSON list
    ai_summary TEXT,                   -- written for the guardian
    call_script TEXT,                  -- what the guardian could say
    screenshot TEXT,                   -- data URL of evidence image, optional
    status TEXT NOT NULL,              -- awaiting_payment | held | sent | blocked | cancelled | failed
    decision_by TEXT,
    decision_note TEXT,
    paypal_order_id TEXT,
    paypal_authorization_id TEXT,
    paypal_capture_id TEXT,
    paypal_payout_batch_id TEXT,
    payout_status TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipients (
    email TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    times_paid INTEGER NOT NULL DEFAULT 0,
    note TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id TEXT,
    kind TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    history TEXT NOT NULL,
    last_image TEXT,
    updated_at TEXT NOT NULL
);
"""

SEED_RECIPIENTS = [
    ("daniel@example.com", "Daniel (son)", 14, "family"),
    ("rosa.neighbor@example.com", "Rosa (neighbour)", 3, "pays her back for groceries"),
    ("billing@citywater.example.com", "City Water Utility", 22, "monthly bill"),
]


def connect():
    conn = sqlite3.connect(config.DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def tx():
    with _lock:
        conn = connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init():
    with tx() as conn:
        conn.executescript(SCHEMA)
        for email, name, times, note in SEED_RECIPIENTS:
            conn.execute(
                "INSERT OR IGNORE INTO recipients (email, name, times_paid, note) VALUES (?,?,?,?)",
                (email, name, times, note),
            )


def row(r):
    return dict(r) if r is not None else None


def load_conversation(conv_id):
    with tx() as conn:
        r = conn.execute("SELECT history, last_image FROM conversations WHERE id=?", (conv_id,)).fetchone()
    if not r:
        return [], None
    return json.loads(r["history"]), r["last_image"]


def save_conversation(conv_id, history, last_image, now_iso):
    with tx() as conn:
        conn.execute(
            "INSERT INTO conversations (id, history, last_image, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET history=excluded.history, last_image=excluded.last_image, "
            "updated_at=excluded.updated_at",
            (conv_id, json.dumps(history), last_image, now_iso),
        )
