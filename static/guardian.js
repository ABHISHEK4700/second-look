(async function () {
  let pin = "";
  try { pin = sessionStorage.getItem("sl-pin") || ""; } catch (_) {}
  const headers = () => ({ "X-Guardian-Pin": pin });
  const cfg = await api("/api/config");
  if (cfg.paypal_mode === "mock") { $("#modePill").textContent = "Offline test mode"; $("#modePill").classList.add("warn"); }
  let lastHeldIds = "";

  $("#pinForm").addEventListener("submit", async e => {
    e.preventDefault();
    pin = $("#pin").value;
    try { await load(); open(); } catch (err) { toast(err.message); }
  });

  function open() {
    try { sessionStorage.setItem("sl-pin", pin); } catch (_) {}
    $("#gate").classList.add("hidden");
    $("#app").classList.remove("hidden");
  }

  const riskColor = r => r >= 70 ? "var(--red)" : r >= 40 ? "var(--amber)" : "var(--green)";

  function lightbox(src) {
    const lb = el("div", { class: "lightbox" }, el("img", { src }));
    lb.addEventListener("click", () => lb.remove());
    document.body.append(lb);
  }

  async function decide(p, decision, note, buttons) {
    buttons.forEach(b => b.disabled = true);
    try {
      await api(`/api/guardian/payments/${p.id}/${decision}`, { method: "POST", headers: headers(), body: { note } });
      toast(decision === "approve"
        ? `Approved: PayPal captured ${money(p.amount, p.currency)} and paid ${p.recipient_name}`
        : `Blocked: the PayPal hold was voided. ${cfg.sender}'s money never left.`);
      load();
    } catch (e) { toast(e.message); buttons.forEach(b => b.disabled = false); }
  }

  function reviewCard(p) {
    const note = el("input", { placeholder: `Optional note to ${cfg.sender}` });
    const block = el("button", { class: "btn red" }, "Block payment");
    const approve = el("button", { class: "btn green" }, "Approve & send");
    block.addEventListener("click", () => decide(p, "block", note.value, [block, approve]));
    approve.addEventListener("click", () => decide(p, "approve", note.value, [block, approve]));
    const evidence = el("div", { class: "evidence" }, el("div", { class: "label-sm", style: "margin-top:0" }, "Evidence"));
    if (p.screenshot) {
      const img = el("img", { src: p.screenshot, alt: "screenshot shared by sender" });
      img.addEventListener("click", () => lightbox(p.screenshot));
      evidence.append(img);
    } else evidence.append(el("div", { class: "none" }, "No screenshot shared"));
    evidence.append(el("div", { class: "label-sm" }, "PayPal"),
      el("div", { class: "mono", style: "font-size:12px;color:var(--muted)" },
        `Authorization ${p.paypal.paypal_authorization_id || "–"}`, el("br"), "Funds reserved, not captured"));

    return el("div", { class: `card review ${p.risk >= 70 ? "high" : ""}` },
      el("div", {},
        el("div", { class: "who" }, `${money(p.amount, p.currency)} → ${p.recipient_name}`),
        el("div", { class: "meta" }, `${p.recipient_email} · for "${p.purpose || "not given"}" · ${p.relationship || ""}`),
        el("div", { class: "meter" }, el("i", { style: `width:${p.risk}%;background:${riskColor(p.risk)}` })),
        el("div", { class: "risk-line" }, el("span", {}, `Scam risk ${p.risk}/100 (${p.risk_level})`),
          el("span", {}, `AI ${p.ai_risk} · rules ${p.rule_risk}`)),
        el("div", { class: "flags" }, ...p.red_flags.map(f => el("span", { class: "flag" }, f))),
        el("div", { class: "label-sm" }, "What the AI found"),
        el("p", { class: "summary" }, p.ai_summary || ""),
        p.call_script ? el("div", { class: "label-sm" }, `If you call ${cfg.sender}`) : null,
        p.call_script ? el("p", { class: "script" }, `"${p.call_script}"`) : null,
        el("div", { class: "actions" }, note, block, approve)),
      evidence);
  }

  async function load() {
    const d = await api("/api/guardian/overview", { headers: headers() });
    const s = d.stats;
    $("#sHeld").textContent = s.held;
    $("#statHeld").classList.toggle("alert", s.held > 0);
    $("#sProtected").textContent = money(s.amount_protected, s.currency);
    $("#sSent").textContent = s.sent;
    $("#sSentAmt").textContent = money(s.amount_sent, s.currency);
    $("#sBlocked").textContent = s.blocked;

    const held = d.payments.filter(p => p.status === "held");
    const ids = held.map(p => p.id).join(",");
    if (ids !== lastHeldIds) {   // don't wipe a note the guardian is typing
      lastHeldIds = ids;
      const reviews = $("#reviews");
      reviews.replaceChildren();
      if (!held.length) reviews.append(el("div", { class: "card calm" }, el("strong", {}, "All clear"),
        `Nothing is waiting. Safe payments from ${cfg.sender} go straight through.`));
      held.forEach(p => reviews.append(reviewCard(p)));
      if (held.length) document.title = `(${held.length}) Second Look · Guardian`;
      else document.title = "Second Look · Guardian";
    }

    const rows = $("#rows");
    rows.replaceChildren();
    if (!d.payments.length) rows.append(el("tr", {}, el("td", { colspan: "6", class: "empty" }, "No payments yet.")));
    for (const p of d.payments) {
      rows.append(el("tr", {},
        el("td", {}, el("span", { class: "mono" }, p.id)),
        el("td", {}, p.recipient_name),
        el("td", {}, money(p.amount, p.currency)),
        el("td", {}, el("span", { style: `color:${riskColor(p.risk)};font-weight:600` }, String(p.risk))),
        el("td", {}, statusBadge(p.status)),
        el("td", {}, p.decision_by || "–")));
    }
    const ev = $("#events");
    ev.replaceChildren();
    if (!d.events.length) ev.append(el("li", { class: "empty" }, "No activity yet."));
    for (const e of d.events) ev.append(el("li", {},
      el("span", { class: "k" }, e.kind.replace(/_/g, " ")),
      el("span", { class: "mono" }, `${e.payment_id || ""} ${e.detail || ""}`.slice(0, 60)),
      el("span", { class: "t" }, e.created_at.slice(11, 16))));
  }

  $("#refresh").addEventListener("click", () => { lastHeldIds = "-"; load(); });
  if (pin) { try { await load(); open(); } catch (_) {} }
  setInterval(() => { if (!$("#app").classList.contains("hidden")) load().catch(() => {}); }, 4000);
})();
