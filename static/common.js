// Shared helpers (adapted from SeatFlow Pay).
const $ = (sel, root = document) => root.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

async function api(path, { method = "GET", body, headers = {} } = {}) {
  const res = await fetch(path, {
    method, headers: { "Content-Type": "application/json", ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function toast(text) {
  const t = el("div", { class: "toast" }, text);
  document.body.append(t);
  setTimeout(() => t.remove(), 3400);
}

function statusBadge(status) {
  const label = { awaiting_payment: "not paid", held: "waiting for you" }[status] || status;
  return el("span", { class: `status ${status}` }, label);
}

const money = (v, cur) => `${Number(v).toFixed(2)} ${cur}`;

// PayPal JS SDK, loaded once, with intent=authorize so money is reserved first.
let paypalLoading = null;
function loadPayPal(clientId, currency) {
  if (window.paypal) return Promise.resolve(window.paypal);
  if (!paypalLoading) {
    paypalLoading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = `https://www.paypal.com/sdk/js?client-id=${encodeURIComponent(clientId)}&currency=${currency}&intent=authorize&components=buttons`;
      s.onload = () => resolve(window.paypal);
      s.onerror = () => reject(new Error("Could not load PayPal"));
      document.head.append(s);
    });
  }
  return paypalLoading;
}

// Shrink a screenshot before upload (max 1280px, JPEG).
function shrinkImage(file, maxSide = 1280) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * scale);
      c.height = Math.round(img.height * scale);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => reject(new Error("That file isn't an image."));
    img.src = url;
  });
}
