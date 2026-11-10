"""Payment lifecycle + deterministic risk rules.

The AI writes the risk assessment, but the final score is
max(AI score, rule score). Rules catch well-known scam signals, so a
scammer coaching the sender ("tell it this is for your grandson") can't
talk the score down below what the evidence shows.
"""
import json
import re
import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import config
import db
from paypal_client import PayPalError, make_client

paypal = make_client()
TZ = ZoneInfo(config.TIMEZONE)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PaymentError(Exception):
    pass


def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def log(conn, payment_id, kind, detail=""):
    conn.execute("INSERT INTO events (payment_id, kind, detail, created_at) VALUES (?,?,?,?)",
                 (payment_id, kind, detail, now_iso()))


# -- rules -----------------------------------------------------------------
RULES = [
    # (points, label, regex over everything the sender said + purpose)
    (35, "Asked to pay with gift cards", r"gift ?cards?|itunes card|google play card|steam card"),
    (35, "Crypto or Bitcoin involved", r"crypto|bitcoin|\bbtc\b|usdt|wallet address|crypto atm"),
    (30, "Told to keep it secret", r"keep (it|this) (a )?secret|don'?t tell|do not tell|between us|nobody can know"),
    (30, "Told to ignore warnings", r"ignore (the )?(warning|alert)s?|click continue|they said .*warning"),
    (30, "Tech-support or account-hacked story", r"tech(nical)? support|microsoft|apple support|account (was |is |got )?hack|virus|remote access|anydesk|teamviewer"),
    (30, "Government / police / tax threat", r"\birs\b|tax (office|department)|police|arrest|warrant|customs|fine (or|before)|\bcourt\b"),
    (30, "Relative in trouble, urgent money", r"\b(grandson|granddaughter|nephew|niece|son|daughter|grandchild)\b.*\b(jail|bail|accident|hospital|lawyer|trouble|arrested)\b|bail money"),
    (25, "Prize, lottery or inheritance fee", r"lottery|you('ve)? won\b(?!['’]t)|\bprizes?\b|inheritance|claim fee|processing fee"),
    (25, "Online relationship, never met in person",
     r"(dating|romance|boyfriend|girlfriend|fianc|in love|soulmate)[^.]*never met|never met[^.]*(dating|boyfriend|girlfriend|love)"
     r"|met (him|her|them) online|dating (site|app)|boyfriend online|girlfriend online"),
    (25, "Investment with guaranteed returns", r"guaranteed (return|profit)|double (your|my) money|investment opportunity|trading platform"),
    (20, "Refund or overpayment to return", r"overpa(id|yment)|refund (them|back)|sent (me )?too much"),
    (15, "Extreme urgency", r"right now|immediately|urgent|within (the )?(hour|30 minutes)|before (it'?s )?too late|today or"),
    (15, "Stranger contacted them first", r"called me|cold call|texted me|messaged me out of|unknown number|pop-?up"),
]


def rule_assessment(text, amount, recipient_email):
    text = (text or "").lower()
    score, flags = 0, []
    for points, label, pattern in RULES:
        if re.search(pattern, text):
            score += points
            flags.append(label)
    known = known_recipient(recipient_email)
    if not known:
        score += 15
        flags.append("First payment to this recipient")
        if amount >= Decimal("500"):
            score += 25
            flags.append("Large amount to a new recipient")
    return min(score, 100), flags


def level_for(score):
    if score >= 70:
        return "high"
    if score >= config.HOLD_THRESHOLD:
        return "medium"
    return "low"


# -- recipients ------------------------------------------------------------
def known_recipient(email):
    with db.tx() as conn:
        r = conn.execute("SELECT * FROM recipients WHERE email=?", ((email or "").lower(),)).fetchone()
    return db.row(r)


def list_recipients():
    with db.tx() as conn:
        rows = conn.execute("SELECT * FROM recipients ORDER BY times_paid DESC").fetchall()
    return [db.row(r) for r in rows]


# -- lifecycle -------------------------------------------------------------
def get(payment_id):
    with db.tx() as conn:
        r = conn.execute("SELECT * FROM payments WHERE id=?", ((payment_id or "").upper(),)).fetchone()
    p = db.row(r)
    if p:
        p["red_flags"] = json.loads(p["red_flags"])
    return p


