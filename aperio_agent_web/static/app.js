const conversation = document.querySelector("#conversation");
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const sendBtn = document.querySelector("#sendBtn");
const runMeta = document.querySelector("#runMeta");
const statusDot = document.querySelector("#statusDot");
const statusText = document.querySelector("#statusText");
const statusDetail = document.querySelector("#statusDetail");
const approvalMode = document.querySelector("#approvalMode");
const timeoutSeconds = document.querySelector("#timeoutSeconds");
const artifactList = document.querySelector("#artifactList");
const runId = document.querySelector("#runId");
const clearBtn = document.querySelector("#clearBtn");
const newChatBtn = document.querySelector("#newChatBtn");
const shell = document.querySelector(".shell");
const settingsToggle = document.querySelector("#settingsToggle");
const settingsPanel = document.querySelector("#settingsPanel");
const artifactToggle = document.querySelector("#artifactToggle");
const artifactClose = document.querySelector("#artifactClose");
const railToggle = document.querySelector("#railToggle");
const observeRefresh = document.querySelector("#observeRefresh");
const artifactTab = document.querySelector("#artifactTab");
const observeTab = document.querySelector("#observeTab");
const artifactPage = document.querySelector("#artifactPage");
const observePage = document.querySelector("#observePage");
const observeSummary = document.querySelector("#observeSummary");
const runList = document.querySelector("#runList");
const eventList = document.querySelector("#eventList");
const recentSessions = document.querySelector("#recentSessions");
const observeNavLink = document.querySelector(".nav-link");

let selectedRunId = "";
let isRunning = false;
const SESSION_STORE_KEY = "aperio.chat.sessions.v1";
const ACTIVE_SESSION_KEY = "aperio.chat.activeSession";
let chatSessions = [];
let activeSessionId = "";

function assignIcon(selector, name, root = document) {
  const element = root.querySelector(selector);
  if (!element) return;
  element.textContent = "";
  element.setAttribute("data-lucide", name);
}

function renderLucideIcons(root = document) {
  assignIcon("#newChatBtn .nav-icon", "plus", root);
  assignIcon(".nav-link .nav-icon", "activity", root);
  assignIcon(".nav-section:not(.muted-list) button:nth-of-type(1) .nav-icon", "cloud-sun", root);
  assignIcon(".nav-section:not(.muted-list) button:nth-of-type(2) .nav-icon", "file-text", root);
  assignIcon(".nav-section:not(.muted-list) button:nth-of-type(3) .nav-icon", "code-xml", root);
  assignIcon("#railToggle span", "menu", root);
  assignIcon("#artifactToggle span", "panel-right", root);
  assignIcon("#settingsToggle span", "settings", root);
  assignIcon("#observeRefresh span", "refresh-cw", root);
  assignIcon("#artifactClose span", "chevron-right", root);
  assignIcon("#welcomePanel .suggestions button:nth-child(1) span", "file-text", root);
  assignIcon("#welcomePanel .suggestions button:nth-child(2) span", "code-xml", root);
  assignIcon("#welcomePanel .suggestions button:nth-child(3) span", "cloud-sun", root);
  if (window.lucide) {
    window.lucide.createIcons({
      attrs: {
        "stroke-width": 2,
        "aria-hidden": "true",
      },
    });
  }
}

function loadStoredSessions() {
  try {
    const stored = JSON.parse(localStorage.getItem(SESSION_STORE_KEY) || "[]");
    chatSessions = Array.isArray(stored) ? stored : [];
  } catch {
    chatSessions = [];
  }
}

function saveSessions() {
  const ordered = chatSessions
    .sort((a, b) => Number(b.updatedAt || 0) - Number(a.updatedAt || 0))
    .slice(0, 30);
  chatSessions = ordered;
  localStorage.setItem(SESSION_STORE_KEY, JSON.stringify(ordered));
  localStorage.setItem(ACTIVE_SESSION_KEY, activeSessionId);
}

