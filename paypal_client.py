"""PayPal REST client for Second Look.

The money flow uses PayPal's authorize-then-capture model so a risky payment
can be frozen without the money ever leaving the sender:

  1. POST /v2/checkout/orders                       intent AUTHORIZE
  2. (sender approves with the PayPal JS SDK button)
  3. POST /v2/checkout/orders/{id}/authorize         money is reserved, not moved
  4a. guardian approves -> POST /v2/payments/authorizations/{id}/capture
                         -> POST /v1/payments/payouts  (deliver to recipient)
  4b. guardian blocks   -> POST /v2/payments/authorizations/{id}/void

PAYPAL_MODE=mock swaps in an in-memory fake for the offline test suite.
"""
import time
import uuid

import requests

import config


class PayPalError(Exception):
    pass


class PayPalClient:
    def __init__(self):
        self._token, self._expiry = None, 0

    def _access_token(self):
        if self._token and time.time() < self._expiry - 60:
            return self._token
        if not (config.PAYPAL_CLIENT_ID and config.PAYPAL_CLIENT_SECRET):
            raise PayPalError("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set")
        resp = requests.post(
            f"{config.PAYPAL_API_BASE}/v1/oauth2/token",
            auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            timeout=20,
        )
        if resp.status_code != 200:
            raise PayPalError(f"PayPal auth failed: {resp.status_code} {resp.text[:300]}")
        body = resp.json()
        self._token = body["access_token"]
        self._expiry = time.time() + int(body.get("expires_in", 3000))
        return self._token

    def _request(self, method, path, body=None, request_id=None):
        headers = {"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        resp = requests.request(method, f"{config.PAYPAL_API_BASE}{path}", json=body, headers=headers, timeout=30)
        if resp.status_code >= 400:
            raise PayPalError(f"PayPal {method} {path} failed: {resp.status_code} {resp.text[:500]}")
        return resp.json() if resp.text else {}

    def create_order(self, payment_id, amount, currency, description):
        body = {
            "intent": "AUTHORIZE",
            "purchase_units": [{
                "reference_id": payment_id,
                "custom_id": payment_id,
                "description": description[:127],
                "amount": {"currency_code": currency, "value": amount},
            }],
            "payment_source": {"paypal": {"experience_context": {
                "brand_name": "Second Look",
                "shipping_preference": "NO_SHIPPING",
                "user_action": "CONTINUE",
            }}},
        }
        return self._request("POST", "/v2/checkout/orders", body, f"order-{payment_id}")["id"]

    def authorize_order(self, order_id):
        res = self._request("POST", f"/v2/checkout/orders/{order_id}/authorize", {}, f"auth-{order_id}")
        auth = res["purchase_units"][0]["payments"]["authorizations"][0]
        return {"authorization_id": auth["id"], "status": auth.get("status")}

    def capture_authorization(self, authorization_id):
        res = self._request(
            "POST", f"/v2/payments/authorizations/{authorization_id}/capture",
            {"final_capture": True}, f"cap-{authorization_id}",
        )
        return {"capture_id": res["id"], "status": res.get("status")}

    def void_authorization(self, authorization_id):
        self._request("POST", f"/v2/payments/authorizations/{authorization_id}/void", None, f"void-{authorization_id}")
        return {"status": "VOIDED"}

    def payout(self, payment_id, recipient_email, amount, currency, note):
        body = {
            "sender_batch_header": {
                "sender_batch_id": f"SL-{payment_id}",
                "email_subject": "You've received a payment",
                "email_message": note[:255],
            },
            "items": [{
                "recipient_type": "EMAIL",
                "receiver": recipient_email,
                "amount": {"value": amount, "currency": currency},
                "note": note[:255],
                "sender_item_id": payment_id,
            }],
        }
        res = self._request("POST", "/v1/payments/payouts", body, f"payout-{payment_id}")
        header = res.get("batch_header", {})
        return {"payout_batch_id": header.get("payout_batch_id"), "status": header.get("batch_status")}


class MockPayPalClient:
    """Offline stand-in used only by automated tests."""

    def __init__(self):
        self.calls = []

    def create_order(self, payment_id, amount, currency, description):
        self.calls.append(("create", payment_id))
        return "MOCK-" + uuid.uuid4().hex[:10].upper()

    def authorize_order(self, order_id):
        self.calls.append(("authorize", order_id))
        return {"authorization_id": "AUTH-" + order_id[5:], "status": "CREATED"}

    def capture_authorization(self, authorization_id):
        self.calls.append(("capture", authorization_id))
        return {"capture_id": "CAP-" + authorization_id[5:], "status": "COMPLETED"}

    def void_authorization(self, authorization_id):
        self.calls.append(("void", authorization_id))
        return {"status": "VOIDED"}

    def payout(self, payment_id, recipient_email, amount, currency, note):
        self.calls.append(("payout", payment_id))
        return {"payout_batch_id": "BATCH-" + payment_id, "status": "PENDING"}


def make_client():
    return MockPayPalClient() if config.PAYPAL_MODE == "mock" else PayPalClient()
