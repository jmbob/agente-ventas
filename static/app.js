const chat = document.getElementById("chat");
const welcome = document.getElementById("welcome");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const resetBtn = document.getElementById("reset");

const STORAGE_KEY = "agente-ventas-conversation";
let conversationId = null;
let busy = false;

try { conversationId = sessionStorage.getItem(STORAGE_KEY); } catch (_) {}

fetch("/api/info")
  .then((r) => r.json())
  .then((info) => { document.getElementById("model").textContent = info.model; })
  .catch(() => {});

function renderMarkdown(text) {
  if (window.marked && window.DOMPurify) {
    return DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: true }));
  }
  const div = document.createElement("div");
  div.style.whiteSpace = "pre-wrap";
  div.textContent = text;
  return div.outerHTML;
}

function scrollToBottom() {
  chat.scrollTop = chat.scrollHeight;
}

function addMessage(role) {
  welcome.hidden = true;
  const wrapper = document.createElement("div");
  wrapper.className = `msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  wrapper.appendChild(bubble);
  chat.appendChild(wrapper);
  return bubble;
}

function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
  resetBtn.disabled = value;
  document.querySelectorAll(".suggestion").forEach((b) => (b.disabled = value));
}

async function ask(question) {
  if (busy || !question.trim()) return;
  setBusy(true);

  addMessage("user").textContent = question;

  const bubble = addMessage("assistant");
  const queries = document.createElement("div");
  queries.className = "queries";
  const body = document.createElement("div");
  const typing = document.createElement("div");
  typing.className = "typing";
  typing.textContent = "Pensando";
  bubble.append(queries, body, typing);
  scrollToBottom();

  let text = "";
  let queryCount = 0;

  const showError = (message) => {
    const err = document.createElement("div");
    err.className = "error";
    err.textContent = `⚠️ ${message}`;
    bubble.appendChild(err);
  };

  const handle = (event) => {
    switch (event.type) {
      case "start":
        conversationId = event.conversation_id;
        try { sessionStorage.setItem(STORAGE_KEY, conversationId); } catch (_) {}
        break;
      case "tool": {
        queryCount += 1;
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = `Consulta SQL ${queryCount}`;
        const pre = document.createElement("pre");
        pre.textContent = event.sql;
        details.append(summary, pre);
        queries.appendChild(details);
        typing.textContent = "Consultando la base de datos";
        break;
      }
      case "delta":
        text += event.text;
        body.innerHTML = renderMarkdown(text);
        typing.textContent = "Escribiendo";
        break;
      case "error":
        showError(event.message);
        break;
    }
    scrollToBottom();
  };

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: conversationId, message: question }),
    });
    if (!response.ok) {
      let detail = `Error ${response.status}`;
      try { detail = (await response.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const chunk = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const data = chunk.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
        if (data) handle(JSON.parse(data));
      }
    }
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    typing.remove();
    if (!text && !bubble.querySelector(".error")) {
      body.textContent = "(sin respuesta)";
    }
    setBusy(false);
    scrollToBottom();
    input.focus();
  }
}

function autoGrow() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const question = input.value;
  if (busy || !question.trim()) return;
  input.value = "";
  autoGrow();
  ask(question);
});

input.addEventListener("input", autoGrow);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll(".suggestion").forEach((btn) => {
  btn.addEventListener("click", () => ask(btn.textContent));
});

resetBtn.addEventListener("click", async () => {
  if (busy) return;
  if (conversationId) {
    fetch("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: conversationId }),
    }).catch(() => {});
  }
  conversationId = null;
  try { sessionStorage.removeItem(STORAGE_KEY); } catch (_) {}
  chat.querySelectorAll(".msg").forEach((m) => m.remove());
  welcome.hidden = false;
  input.focus();
});

input.focus();