function createSession() {
  const session = {
    id: `chat_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
    title: "新对话",
    messages: [],
    runId: "",
    updatedAt: Date.now(),
  };
  chatSessions.unshift(session);
  activeSessionId = session.id;
  saveSessions();
  return session;
}

function activeSession() {
  let session = chatSessions.find((item) => item.id === activeSessionId);
  if (!session) {
    session = createSession();
  }
  return session;
}

function touchActiveSession() {
  activeSession().updatedAt = Date.now();
  saveSessions();
  renderRecentSessions();
}

function updateSessionTitle(message) {
  const session = activeSession();
  if (session.title === "新对话") {
    session.title = message.slice(0, 24) || "新对话";
  }
}

function rememberMessage(role, text) {
  const session = activeSession();
  session.messages.push({ role, text });
  updateSessionTitle(text);
  touchActiveSession();
}

function setSessionRun(runIdValue) {
  if (!runIdValue) return;
  const session = activeSession();
  session.runId = runIdValue;
  selectedRunId = runIdValue;
  touchActiveSession();
}

function selectSession(sessionId) {
  activeSessionId = sessionId;
  const session = activeSession();
  selectedRunId = session.runId || "";
  saveSessions();
  history.replaceState(null, "", `/?session=${encodeURIComponent(activeSessionId)}`);
  renderSessionMessages(session);
  renderRecentSessions();
}

function renderSessionMessages(session) {
  if (!session.messages.length) {
    resetConversation();
    return;
  }
  conversation.innerHTML = "";
  for (const message of session.messages) {
    addMessage(message.role, message.text, { persist: false });
  }
}

function renderRecentSessions() {
  if (!recentSessions) return;
  const items = chatSessions.slice(0, 8);
  if (!items.length) {
    recentSessions.innerHTML = "<span>暂无会话</span>";
    return;
  }
  recentSessions.innerHTML = "";
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "recent-session";
    if (item.id === activeSessionId) button.classList.add("active");
    button.innerHTML = `
      <strong>${escapeHtml(item.title || "新对话")}</strong>
      <small>${escapeHtml(item.runId || "未运行")}</small>
    `;
    button.addEventListener("click", () => selectSession(item.id));
    recentSessions.append(button);
  }
}

function initializeSession() {
  loadStoredSessions();
  const requested = new URLSearchParams(window.location.search).get("session");
  const storedActive = localStorage.getItem(ACTIVE_SESSION_KEY);
  activeSessionId = requested || storedActive || "";
  if (!chatSessions.find((item) => item.id === activeSessionId)) {
    createSession();
  }
  const session = activeSession();
  selectedRunId = session.runId || "";
  renderSessionMessages(session);
  renderRecentSessions();
  renderLucideIcons();
}

function hideWelcome() {
  const panel = document.querySelector("#welcomePanel");
  if (panel) {
    panel.hidden = true;
  }
}

function resetConversation() {
  conversation.innerHTML = `
    <div class="welcome" id="welcomePanel">
      <h2>有什么我能帮你的吗？</h2>
      <div class="suggestions">
        <button data-example="为「智慧校园导航助手」写一份 PRD 并评审。">
          <span aria-hidden="true">□</span>
          写 PRD 并评审
        </button>
        <button data-example="对当前项目做一次代码健康检查。">
          <span aria-hidden="true">⌁</span>
          检查代码健康
        </button>
        <button data-example="我在珠海，明天天气如何？要带伞吗？">
          <span aria-hidden="true">☁</span>
          查询天气建议
        </button>
      </div>
    </div>
    <article class="message assistant">
      <div class="avatar">AI</div>
      <div class="bubble">
        <p>输入一个任务，我会调用本地 Aperio agent，并在完成后展示回答、运行目录和产物预览。</p>
      </div>
    </article>
  `;
  bindExampleButtons(conversation);
  renderLucideIcons(conversation);
}

function setStatus(kind, text, detail) {
  statusDot.className = `status-dot ${kind}`;
  statusText.textContent = text;
  statusDetail.textContent = detail;
}

function addMessage(role, text, options = {}) {
  hideWelcome();
  const article = document.createElement("article");
  article.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "ME" : "AI";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;

  article.append(avatar, bubble);
  conversation.append(article);
  conversation.scrollTop = conversation.scrollHeight;
  if (options.persist !== false) {
    rememberMessage(role, text);
  }
  return bubble;
}

function renderArtifacts(data) {
  shell.classList.remove("artifacts-collapsed");
  artifactToggle.setAttribute("aria-label", "收起产物面板");
  runId.textContent = data.run_id || "无运行目录";
  selectedRunId = data.run_id || selectedRunId;
  artifactList.innerHTML = "";

  if (!data.artifacts || data.artifacts.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "本次运行没有发现可预览产物。";
    artifactList.append(empty);
    return;
  }

  for (const artifact of data.artifacts) {
    const card = document.createElement("section");
    card.className = "artifact-card";

    const title = document.createElement("div");
    title.className = "artifact-title";

    const name = document.createElement("span");
    name.textContent = `${artifact.path} · ${artifact.size} bytes`;

    const link = document.createElement("a");
    link.href = `/api/runs/${encodeURIComponent(data.run_id)}/artifact?path=${encodeURIComponent(artifact.path)}`;
    link.textContent = "下载";

    const pre = document.createElement("pre");
    pre.textContent = artifact.preview || "(empty)";

    title.append(name, link);
    card.append(title, pre);
    artifactList.append(card);
  }
}

async function refreshCurrentArtifacts() {
  let targetRunId = selectedRunId;
  if (!targetRunId) {
    const listResponse = await fetch("/api/runs?limit=1");
    if (!listResponse.ok) throw new Error(`HTTP ${listResponse.status}`);
    const listData = await listResponse.json();
    targetRunId = listData.runs && listData.runs[0] && listData.runs[0].runId;
  }
  if (!targetRunId) {
    runMeta.textContent = "暂无可刷新的运行";
    return;
  }

  runMeta.textContent = "正在刷新产物";
  const response = await fetch(`/api/runs/${encodeURIComponent(targetRunId)}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const detail = await response.json();
  renderArtifacts({
    run_id: detail.runId,
    artifacts: detail.artifacts || [],
  });
  setSessionRun(detail.runId);
  runMeta.textContent = "产物已刷新";
}

function setPanelTab(tabName) {
  const observeActive = tabName === "observe";
  artifactTab.classList.toggle("active", !observeActive);
  observeTab.classList.toggle("active", observeActive);
  artifactPage.classList.toggle("active", !observeActive);
  observePage.classList.toggle("active", observeActive);
  if (observeActive) {
    loadRuns(selectedRunId);
  }
}

async function loadRuns(preferredRunId = "") {
  try {
    const res = await fetch("/api/runs?limit=30");
    const data = await res.json();
    renderRunList(data.runs || [], preferredRunId);
    const target = preferredRunId || (data.runs && data.runs[0] && data.runs[0].runId);
    if (target) {
      await loadRunDetail(target);
    }
  } catch (error) {
    runList.innerHTML = `<div class="empty-state">无法读取运行记录：${escapeHtml(error.message)}</div>`;
  }
}

function renderRunList(runs, preferredRunId = "") {
  runList.innerHTML = "";
  if (!runs.length) {
    runList.innerHTML = '<div class="empty-state">暂无运行记录。</div>';
    return;
  }
  for (const item of runs) {
    const button = document.createElement("button");
    button.className = "run-item";
    if (item.runId === preferredRunId) button.classList.add("active");
    button.type = "button";
    button.innerHTML = `
      <span class="run-route">${escapeHtml(item.route || "unknown")}</span>
      <strong>${escapeHtml(item.runId)}</strong>
      <span>${Number(item.durationSeconds || 0).toFixed(1)}s · ${item.artifactCount || 0} files</span>
    `;
    button.addEventListener("click", () => loadRunDetail(item.runId));
    runList.append(button);
  }
}

async function loadRunDetail(id) {
  selectedRunId = id;
  runId.textContent = id;
  const res = await fetch(`/api/runs/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  renderObservation(data);
  document.querySelectorAll(".run-item").forEach((item) => {
    item.classList.toggle("active", item.textContent.includes(id));
  });
}

function renderObservation(data) {
  const perf = data.performance || {};
  const obs = data.observability || perf.observability || {};
  observeSummary.innerHTML = `
    <div class="metric"><span>模型</span><strong>${Number(obs.model_calls || perf.model_calls || 0)}</strong></div>
    <div class="metric"><span>工具</span><strong>${Number(obs.tool_calls || perf.tool_calls || 0)}</strong></div>
    <div class="metric"><span>Token</span><strong>${Number(obs.total_tokens || perf.total_tokens || 0)}</strong></div>
    <div class="metric wide"><span>耗时</span><strong>${Number(perf.duration_seconds || 0).toFixed(1)}s</strong></div>
  `;

  const events = obs.events || [];
  if (!events.length) {
    eventList.innerHTML = '<div class="empty-state">这次运行没有记录到模型或工具事件。旧版本运行可能只有 performance.json。</div>';
    return;
  }

  eventList.innerHTML = "";
  for (const event of events.slice().reverse()) {
    const row = document.createElement("div");
    row.className = `event-row ${event.type || ""}`;
    const title = event.tool ? `${event.type}: ${event.tool}` : event.type || "event";
    const tokens = event.tokens ? ` · ${event.tokens.input_tokens || 0}/${event.tokens.output_tokens || 0} tok` : "";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(event.agent || "agent")}${tokens}</span>
      </div>
      <em>${Number(event.elapsed_ms || 0).toFixed(1)} ms</em>
    `;
    eventList.append(row);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function bindExampleButtons(root = document) {
  root.querySelectorAll("[data-example]").forEach((button) => {
    button.addEventListener("click", () => {
      input.value = button.dataset.example;
      input.focus();
    });
  });
}

async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    if (data.ok) {
      setStatus("ok", "已连接", `${data.engine || "agent"} · ${data.model || "model"}`);
    } else {
      setStatus("error", "未配置", "请配置后端模型 API Key");
    }
  } catch (error) {
    setStatus("error", "服务异常", error.message);
  }
}

async function submitTask(event) {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;

  addMessage("user", message);
  input.value = "";
  sendBtn.disabled = true;
  sendBtn.textContent = "运行中";
  runMeta.textContent = "agent 正在处理";

  const startedAt = performance.now();
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        approval_mode: approvalMode.value,
        timeout_seconds: Number(timeoutSeconds.value || 900),
      }),
    });

    const data = await res.json();
    if (!res.ok) {
      const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail, null, 2);
      throw new Error(detail);
    }

    const answer = data.answer || (data.ok ? "运行完成，但没有提取到最终回答。" : "运行失败，请查看日志尾部。");
    addMessage("assistant", answer);
    renderArtifacts(data);
    selectedRunId = data.run_id || selectedRunId;

    const seconds = ((performance.now() - startedAt) / 1000).toFixed(1);
    runMeta.textContent = `完成 · ${seconds}s · code ${data.return_code}`;
    setStatus(data.ok ? "ok" : "error", data.ok ? "运行完成" : "运行失败", data.run_id || "无运行目录");

    if (!data.ok && data.stderr_tail) {
      addMessage("assistant", `错误日志：\n${data.stderr_tail}`);
    }
  } catch (error) {
    addMessage("assistant", `请求失败：${error.message}`);
    setStatus("error", "请求失败", "查看对话中的错误");
    runMeta.textContent = "运行失败";
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = "运行";
  }
}

