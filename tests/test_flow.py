"""End-to-end tests: PayPal in mock mode, Gemini replaced by scripted replies.

Run:  python -m pytest -q
"""
import copy
import os
import sys
import tempfile

os.environ["PAYPAL_MODE"] = "mock"
os.environ["GEMINI_API_KEY"] = "test-key"
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import agent  # noqa: E402
import paypal_client  # noqa: E402
import payments  # noqa: E402
from app import app  # noqa: E402

PIN = {"X-Guardian-Pin": "2468"}
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


def call(name, **args):
    return {"role": "model", "parts": [{"functionCall": {"name": name, "args": args}, "thoughtSignature": "sig"}]}


def text(t):
    return {"role": "model", "parts": [{"text": t}]}


def script(monkeypatch, replies):
    seen = []

    def fake(contents):
        seen.append(copy.deepcopy(contents))
        return replies.pop(0)

    monkeypatch.setattr(agent, "_call_gemini", fake)
    return seen


@pytest.fixture
def client():
    return app.test_client()


def review_args(**over):
    base = dict(recipient_name="Microsoft Support", recipient_email="help@ms-secure-fix.example.com", amount=400,
                purpose="fix my computer", relationship="they called me, never met", risk_score=55,
                red_flags=["contacted her first"], summary_for_guardian="Caller claims to be Microsoft.",
                call_script="Mom, Microsoft never calls people.")
    base.update(over)
    return base


def test_scam_is_held_then_blocked(client, monkeypatch):
    seen = script(monkeypatch, [
        text("I'm sorry that happened. Did they contact you first, and did they ask for anything unusual?"),
        call("submit_payment_review", **review_args(risk_score=30)),
        text("I've prepared it. Tap the PayPal button. Daniel will take a second look before anything is sent."),
    ])
    r = client.post("/api/chat", json={"message": "Microsoft tech support called me, my account was hacked, I must pay $400",
                                       "conversation_id": "conv-scam-0001", "image": PNG})
    assert r.status_code == 200
    # the screenshot reached Gemini as inline data
    assert "inlineData" in seen[0][-1]["parts"][0]
    r = client.post("/api/chat", json={"message": "Yes they called me and said ignore any warning and keep it secret",
                                       "conversation_id": "conv-scam-0001"})
    p = r.json["actions"][0]["payment"]
    # AI said 30, rules push it up: tech support + secret + ignore warnings + new recipient
    assert p["risk"] >= 70 and p["rule_risk"] > p["ai_risk"] and p["will_hold"]
    assert "Told to keep it secret" in p["red_flags"]

    r = client.post("/api/payments/authorize", json={"payment_id": p["id"], "order_id": p["paypal_order_id"]})
    assert r.json["payment"]["status"] == "held"

    ov = client.get("/api/guardian/overview", headers=PIN).json
    held = [x for x in ov["payments"] if x["status"] == "held"]
    assert held and held[0]["screenshot"].startswith("data:image/png")

    r = client.post(f"/api/guardian/payments/{p['id']}/block", headers=PIN, json={"note": "This is a scam, Mom."})
    assert r.json["payment"]["status"] == "blocked"
    calls = [c[0] for c in payments.paypal.calls]
    assert "void" in calls
    assert payments.stats()["amount_protected"] == "400.00"


def test_known_recipient_low_risk_sends_immediately(client, monkeypatch):
    script(monkeypatch, [
        call("lookup_recipient", email="rosa.neighbor@example.com"),
        call("submit_payment_review", **review_args(recipient_name="Rosa", recipient_email="rosa.neighbor@example.com",
                                                     amount=40, purpose="groceries", relationship="neighbour",
                                                     risk_score=5, red_flags=[])),
        text("Ready. Tap the PayPal button to send it to Rosa."),
    ])
    r = client.post("/api/chat", json={"message": "Pay Rosa back $40 for groceries", "conversation_id": "conv-rosa-0001"})
    p = r.json["actions"][0]["payment"]
    assert p["risk"] < 40 and not p["will_hold"]
    r = client.post("/api/payments/authorize", json={"payment_id": p["id"], "order_id": p["paypal_order_id"]})
    out = r.json["payment"]
    assert out["status"] == "sent" and out["payout_status"] == "PENDING"
    assert payments.known_recipient("rosa.neighbor@example.com")["times_paid"] == 4


