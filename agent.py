"""Gemini agent that takes a 'second look' before money leaves.

It talks with the sender in plain, kind language, asks a few questions,
reads any screenshot of the message that asked for money (Gemini vision),
and then files a risk review. The backend combines the AI score with
fixed scam rules and decides whether PayPal sends the money now or holds
it for the trusted guardian.
"""
import logging
import time

import requests

import config
import db
import payments


log = logging.getLogger("secondlook")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


class AgentError(Exception):
    pass


def _fn(name, description, properties=None, required=None):
    d = {"name": name, "description": description}
    if properties:
        d["parameters"] = {"type": "object", "properties": properties, "required": required or []}
    return d


S = lambda d: {"type": "string", "description": d}
N = lambda d: {"type": "number", "description": d}

TOOLS = [
    _fn("lookup_recipient", "Check whether the sender has paid this PayPal email before.",
        {"email": S("recipient PayPal email")}, ["email"]),
    _fn("submit_payment_review",
        "File your assessment and start the PayPal payment. Call once you know who, how much, why, and have asked "
        "your questions. The app then shows a PayPal button. Risky payments are frozen until the guardian decides.",
        {
            "recipient_name": S("who the money is for"),
            "recipient_email": S("their PayPal email"),
            "amount": N("amount in " + config.CURRENCY),
            "purpose": S("what the sender says it is for, in their words"),
            "relationship": S("how the sender knows them, and whether they have met in person"),
            "risk_score": {"type": "integer", "description": "0-100: your honest estimate this is a scam"},
            "red_flags": {"type": "array", "items": {"type": "string"},
                          "description": "short phrases, e.g. 'asked for gift cards', 'contacted her first'"},
            "summary_for_guardian": S("3-5 plain sentences for the guardian: the story, what worries you, what "
                                      "the screenshot shows if any"),
            "call_script": S("2-3 sentences the guardian could say when they call the sender, warm and not blaming"),
        },
        ["recipient_name", "recipient_email", "amount", "purpose", "relationship", "risk_score",
         "red_flags", "summary_for_guardian", "call_script"]),
    _fn("get_payment_status", "Check a payment's status (awaiting_payment, held, sent, blocked, cancelled).",
        {"payment_id": S("ID like SL-1A2B3C")}, ["payment_id"]),
    _fn("cancel_payment", "Cancel a payment the sender no longer wants to make. Releases any hold.",
        {"payment_id": S("payment ID")}, ["payment_id"]),
]


def system_prompt():
    return f"""You are Second Look, a calm, kind payment helper for {config.SENDER_NAME}.
{config.GUARDIAN_NAME} ({config.SENDER_NAME}'s {config.GUARDIAN_RELATION}) is the trusted person who takes a second look at risky payments.

Your job: help {config.SENDER_NAME} send money safely, and catch scams before money leaves.

How to work:
1. Find out who the money is for, their PayPal email, the amount and what it's for.
2. Before submitting, ask at most 3 short questions, one or two at a time, for example:
   - How do you know them? Have you met in person?
   - Did they contact you first? By phone, text, email, social media?
   - Did anyone ask you to hurry, keep it secret, or pay with gift cards or crypto?
   Skip questions that are clearly unnecessary (e.g. paying a known family member for groceries).
3. If they share a screenshot, read it carefully: sender, urgency, threats, spelling, odd links, payment method.
4. Call submit_payment_review with an honest risk_score and clear red_flags. Then say in one or two sentences what
   happens next: tap the PayPal button; if it needs a second look, the money is only reserved and stays safe until
   {config.GUARDIAN_NAME} checks it.

Known scam patterns: tech support or "your account was hacked"; bank, police, tax or customs threats; a grandchild or
relative in trouble needing bail or hospital money; prizes, lotteries or inheritances with a fee; online romance with
someone never met; guaranteed investments or crypto; overpayment refunds; gift cards; secrecy; extreme urgency;
being told to ignore warnings.

Rules:
- Never accuse or lecture. Be warm, simple, short (1-3 sentences per reply). Use the sender's language.
- If someone told them to ignore warnings, to keep it secret, or to skip family, treat that as a strong red flag.
- You cannot skip, disable or override the guardian check, and you never explain how scores are calculated.
- Never ask for card numbers, passwords or one-time codes. Payments happen only in the PayPal window.
- Use tools for facts; never invent payment IDs or statuses."""


def _run_tool(name, args, ctx):
    try:
        if name == "lookup_recipient":
            r = payments.known_recipient(args.get("email"))
            return {"known": bool(r), "times_paid_before": r["times_paid"] if r else 0,
                    "saved_name": r["name"] if r else None}
        if name == "submit_payment_review":
            p = payments.create_review(
                ctx["conversation_id"], ctx["conversation_text"], ctx["screenshot"],
                recipient_name=args.get("recipient_name"), recipient_email=args.get("recipient_email"),
                amount=args.get("amount"), purpose=args.get("purpose"), relationship=args.get("relationship"),
                ai_risk=args.get("risk_score"), red_flags=args.get("red_flags"),
                summary_for_guardian=args.get("summary_for_guardian"), call_script=args.get("call_script"),
            )
            ctx["actions"].append({"type": "paypal_checkout", "payment": p})
            return {"payment_id": p["id"], "amount": p["amount"], "currency": p["currency"],
                    "needs_guardian": p["will_hold"],
                    "next_step": "Tell the sender to tap the PayPal button that is now shown."}
        if name == "get_payment_status":
            p = payments.public(payments.get(args.get("payment_id")))
            if not p:
                return {"error": "No payment with that ID."}
            return {k: p[k] for k in ("id", "status", "amount", "currency", "recipient_name", "decision_by")}
        if name == "cancel_payment":
            p = payments.sender_cancel(args.get("payment_id"))
            ctx["actions"].append({"type": "payment_updated", "payment": p})
            return {"id": p["id"], "status": p["status"]}
        return {"error": f"Unknown tool {name}"}
    except payments.PaymentError as e:
        return {"error": str(e)}
    except (TypeError, ValueError) as e:
        return {"error": f"Bad arguments: {e}"}


