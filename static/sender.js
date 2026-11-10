(async function () {
  const cfg = await api("/api/config");
  $("#greeting").textContent = `Hi ${cfg.sender}, send money with a second look.`;
  $("#gName").textContent = cfg.guardian;
  $("#gFace").textContent = cfg.guardian[0];
  $("#gLine").textContent = `Your ${cfg.guardian_relation}, takes a second look at risky payments`;
  if (cfg.paypal_mode === "mock") { $("#modePill").textContent = "Offline test mode"; $("#modePill").classList.add("warn"); }

  let conversationId = uuid();
  try {
    const saved = sessionStorage.getItem("sl-conv");
    if (saved) conversationId = saved; else sessionStorage.setItem("sl-conv", conversationId);
  } catch (_) {}

  const box = $("#messages");
  let pendingImage = null;
  const watching = new Map();

  function add(kind, text, img) {
    const m = el("div", { class: `msg ${kind}` });
    if (img) m.append(el("img", { src: img, alt: "screenshot" }));
    if (text) m.append(text);
    box.append(m);
    box.scrollTop = box.scrollHeight;
    return m;
  }

  add("bot", `Hello ${cfg.sender}! Who would you like to send money to, and what is it for?`);
  if (!cfg.ai_ready) add("err", "The AI key isn't set on the server yet (GEMINI_API_KEY).");

  async function send(text, { silent = false } = {}) {
    const image = pendingImage;
    if (!text.trim() && !image) return;
    $("#chips")?.remove();
    if (!silent) add("me", text, image);
    pendingImage = null;
    $("#preview").classList.add("hidden");
    const typing = el("div", { class: "typing" }, "Taking a look…");
    box.append(typing);
    box.scrollTop = box.scrollHeight;
    $("#input").disabled = true;
    try {
      const data = await api("/api/chat", { method: "POST", body: { message: text, image, conversation_id: conversationId } });
      typing.remove();
      if (data.tools_used?.length) {
        const row = el("div", { class: "tools" });
        data.tools_used.forEach(t => row.append(el("span", { class: "tool" }, `⚙ ${t.name}`)));
        box.append(row);
      }
      add("bot", data.reply);
      for (const a of data.actions || []) {
        if (a.type === "paypal_checkout") renderCheckout(a.payment);
        if (a.type === "payment_updated") showResult(a.payment);
      }
    } catch (e) {
      typing.remove();
      add("err", e.message);
    } finally {
      $("#input").disabled = false;
      $("#input").focus();
      box.scrollTop = box.scrollHeight;
    }
  }

  function showResult(p, holder) {
    const g = cfg.guardian;
    const content = {
      sent: ["✓ Sent", `${money(p.amount, p.currency)} is on its way to ${p.recipient_name} through PayPal.`],
      held: ["Your money is safe", `I've reserved ${money(p.amount, p.currency)} but not sent it. ${g} will take a second look and you'll see the answer here. You can also cancel any time.`],
      blocked: [`${g} stopped this payment`, `Your money was never sent. ${p.decision_note ? `${g} says: "${p.decision_note}"` : `${g} will talk with you about it.`}`],
      cancelled: ["Cancelled", "This payment was cancelled. No money was sent."],
    }[p.status];
    if (!content) return;
    const r = el("div", { class: `result ${p.status}`, id: `res-${p.id}` }, el("h3", {}, content[0]), el("div", {}, content[1]));
    if (p.status === "held") {
      const c = el("button", { class: "btn ghost small", style: "margin-top:10px" }, "Cancel this payment");
      c.addEventListener("click", async () => {
        try { showResult((await api(`/api/payments/${p.id}/cancel`, { method: "POST" })).payment); } catch (e) { toast(e.message); }
      });
      r.append(c);
      watch(p.id);
    } else watching.delete(p.id);
    const old = $(`#res-${p.id}`);
    if (old) old.replaceWith(r); else if (holder) holder.replaceWith(r); else box.append(r);
    box.scrollTop = box.scrollHeight;
  }

  function watch(id) {
    if (watching.has(id)) return;
    watching.set(id, setInterval(async () => {
      try {
        const { payment } = await api(`/api/payments/${id}`);
        if (payment.status !== "held") { clearInterval(watching.get(id)); watching.delete(id); showResult(payment); }
      } catch (_) {}
    }, 4000));
  }

  const finished = new Set();
  async function authorized(p, orderId, holder) {
    finished.add(p.id);
    const { payment } = await api("/api/payments/authorize", { method: "POST", body: { payment_id: p.id, order_id: orderId } });
    showResult(payment, holder);
  }

  async function renderCheckout(p) {
    const btnHolder = el("div");
    const holder = el("div", { class: "pay-box" },
      el("div", { class: "amount" }, `${money(p.amount, p.currency)} → ${p.recipient_name}`),
      el("div", { class: "muted" }, p.will_hold
        ? `This one needs a second look. Approving in PayPal only reserves the money; ${cfg.guardian} decides before it's sent.`
        : "Approve in PayPal to send it."),
      btnHolder);
    box.append(holder);
    box.scrollTop = box.scrollHeight;
    if (cfg.paypal_mode === "mock") {
      btnHolder.append(el("button", { class: "btn", onclick: () => authorized(p, p.paypal_order_id, holder).catch(e => add("err", e.message)) }, "Approve with PayPal (test mode)"));
      return;
    }
    try {
      const paypal = await loadPayPal(cfg.paypal_client_id, p.currency);
      await paypal.Buttons({
        style: { layout: "vertical", shape: "rect", label: "paypal", height: 44 },
        createOrder: () => p.paypal_order_id,
        onApprove: data => authorized(p, data.orderID, holder).catch(e => add("err", e.message)),
        onCancel: () => { if (!finished.has(p.id)) add("sys", "PayPal window closed. Nothing was sent."); },
        onError: err => add("err", "PayPal error: " + (err?.message || err)),
      }).render(btnHolder);
    } catch (e) { add("err", e.message); }
  }

  $("#composer").addEventListener("submit", e => {
    e.preventDefault();
    const t = $("#input").value;
    $("#input").value = "";
    send(t);
  });
  document.querySelectorAll("#chips .chip").forEach(c => c.addEventListener("click", () => send(c.textContent)));
  $("#file").addEventListener("change", async e => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    try {
      pendingImage = await shrinkImage(f);
      $("#preview img").src = pendingImage;
      $("#preview").classList.remove("hidden");
      $("#input").focus();
    } catch (err) { toast(err.message); }
  });
  $("#clearImg").addEventListener("click", () => { pendingImage = null; $("#preview").classList.add("hidden"); });
})();