def public(p, include_evidence=False):
    if not p:
        return None
    keys = ["id", "recipient_name", "recipient_email", "amount", "currency", "purpose", "relationship",
            "risk", "ai_risk", "rule_risk", "risk_level", "red_flags", "ai_summary", "call_script", "status",
            "decision_by", "decision_note", "payout_status", "created_at", "updated_at"]
    out = {k: p[k] for k in keys}
    if include_evidence:
        out["screenshot"] = p["screenshot"]
        out["paypal"] = {k: p[k] for k in ("paypal_order_id", "paypal_authorization_id",
                                           "paypal_capture_id", "paypal_payout_batch_id")}
    return out


def create_review(conversation_id, conversation_text, screenshot, *, recipient_name, recipient_email, amount,
                  purpose, relationship, ai_risk, red_flags, summary_for_guardian, call_script):
    """Called by the AI after it has talked with the sender. Creates the PayPal order."""
    recipient_name = (recipient_name or "").strip()[:80]
    recipient_email = (recipient_email or "").strip().lower()[:120]
    if not recipient_name:
        raise PaymentError("Recipient name is missing.")
    if not EMAIL_RE.match(recipient_email):
        raise PaymentError("A valid PayPal email for the recipient is needed.")
    try:
        amt = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise PaymentError("Amount must be a number.")
    if amt <= 0 or amt > Decimal(str(config.MAX_AMOUNT)):
        raise PaymentError(f"Amount must be between 0.01 and {config.MAX_AMOUNT:.0f}.")
    try:
        ai_risk = max(0, min(100, int(ai_risk)))
    except (TypeError, ValueError):
        ai_risk = 50
    rule_risk, rule_flags = rule_assessment(f"{conversation_text}\n{purpose}\n{relationship}", amt, recipient_email)
    risk = max(ai_risk, rule_risk)
    flags = []
    for f in list(red_flags or []) + rule_flags:
        f = str(f).strip()[:120]
        if f and f.lower() not in {x.lower() for x in flags}:
            flags.append(f)
    pid = "SL-" + secrets.token_hex(3).upper()
    ts = now_iso()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO payments (id, conversation_id, recipient_name, recipient_email, amount, currency, purpose,"
            " relationship, ai_risk, rule_risk, risk, risk_level, red_flags, ai_summary, call_script, screenshot,"
            " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, conversation_id, recipient_name, recipient_email, str(amt), config.CURRENCY,
             (purpose or "")[:300], (relationship or "")[:200], ai_risk, rule_risk, risk, level_for(risk),
             json.dumps(flags[:10]), (summary_for_guardian or "")[:1200], (call_script or "")[:800],
             screenshot, "awaiting_payment", ts, ts),
        )
        log(conn, pid, "reviewed", f"risk {risk} (AI {ai_risk}, rules {rule_risk})")
    try:
        order_id = paypal.create_order(pid, str(amt), config.CURRENCY,
                                       f"Second Look payment {pid} to {recipient_name}")
    except PayPalError as e:
        _set(pid, status="failed")
        _event(pid, "paypal_error", str(e))
        raise PaymentError("Couldn't start the PayPal payment. Please try again.") from e
    _set(pid, paypal_order_id=order_id)
    _event(pid, "order_created", order_id)
    return {**public(get(pid)), "paypal_order_id": order_id, "will_hold": risk >= config.HOLD_THRESHOLD}


def _set(pid, **fields):
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k}=?" for k in fields)
    with db.tx() as conn:
        conn.execute(f"UPDATE payments SET {cols} WHERE id=?", (*fields.values(), pid))


def _event(pid, kind, detail=""):
    with db.tx() as conn:
        log(conn, pid, kind, detail)