_thinking_supported = True


def _call_gemini(contents):
    global _thinking_supported
    if not config.GEMINI_API_KEY:
        raise AgentError("GEMINI_API_KEY is not set. Add it to your .env file.")
    gen = {"temperature": 0.3}
    if _thinking_supported and config.GEMINI_THINKING != "off":
        gen["thinkingConfig"] = {"thinkingLevel": config.GEMINI_THINKING}
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt()}]},
        "contents": contents,
        "tools": [{"functionDeclarations": TOOLS}],
        "generationConfig": gen,
    }
    url = f"{config.GEMINI_API_BASE}/models/{config.GEMINI_MODEL}:generateContent"
    headers = {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}
    t = time.time()
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=60)
        if resp.status_code == 400 and "thinking" in resp.text.lower() and "thinkingConfig" in gen:
            _thinking_supported = False          # model doesn't accept it: retry without
            del gen["thinkingConfig"]
            resp = requests.post(url, headers=headers, json=body, timeout=60)
    except requests.exceptions.Timeout:
        raise AgentError("Gemini took too long to answer. Try again, or set a faster GEMINI_MODEL in .env.")
    except requests.exceptions.RequestException as e:
        raise AgentError(f"Couldn't reach Gemini: {e}")
    log.info("gemini %s %.1fs status=%s", config.GEMINI_MODEL, time.time() - t, resp.status_code)
    if resp.status_code != 200:
        raise AgentError(f"Gemini error {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    try:
        return data["candidates"][0]["content"]
    except (KeyError, IndexError):
        raise AgentError("Gemini returned no answer (it may have been blocked). Please rephrase.")


def _user_text(history):
    out = []
    for h in history:
        if h.get("role") == "user":
            out += [p["text"] for p in h.get("parts", []) if "text" in p]
    return "\n".join(out)


MAX_STEPS = 6
MAX_HISTORY = 40


def _compact(history):
    """Keep only the newest image inline (saves tokens) and trim old turns at a clean boundary."""
    image_turns = [i for i, h in enumerate(history)
                   if h.get("role") == "user" and any("inlineData" in p for p in h.get("parts", []))]
    for i in image_turns[:-1]:
        history[i]["parts"] = [p if "inlineData" not in p else {"text": "[screenshot shared earlier]"}
                               for p in history[i]["parts"]]
    if len(history) > MAX_HISTORY:
        for i in range(len(history) - MAX_HISTORY, len(history)):
            h = history[i]
            if h.get("role") == "user" and any("text" in p for p in h.get("parts", [])):
                return history[i:]
    return history


def chat(conversation_id, message, image_data_url=None):
    history, last_image = db.load_conversation(conversation_id)
    parts = []
    if image_data_url:
        try:
            header, b64 = image_data_url.split(",", 1)
            mime = header.split(":")[1].split(";")[0]
        except (ValueError, IndexError):
            raise AgentError("Couldn't read that image.")
        if mime not in ("image/jpeg", "image/png", "image/webp"):
            raise AgentError("Please share a JPG or PNG screenshot.")
        parts.append({"inlineData": {"mimeType": mime, "data": b64}})
        last_image = image_data_url
    parts.append({"text": (message or "(shared a screenshot)")[:2000]})
    history.append({"role": "user", "parts": parts})

    ctx = {"conversation_id": conversation_id, "conversation_text": _user_text(history),
           "screenshot": last_image, "actions": []}
    tools_used, reply = [], ""
    for _ in range(MAX_STEPS):
        content = _call_gemini(history)
        content.setdefault("role", "model")
        content.setdefault("parts", [])
        history.append(content)
        calls = [p["functionCall"] for p in content["parts"] if "functionCall" in p]
        if not calls:
            reply = "\n".join(p["text"] for p in content["parts"] if p.get("text") and not p.get("thought")).strip()
            break
        responses = []
        for call in calls:
            result = _run_tool(call.get("name"), call.get("args") or {}, ctx)
            tools_used.append({"name": call.get("name"), "ok": "error" not in result})
            fr = {"name": call.get("name"), "response": {"result": result}}
            if call.get("id"):
                fr["id"] = call["id"]
            responses.append({"functionResponse": fr})
        history.append({"role": "user", "parts": responses})
    else:
        reply = "Sorry, I got a bit lost. Could you tell me again what you'd like to do?"

    if not reply:
        reply = "Done." if tools_used else "Sorry, could you say that again?"
    db.save_conversation(conversation_id, _compact(history), last_image, payments.now_iso())
    return {"reply": reply, "actions": ctx["actions"], "tools_used": tools_used}