async function submitTaskStream(event) {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || isRunning) return;

  addMessage("user", message);
  const assistantBubble = addMessage("assistant", "正在连接后端...");
  input.value = "";
  isRunning = true;
  activeSession().messages.pop();
  saveSessions();
  sendBtn.disabled = true;
  sendBtn.textContent = "运行中";
  runMeta.textContent = "agent 正在处理";

  const startedAt = performance.now();
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        approval_mode: approvalMode.value,
        timeout_seconds: Number(timeoutSeconds.value || 900),
      }),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data, null, 2);
      throw new Error(detail);
    }
    if (!res.body) {
      throw new Error("当前浏览器不支持流式响应");
    }

    await readChatStream(res.body, assistantBubble, startedAt);
  } catch (error) {
    assistantBubble.textContent = `请求失败：${error.message}`;
    setStatus("error", "请求失败", "查看对话中的错误");
    runMeta.textContent = "运行失败";
  } finally {
    isRunning = false;
    sendBtn.disabled = false;
    sendBtn.textContent = "运行";
  }
}

async function readChatStream(body, assistantBubble, startedAt) {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      await handleStreamEvent(JSON.parse(line), assistantBubble, startedAt);
    }
  }
  if (buffer.trim()) {
    await handleStreamEvent(JSON.parse(buffer), assistantBubble, startedAt);
  }
}