def _deliver(p, decided_by):
    """Capture the authorization and pay the recipient out."""
    try:
        cap = paypal.capture_authorization(p["paypal_authorization_id"])
    except PayPalError as e:
        _event(p["id"], "capture_failed", str(e))
        raise PaymentError("PayPal couldn't complete the payment.") from e
    _set(p["id"], status="sent", paypal_capture_id=cap["capture_id"], decision_by=decided_by)
    _event(p["id"], "captured", cap["capture_id"])
    try:
        po = paypal.payout(p["id"], p["recipient_email"], p["amount"], p["currency"],
                           f"Payment from {config.SENDER_NAME}" + (f": {p['purpose']}" if p["purpose"] else ""))
        _set(p["id"], paypal_payout_batch_id=po["payout_batch_id"], payout_status=po["status"])
        _event(p["id"], "payout_sent", f"{po['payout_batch_id']} {po['status']}")
    except PayPalError as e:
        _set(p["id"], payout_status="FAILED")
        _event(p["id"], "payout_failed", str(e))
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO recipients (email, name, times_paid, note) VALUES (?,?,1,?) "
            "ON CONFLICT(email) DO UPDATE SET times_paid=times_paid+1",
            (p["recipient_email"], p["recipient_name"], "added after a completed payment"),
        )
    return public(get(p["id"]))


def authorize(payment_id, order_id):
    """Sender approved in PayPal. Reserve the money; send now or freeze for the guardian."""
    p = get(payment_id)
    if not p or p["paypal_order_id"] != order_id:
        raise PaymentError("Payment and PayPal order don't match.")
    if p["status"] != "awaiting_payment":
        return public(p)
    try:
        auth = paypal.authorize_order(order_id)
    except PayPalError as e:
        _event(p["id"], "authorize_failed", str(e))
        raise PaymentError("PayPal couldn't authorize the payment.") from e
    _set(p["id"], paypal_authorization_id=auth["authorization_id"])
    _event(p["id"], "authorized", auth["authorization_id"])
    p = get(p["id"])
    if p["risk"] >= config.HOLD_THRESHOLD:
        _set(p["id"], status="held")
        _event(p["id"], "held_for_guardian", f"{config.GUARDIAN_NAME} notified")
        return public(get(p["id"]))
    return _deliver(p, "auto (low risk)")


def guardian_decide(payment_id, decision, note=""):
    p = get(payment_id)
    if not p:
        raise PaymentError("No such payment.")
    if p["status"] != "held":
        raise PaymentError(f"Payment is {p['status']}, not waiting for a decision.")
    if decision == "approve":
        _set(p["id"], decision_note=(note or "")[:300])
        _event(p["id"], "guardian_approved", note)
        return _deliver(get(p["id"]), config.GUARDIAN_NAME)
    if decision == "block":
        try:
            paypal.void_authorization(p["paypal_authorization_id"])
        except PayPalError as e:
            _event(p["id"], "void_failed", str(e))
            raise PaymentError("PayPal couldn't release the hold. Try again.") from e
        _set(p["id"], status="blocked", decision_by=config.GUARDIAN_NAME, decision_note=(note or "")[:300])
        _event(p["id"], "guardian_blocked", "authorization voided, money never left")
        return public(get(p["id"]))
    raise PaymentError("Decision must be approve or block.")


def sender_cancel(payment_id):
    p = get(payment_id)
    if not p:
        raise PaymentError("No such payment.")
    if p["status"] == "held":
        try:
            paypal.void_authorization(p["paypal_authorization_id"])
        except PayPalError as e:
            raise PaymentError("PayPal couldn't release the hold. Try again.") from e
    elif p["status"] != "awaiting_payment":
        raise PaymentError(f"Payment is already {p['status']}.")
    _set(p["id"], status="cancelled", decision_by=config.SENDER_NAME)
    _event(p["id"], "cancelled_by_sender")
    return public(get(p["id"]))


def list_payments(status=None, limit=100):
    q, args = "SELECT * FROM payments", []
    if status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(limit))
    with db.tx() as conn:
        rows = conn.execute(q, args).fetchall()
    out = []
    for r in rows:
        p = db.row(r)
        p["red_flags"] = json.loads(p["red_flags"])
        out.append(p)
    return out


def stats():
    rows = list_payments(limit=10000)
    total = lambda st: str(sum(Decimal(r["amount"]) for r in rows if r["status"] == st))
    return {
        "held": sum(1 for r in rows if r["status"] == "held"),
        "sent": sum(1 for r in rows if r["status"] == "sent"),
        "blocked": sum(1 for r in rows if r["status"] == "blocked"),
        "amount_protected": total("blocked"),
        "amount_sent": total("sent"),
        "currency": config.CURRENCY,
    }


def recent_events(limit=20):
    with db.tx() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [db.row(r) for r in rows]
