# Devpost submission kit: Second Look

## Project name
Second Look

## Tagline
PayPal's AI spots the scam. Second Look adds a human the scammer can't talk past.

## Description

### Inspiration
Payment apps already warn people about scams, but warnings fail at the worst moment. In many scams the
fraudster is still on the phone, telling the victim: "you'll see a warning, just click continue, and don't tell
your family." A warning shown only to the person being manipulated is easy to click past. We asked: what if the
payment waited for one other trusted person?

### What it does
Second Look is a payment helper built on PayPal for people who are often targeted by scams.
- **An AI interview before paying.** A Gemini agent asks the sender a few kind, plain questions: who is this for,
  did they contact you first, did anyone ask you to hurry, keep it secret, or pay with gift cards or crypto?
- **Screenshot reading.** The sender can share the message that asked for money. Gemini vision reads it and spots
  urgency, threats, secrecy and odd payment instructions.
- **Money reserved, not sent.** The sender approves in PayPal, but Second Look only *authorizes* the payment.
- **Safe payments go straight through:** PayPal captures and pays out to the recipient.
- **Risky payments wait for a trusted guardian** (a son, daughter or friend). The guardian sees the risk score, red flags,
  the AI's summary, the screenshot and a suggested script for calling the sender. **Approve** captures and pays out.
  **Block** voids the PayPal authorization, so the money never leaves.
- The sender's screen updates live ("Daniel stopped this payment. Your money was never sent."), and the
  guardian dashboard tracks how much scam money was stopped.

### How we built it
- **PayPal (sandbox):** Orders v2 with intent AUTHORIZE, JS SDK Smart Buttons, authorize, capture-authorization,
  void-authorization and Payouts, with PayPal-Request-Id idempotency on every call. The authorize/void pair is
  what makes a "freeze" possible without ever moving the money.
- **AI:** Gemini `generateContent` with function calling and image input. The agent has 4 tools and writes the
  guardian's report and call script.
- **Safety floor:** 13 deterministic scam rules plus "new recipient" and "large first payment". The final risk is the max of
  the AI score and the rules, so a coached victim can't talk the AI down. The AI can't skip the guardian and only
  ever triggers an authorization. Capture, void and payout run in the backend.
- **Stack:** Python Flask, SQLite and vanilla JS. An end-to-end pytest suite with mock PayPal and scripted Gemini
  covers hold → block, instant send, guardian approve, cancel and request shapes.

### Challenges
Making "hold" real rather than cosmetic. We use PayPal's authorize → capture/void model so a held payment is
reserved but untouched until a human decides. We also tuned the scam rules to avoid false alarms
(e.g. "my son" shouldn't trigger the "relative in trouble" rule).

### What we learned
The best place for AI in payments isn't replacing people's judgment but routing the right decision to the right person
at the right time. PayPal's authorization model is a natural fit for that.

### What's next
Multiple guardians, SMS/WhatsApp alerts to guardians, voice input for senders, and auto-voiding stale holds.

## Links
- Live demo: https://second-look-usym.onrender.com (guardian view: /guardian, PIN 2468)
- Code: https://github.com/ABHISHEK4700/second-look

## Built with
python, flask, sqlite, javascript, paypal-orders-api, paypal-payments-api, paypal-payouts, paypal-javascript-sdk, google-gemini, gemini-vision, render

## Tools used (AI)
- **Google Gemini API:** the conversational agent (function calling), screenshot analysis (vision), and the risk report and call script for the guardian.
- **Claude Code (Anthropic):** coding assistant during development.

---

# Demo video script (target 2:45)

Record your screen at 1080p with the **sender** and **guardian** windows side by side.
Use no music, or only YouTube Audio Library music. The scam message image is `docs/demo-scam-message.png` (fictional).

| Time | Screen | What you say |
|---|---|---|
| 0:00–0:20 | Title / the scam message image | "Scam warnings often fail because the scammer is still on the phone, saying 'ignore the warning, don't tell your family.' Second Look adds a person the scammer can't coach." |
| 0:20–0:40 | Sender: pay Rosa $40 → PayPal → Sent | "First, a normal payment. Margaret pays her neighbour back. A known person and a small amount, so the AI lets it straight through with PayPal." |
| 0:40–1:15 | Sender: attach the screenshot, describe the hack story, answer the questions | "Now a scam. Margaret shares the texts she got. Gemini reads the screenshot, asks a couple of gentle questions, and flags the secrecy, the deadline and 'ignore the warning'." |
| 1:15–1:35 | Sender: approve in PayPal → "Your money is safe… reserved, not sent" | "She approves in PayPal, but Second Look only authorizes the payment. The money is reserved, not sent." |
| 1:35–2:10 | Guardian window: review card | "Her son Daniel gets the review: the risk score from the AI plus fixed scam rules, the red flags, the screenshot, and even what to say when he calls her." |
| 2:10–2:30 | Click Block → sender screen updates | "He blocks it. Second Look voids the PayPal authorization, so the money never left her account, and Margaret sees why." |
| 2:30–2:45 | Architecture diagram from README | "Built with PayPal Orders, Authorizations, Payouts and Smart Buttons, and Google Gemini. Open source, with setup steps in the repo." |
