"""Quick setup check: run `python check_setup.py` in the project folder.

Tests your .env, the Gemini key/model, and the PayPal sandbox keys, and prints
what is wrong in plain words.
"""
import time

import requests

import config


def line(ok, msg):
    print(("  OK   " if ok else "  FAIL ") + msg)


print("\n1) .env values")
line(bool(config.GEMINI_API_KEY), f"GEMINI_API_KEY is {'set' if config.GEMINI_API_KEY else 'MISSING'}")
line(bool(config.PAYPAL_CLIENT_ID), f"PAYPAL_CLIENT_ID is {'set' if config.PAYPAL_CLIENT_ID else 'MISSING'}")
line(bool(config.PAYPAL_CLIENT_SECRET), f"PAYPAL_CLIENT_SECRET is {'set' if config.PAYPAL_CLIENT_SECRET else 'MISSING'}")
print(f"       GEMINI_MODEL = {config.GEMINI_MODEL}")

print("\n2) Can this computer reach Google? (listing your models)")
flash = []
try:
    t = time.time()
    r = requests.get(f"{config.GEMINI_API_BASE}/models", params={"pageSize": 200},
                     headers={"x-goog-api-key": config.GEMINI_API_KEY}, timeout=20)
    if r.status_code == 200:
        names = [m["name"].replace("models/", "") for m in r.json().get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
        flash = [n for n in names if "flash" in n and not any(x in n for x in ("tts", "image", "live", "audio", "embed"))]
        line(True, f"Reached Gemini in {time.time() - t:.1f}s. Flash models your key can use:")
        for n in flash:
            print("         -", n)
        line(config.GEMINI_MODEL in names,
             f"Your GEMINI_MODEL '{config.GEMINI_MODEL}' is " + ("available" if config.GEMINI_MODEL in names
                                                                  else "NOT in the list. Copy one name from above into .env"))
    else:
        line(False, f"Gemini answered {r.status_code}: {r.text[:300]}")
except requests.exceptions.Timeout:
    line(False, "Timed out reaching Google. Your network may block it: try mobile hotspot or another Wi-Fi.")
except requests.exceptions.RequestException as e:
    line(False, f"Network error reaching Google: {e}")

print("\n3) Does the model answer? (short test, up to 60s)")
try:
    t = time.time()
    r = requests.post(f"{config.GEMINI_API_BASE}/models/{config.GEMINI_MODEL}:generateContent",
                      headers={"x-goog-api-key": config.GEMINI_API_KEY},
                      json={"contents": [{"role": "user", "parts": [{"text": "Say hi in 3 words."}]}]}, timeout=60)
    if r.status_code == 200:
        parts = r.json()["candidates"][0]["content"].get("parts", [])
        line(True, f"Model replied in {time.time() - t:.1f}s: {' '.join(p.get('text', '') for p in parts).strip()[:80]}")
    else:
        line(False, f"Model error {r.status_code}: {r.text[:300]}")
except requests.exceptions.Timeout:
    line(False, "Model took over 60s. Try a different flash model from the list above.")
except Exception as e:
    line(False, f"{type(e).__name__}: {e}")

print("\n4) PayPal sandbox keys")
try:
    r = requests.post(f"{config.PAYPAL_API_BASE}/v1/oauth2/token",
                      auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET),
                      data={"grant_type": "client_credentials"}, timeout=20)
    if r.status_code == 200:
        line(True, "PayPal sandbox login works")
        scope = r.json().get("scope", "")
        line("payouts" in scope, "Payouts permission " + ("enabled" if "payouts" in scope
                                                        else "NOT found: tick 'Payouts' in your app's features"))
    else:
        line(False, f"PayPal login failed {r.status_code}: check Client ID/Secret and that Sandbox is selected")
except requests.exceptions.RequestException as e:
    line(False, f"Network error reaching PayPal: {e}")
print()
