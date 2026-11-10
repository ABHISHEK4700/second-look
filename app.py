"""Second Look: an AI that takes a second look before your money leaves."""
import os
import re
import uuid

from flask import Flask, jsonify, request, send_from_directory

import agent
import config
import db
import payments

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024  # screenshots
db.init()

CONV_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def _err(msg, code=400):
    return jsonify({"error": msg}), code


def _guardian_ok():
    return request.headers.get("X-Guardian-Pin", "") == config.GUARDIAN_PIN


@app.get("/")
def sender_page():
    return send_from_directory("static", "index.html")


@app.get("/guardian")
def guardian_page():
    return send_from_directory("static", "guardian.html")


@app.get("/healthz")
def health():
    return {"ok": True}


@app.get("/api/config")
def public_config():
    return {
        "sender": config.SENDER_NAME, "guardian": config.GUARDIAN_NAME,
        "guardian_relation": config.GUARDIAN_RELATION, "currency": config.CURRENCY,
        "paypal_client_id": config.PAYPAL_CLIENT_ID, "paypal_mode": config.PAYPAL_MODE,
        "ai_ready": bool(config.GEMINI_API_KEY), "model": config.GEMINI_MODEL,
    }


# -- sender ----------------------------------------------------------------
@app.post("/api/chat")
def chat():
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    image = body.get("image")
    conv = body.get("conversation_id") or str(uuid.uuid4())
    if not message and not image:
        return _err("Message is empty.")
    if not CONV_RE.match(conv):
        return _err("Bad conversation id.")
    if image and (not isinstance(image, str) or not image.startswith("data:image/")):
        return _err("Image must be a data URL.")
    try:
        out = agent.chat(conv, message, image)
    except agent.AgentError as e:
        app.logger.warning("agent error: %s", e)
        return _err(str(e), 502)
    out["conversation_id"] = conv
    return jsonify(out)


@app.post("/api/payments/authorize")
def authorize():
    body = request.get_json(silent=True) or {}
    try:
        p = payments.authorize(body.get("payment_id", ""), body.get("order_id", ""))
    except payments.PaymentError as e:
        return _err(str(e))
    return jsonify({"payment": p})


@app.get("/api/payments/<pid>")
def status(pid):
    p = payments.public(payments.get(pid))
    return jsonify({"payment": p}) if p else _err("Not found", 404)


@app.post("/api/payments/<pid>/cancel")
def cancel(pid):
    try:
        return jsonify({"payment": payments.sender_cancel(pid)})
    except payments.PaymentError as e:
        return _err(str(e))


# -- guardian --------------------------------------------------------------
@app.get("/api/guardian/overview")
def overview():
    if not _guardian_ok():
        return _err("Wrong PIN.", 401)
    rows = payments.list_payments(limit=50)
    return jsonify({
        "stats": payments.stats(),
        "payments": [payments.public(p, include_evidence=True) for p in rows],
        "events": payments.recent_events(20),
        "sender": config.SENDER_NAME,
    })


@app.post("/api/guardian/payments/<pid>/<decision>")
def decide(pid, decision):
    if not _guardian_ok():
        return _err("Wrong PIN.", 401)
    note = (request.get_json(silent=True) or {}).get("note", "")
    try:
        return jsonify({"payment": payments.guardian_decide(pid, decision, note)})
    except payments.PaymentError as e:
        return _err(str(e))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