async function handleStreamEvent(event, assistantBubble, startedAt) {
  if (event.type === "error") {
    throw new Error(event.message || "后端运行失败");
  }

  if (event.type === "status") {
    assistantBubble.textContent = event.message || "agent 正在运行";
    runMeta.textContent = `运行中 · ${Number(event.elapsed || 0).toFixed(1)}s`;
    conversation.scrollTop = conversation.scrollHeight;
    return;
  }

  if (event.type !== "result") return;
  const data = event.data || {};
  const answer = data.answer || (data.ok ? "运行完成，但没有提取到最终回答。" : "运行失败，请查看日志尾部。");
  await renderTextIncrementally(assistantBubble, answer);
  rememberMessage("assistant", answer);
  renderArtifacts(data);
  setSessionRun(data.run_id);

  const seconds = ((performance.now() - startedAt) / 1000).toFixed(1);
  runMeta.textContent = `完成 · ${seconds}s · code ${data.return_code}`;
  setStatus(data.ok ? "ok" : "error", data.ok ? "运行完成" : "运行失败", data.run_id || "无运行目录");

  if (!data.ok && data.stderr_tail) {
    addMessage("assistant", `错误日志：\n${data.stderr_tail}`);
  }
}

async function renderTextIncrementally(element, text) {
  element.textContent = "";
  const chunkSize = 4;
  for (let index = 0; index < text.length; index += chunkSize) {
    element.textContent += text.slice(index, index + chunkSize);
    conversation.scrollTop = conversation.scrollHeight;
    await new Promise((resolve) => setTimeout(resolve, 8));
  }
}