def test_guardian_approves_held_payment(client):
    p = payments.create_review("conv-x", "my nephew asked", None, recipient_name="Ravi",
                               recipient_email="ravi.new@example.com", amount=650, purpose="college fees",
                               relationship="nephew", ai_risk=20, red_flags=[], summary_for_guardian="",
                               call_script="")
    assert p["will_hold"]  # new recipient + large amount
    payments.authorize(p["id"], p["paypal_order_id"])
    r = client.post(f"/api/guardian/payments/{p['id']}/approve", headers=PIN, json={})
    assert r.json["payment"]["status"] == "sent" and r.json["payment"]["decision_by"] == "Daniel"


def test_sender_can_cancel_held(client):
    p = payments.create_review("conv-y", "you won the lottery, pay the processing fee", None, recipient_name="Prize Dept",
                               recipient_email="claims@lotto.example.com", amount=99, purpose="claim fee",
                               relationship="email", ai_risk=60, red_flags=[], summary_for_guardian="", call_script="")
    payments.authorize(p["id"], p["paypal_order_id"])
    r = client.post(f"/api/payments/{p['id']}/cancel")
    assert r.json["payment"]["status"] == "cancelled"


def test_rules_avoid_false_positives():
    score, flags = payments.rule_assessment("paying my son for the person who fixed the roof, you won't believe it",
                                            payments.Decimal("50"), "daniel@example.com")
    assert score == 0 and flags == []
    score, flags = payments.rule_assessment("my grandson is in jail and needs bail", payments.Decimal("900"), "x@y.com")
    assert "Relative in trouble, urgent money" in flags and score >= 60


def test_validation_and_guardrails(client):
    with pytest.raises(payments.PaymentError):
        payments.create_review("c", "", None, recipient_name="A", recipient_email="not-an-email", amount=5,
                               purpose="", relationship="", ai_risk=0, red_flags=[], summary_for_guardian="", call_script="")
    with pytest.raises(payments.PaymentError):
        payments.create_review("c", "", None, recipient_name="A", recipient_email="a@b.com", amount=999999,
                               purpose="", relationship="", ai_risk=0, red_flags=[], summary_for_guardian="", call_script="")
    assert client.get("/api/guardian/overview").status_code == 401
    assert client.post("/api/guardian/payments/SL-1/approve").status_code == 401
    r = client.post("/api/payments/authorize", json={"payment_id": "SL-NOPE", "order_id": "x"})
    assert r.status_code == 400
    assert "error" in agent._run_tool("submit_payment_review", {"recipient_name": "A"}, {
        "conversation_id": "c", "conversation_text": "", "screenshot": None, "actions": []})


def test_real_paypal_client_request_shapes(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, code, body):
            self.status_code, self._b = code, body
            self.text = "x" if body else ""

        def json(self):
            return self._b

    def fake_post(url, **kw):
        calls.append(("POST", url, kw))
        return Resp(200, {"access_token": "tok", "expires_in": 3600})

    def fake_request(method, url, **kw):
        calls.append((method, url, kw))
        if url.endswith("/v2/checkout/orders"):
            return Resp(201, {"id": "O1"})
        if url.endswith("/O1/authorize"):
            return Resp(201, {"purchase_units": [{"payments": {"authorizations": [{"id": "A1", "status": "CREATED"}]}}]})
        if url.endswith("/A1/capture"):
            return Resp(201, {"id": "C1", "status": "COMPLETED"})
        if url.endswith("/A1/void"):
            return Resp(204, {})
        if url.endswith("/v1/payments/payouts"):
            return Resp(201, {"batch_header": {"payout_batch_id": "B1", "batch_status": "PENDING"}})
        return Resp(404, {})

    monkeypatch.setattr(paypal_client.config, "PAYPAL_CLIENT_ID", "id")
    monkeypatch.setattr(paypal_client.config, "PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setattr(paypal_client.requests, "post", fake_post)
    monkeypatch.setattr(paypal_client.requests, "request", fake_request)
    c = paypal_client.PayPalClient()
    assert c.create_order("SL-1", "40.00", "USD", "x") == "O1"
    assert c.authorize_order("O1")["authorization_id"] == "A1"
    assert c.capture_authorization("A1")["capture_id"] == "C1"
    assert c.void_authorization("A1")["status"] == "VOIDED"
    assert c.payout("SL-1", "r@x.com", "40.00", "USD", "hi")["payout_batch_id"] == "B1"
    order = calls[1][2]["json"]
    assert order["intent"] == "AUTHORIZE"
    payout = calls[-1][2]["json"]
    assert payout["items"][0] == {"recipient_type": "EMAIL", "receiver": "r@x.com",
                                  "amount": {"value": "40.00", "currency": "USD"}, "note": "hi", "sender_item_id": "SL-1"}
