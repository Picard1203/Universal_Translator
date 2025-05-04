/**
 * @file app_chat.js
 * @description
 * Client‐side logic for Universal-Translator chat UI (WebSocket edition).
 * Handles session guard, UI updates, WebSocket lifecycle, message framing,
 * 5-stage barrier synchronization, and RTT-based release delays.
 */

(() => {
  /** Enable verbose debug logging when true */
  const DEBUG = true;

  /** Shorthand debug logger */
  const log = (...args) => {
    if (DEBUG) console.log("[UT Chat]", ...args);
  };

  // ── Session Guard ────────────────────────────────────────────────────────
  /** Unique client ID from sessionStorage */
  const cid = sessionStorage.getItem("cid");
  /** Nickname from sessionStorage */
  const nick = sessionStorage.getItem("nick");
  /** Interface language from sessionStorage ("en" or "he") */
  const lang = sessionStorage.getItem("lang");

  if (!cid || !nick || !lang) {
    // Redirect to login if session is missing
    location.replace("/");
    throw new Error("No valid session; redirecting to login.");
  }

  // ── UI References ───────────────────────────────────────────────────────
  const historyEl   = document.getElementById("history");
  const meDisplayEl = document.getElementById("me");
  const formEl      = document.getElementById("compose");
  const inputEl     = document.getElementById("text");

  // Display current user in header
  meDisplayEl.textContent = `${nick} (${lang === "he" ? "עברית" : "English"})`;
  document.documentElement.lang = lang;
  document.documentElement.dir  = lang === "he" ? "rtl" : "ltr";

  // ── WebSocket Setup ─────────────────────────────────────────────────────
  const WS_PORT = 8443;
  const scheme  = location.protocol === "https:" ? "wss" : "ws";
  const wsUrl   = `${scheme}://${location.hostname}:${WS_PORT}/ws?cid=${encodeURIComponent(cid)}`;
  log("Connecting to WebSocket:", wsUrl);

  const ws = new WebSocket(wsUrl);

  /** Track rendered message IDs to avoid duplicates */
  const rendered = new Set();

  // ── Helpers ──────────────────────────────────────────────────────────────

  /**
   * Get current time formatted as HH:MM.
   * @returns {string}
   */
  function getFormattedTime() {
    const now = new Date();
    const hh  = String(now.getHours()).padStart(2, "0");
    const mm  = String(now.getMinutes()).padStart(2, "0");
    return `${hh}:${mm}`;
  }

  /**
   * Append a chat bubble if not already rendered.
   * @param {Object} msg    Incoming message object.
   * @param {boolean} isSelf True if the message originated from this client.
   */
  function addBubbleOnce(msg, isSelf) {
    log("addBubbleOnce:", msg.id, "self?", isSelf);

    if (!msg.id || !msg.sender || typeof msg.text === "undefined") {
      log("Invalid message for rendering:", msg);
      return;
    }
    if (rendered.has(msg.id)) {
      log("Already rendered message:", msg.id);
      return;
    }
    rendered.add(msg.id);

    // Create row container
    const row = document.createElement("div");
    row.className = `row ${isSelf ? "self" : "peer"}`;

    // Bubble element
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.setAttribute("aria-label", `Message from ${msg.sender}`);

    // ── Decide what to show (translation‑first layout) ────────────────
    const srcLang = msg.senderLang || msg.lang;
    const trDict  = msg.translations || {};
    const trans   = trDict[lang];                      // may be undefined
    const cross   = srcLang !== lang;
    const norm    = s => s.trim().toLowerCase();
const hasTr   = cross && trans && norm(trans) !== norm(msg.text);
    
    /* main (top) line – translation if present, else original */
    const mainSpan = document.createElement("span");
    mainSpan.className = "text";
    mainSpan.textContent = hasTr ? trans : msg.text;
    
    /* second (small) line – original or “translation failed” */
    let origSpan = null;
    if (cross) {
      origSpan = document.createElement("span");
      origSpan.className = "orig-text";
      if (hasTr) {
        origSpan.textContent = msg.text;                // original line
        bubble.classList.add("translated");
      } else {
        origSpan.textContent =
          lang === "he" ? "התרגום נכשל" : "translation failed";
      }
    }
    // Sender meta (hidden for self bubbles)
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = msg.sender;

    // Timestamp (bottom‑right / bottom‑left)
    const timeSpan = document.createElement("span");
    timeSpan.className = "timestamp";
    timeSpan.textContent = getFormattedTime();

    // Assemble and append
    bubble.append(meta, mainSpan);
    if (origSpan) bubble.append(origSpan);
    bubble.append(timeSpan);
    row.appendChild(bubble);
    historyEl.appendChild(row);

    // Scroll into view
    requestAnimationFrame(() => {
      historyEl.scrollTop = historyEl.scrollHeight;
    });

    log("Rendered message:", msg.id);
  }

  /**
   * Append a system message (join/leave/errors).
   * @param {string} text
   */
  function addSystemMessage(text) {
    const row = document.createElement("div");
    row.className = "row system";
    const msg = document.createElement("div");
    msg.className = "system-message-content";
    msg.textContent = text;
    row.appendChild(msg);
    historyEl.appendChild(row);
    requestAnimationFrame(() => {
      historyEl.scrollTop = historyEl.scrollHeight;
    });
  }

  /**
   * Send one or more barrier‐stage updates.
   * @param {string} mid       Message ID.
   * @param {...number} stages Stage indices to send.
   */
  function sendStages(mid, ...stages) {
    if (ws.readyState !== WebSocket.OPEN) {
      log("Cannot send stages; socket not open");
      return;
    }
    stages.forEach(stage => {
      const msg = { type: "stage", cid, mid, stage };
      log("Sending stage update:", msg);
      try {
        ws.send(JSON.stringify(msg));
      } catch (err) {
        log("Error sending stage:", err);
        addSystemMessage(`Error sending status for ${mid}`);
      }
    });
  }

  /**
   * Send a chat payload message.
   * @param {string} text
   */
  function sendPayload(text) {
    const payload = {
      type:       "payload",
      id:         crypto.randomUUID(),
      cid,
      nick,
      lang,
      text,
      sender:     nick,
      senderLang: lang
    };
    log("Sending payload:", payload);
    try {
      ws.send(JSON.stringify(payload));
    } catch (err) {
      log("Error sending payload:", err);
      addSystemMessage("Failed to send message.");
    }
  }

  // ── WebSocket Event Handlers ────────────────────────────────────────────

  ws.addEventListener("open", () => {
    log("WebSocket open; registering");
    const reg = { type: "register", cid, nick, lang };
    ws.send(JSON.stringify(reg));
  });

  ws.addEventListener("message", evt => {
    let msg;
    try {
      msg = JSON.parse(evt.data);
    } catch {
      log("Invalid JSON received:", evt.data);
      addSystemMessage("Error processing received message.");
      return;
    }
    log("Received message:", msg);

    switch (msg.type) {
      case "payload":
        // Peer payload → advance barrier stages
        if (msg.sender !== nick) {
          sendStages(msg.id, 2, 3, 4);
        }
        break;

      case "release":
        // Final release → render after delay
        {
          const isSelf = msg.sender === nick;
          const delayMs = Math.max(0, msg.delay || 0);
          setTimeout(() => addBubbleOnce(msg, isSelf), delayMs);
        }
        break;

      case "stage":
        // Stage updates are logged but not acted on UI
        log("Barrier stage update:", msg);
        break;

      case "join":
        addSystemMessage(`${msg.nick} has joined the chat.`);
        break;

      case "leave":
        addSystemMessage(`${msg.nick} has left the chat.`);
        break;

      case "error":
        addSystemMessage(`Server error: ${msg.message}`);
        break;

      default:
        log("Unknown message type:", msg.type);
    }
  });

  ws.addEventListener("error", evt => {
    log("WebSocket error:", evt);
    addSystemMessage("Connection error. Please refresh.");
  });

  ws.addEventListener("close", evt => {
    log("WebSocket closed:", evt.code, evt.reason);
    addSystemMessage("Connection closed. Please refresh.");
  });

  // ── Form Submission ────────────────────────────────────────────────────
  formEl.addEventListener("submit", ev => {
    ev.preventDefault();
    const text = inputEl.value.trim();
    if (!text) return;
    if (ws.readyState !== WebSocket.OPEN) {
      addSystemMessage("Cannot send: connection not open.");
      return;
    }
    sendPayload(text);
    inputEl.value = "";
    inputEl.focus();
  });
})();