function openObservability() {
  const params = new URLSearchParams();
  if (selectedRunId) params.set("run", selectedRunId);
  if (activeSessionId) params.set("session", activeSessionId);
  const query = params.toString();
  const href = query ? `/observability?${query}` : "/observability";
  window.location.href = href;
}

function clearWorkspace() {
  const session = activeSession();
  session.messages = [];
  session.runId = "";
  session.title = "新对话";
  selectedRunId = "";
  touchActiveSession();
  resetConversation();
  artifactList.innerHTML = '<div class="empty-state">完成一次任务后，这里会显示 Markdown 报告和性能文件。</div>';
  runId.textContent = "未运行";
  runMeta.textContent = "等待任务";
}

function startNewChat() {
  createSession();
  history.replaceState(null, "", `/?session=${encodeURIComponent(activeSessionId)}`);
  clearWorkspace();
}

clearBtn.addEventListener("click", clearWorkspace);
newChatBtn.addEventListener("click", startNewChat);
if (observeNavLink) {
  observeNavLink.addEventListener("click", (event) => {
    event.preventDefault();
    openObservability();
  });
}
observeRefresh.addEventListener("click", () => {
  refreshCurrentArtifacts().catch((error) => {
    runMeta.textContent = `刷新失败：${error.message}`;
  });
});
artifactTab.addEventListener("click", () => setPanelTab("artifact"));
if (observeTab) {
  observeTab.remove();
}

settingsToggle.addEventListener("click", (event) => {
  event.stopPropagation();
  const nextHidden = !settingsPanel.hidden;
  settingsPanel.hidden = nextHidden;
  settingsToggle.setAttribute("aria-expanded", String(!nextHidden));
});

settingsPanel.addEventListener("click", (event) => {
  event.stopPropagation();
});

document.addEventListener("click", () => {
  settingsPanel.hidden = true;
  settingsToggle.setAttribute("aria-expanded", "false");
});

artifactToggle.addEventListener("click", () => {
  const collapsed = shell.classList.toggle("artifacts-collapsed");
  artifactToggle.setAttribute("aria-label", collapsed ? "打开产物面板" : "收起产物面板");
});

artifactClose.addEventListener("click", () => {
  shell.classList.add("artifacts-collapsed");
  artifactToggle.setAttribute("aria-label", "打开产物面板");
});

railToggle.addEventListener("click", () => {
  shell.classList.toggle("rail-collapsed");
});

form.addEventListener("submit", submitTaskStream);
initializeSession();
checkHealth();
