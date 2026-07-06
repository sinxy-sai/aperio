const runFeed = document.querySelector("#runFeed");
const refreshBtn = document.querySelector("#refreshBtn");
const routeFilter = document.querySelector("#routeFilter");
const healthStrip = document.querySelector("#healthStrip");
const pageTitle = document.querySelector("#pageTitle");
const metricGrid = document.querySelector("#metricGrid");
const agentGrid = document.querySelector("#agentGrid");
const timeline = document.querySelector("#timeline");
const fileList = document.querySelector("#fileList");
const agentCount = document.querySelector("#agentCount");
const eventCount = document.querySelector("#eventCount");
const fileCount = document.querySelector("#fileCount");
const returnChatLink = document.querySelector(".ghost-link");

let runs = [];
const initialParams = new URLSearchParams(window.location.search);
let selectedRunId = initialParams.get("run") || "";
let selectedSessionId = initialParams.get("session") || "";

if (returnChatLink && selectedSessionId) {
  returnChatLink.href = `/?session=${encodeURIComponent(selectedSessionId)}`;
}

function iconMarkup(name) {
  return `<i data-lucide="${name}" aria-hidden="true"></i>`;
}

function renderLucideIcons() {
  if (window.lucide) {
    window.lucide.createIcons({
      attrs: {
        "stroke-width": 2,
        "aria-hidden": "true",
      },
    });
  }
}

function setupStaticIcons() {
  if (returnChatLink) {
    returnChatLink.innerHTML = `${iconMarkup("arrow-left")}<span>返回聊天</span>`;
  }
  if (refreshBtn) {
    refreshBtn.innerHTML = `${iconMarkup("refresh-cw")}<span>刷新</span>`;
  }
  renderLucideIcons();
}

function setupCollapsibleSurfaces() {
  document.querySelectorAll(".surface").forEach((surface) => {
    const head = surface.querySelector(".surface-head");
    if (!head || head.querySelector(".collapse-button")) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "collapse-button";
    button.setAttribute("aria-label", "收纳面板");
    button.innerHTML = iconMarkup("minus");
    button.addEventListener("click", () => {
      const collapsed = surface.classList.toggle("collapsed");
      button.innerHTML = iconMarkup(collapsed ? "plus" : "minus");
      button.setAttribute("aria-label", collapsed ? "展开面板" : "收纳面板");
      renderLucideIcons();
    });
    head.append(button);
  });
  renderLucideIcons();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function formatSeconds(value) {
  return `${Number(value || 0).toFixed(1)}s`;
}

function setHealth(kind, text) {
  healthStrip.innerHTML = `<span class="dot ${kind}"></span><span>${escapeHtml(text)}</span>`;
}

function replaceObserveUrl() {
  const params = new URLSearchParams();
  if (selectedRunId) params.set("run", selectedRunId);
  if (selectedSessionId) params.set("session", selectedSessionId);
  const query = params.toString();
  history.replaceState(null, "", query ? `/observability?${query}` : "/observability");
}

async function loadHealth() {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    setHealth(data.ok ? "" : "error", `${data.engine || "agent"} · ${data.model || "model"}`);
  } catch (error) {
    setHealth("error", error.message);
  }
}

async function loadRuns() {
  refreshBtn.disabled = true;
  refreshBtn.textContent = "读取中";
  try {
    const response = await fetch("/api/runs?limit=100");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    runs = data.runs || [];
    if (!selectedRunId && runs[0]) {
      selectedRunId = runs[0].runId;
    }
    renderRunFeed();
    if (selectedRunId) {
      await loadRunDetail(selectedRunId);
    } else {
      renderEmptyDetail();
    }
  } catch (error) {
    runFeed.innerHTML = `<div class="empty-state">读取运行记录失败：${escapeHtml(error.message)}</div>`;
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.textContent = "刷新";
  }
}

function renderRunFeed() {
  const filter = routeFilter.value;
  const visibleRuns = filter ? runs.filter((item) => item.route === filter) : runs;
  if (!visibleRuns.length) {
    runFeed.innerHTML = '<div class="empty-state">没有符合条件的运行记录。</div>';
    return;
  }

  runFeed.innerHTML = "";
  for (const item of visibleRuns) {
    const button = document.createElement("button");
    button.className = "run-card";
    if (item.runId === selectedRunId) button.classList.add("active");
    button.type = "button";
    button.innerHTML = `
      <div class="run-topline">
        <span class="route-pill ${item.route === "error" ? "error" : ""}">${escapeHtml(item.route || "unknown")}</span>
        <span>${formatSeconds(item.durationSeconds)}</span>
      </div>
      <strong>${escapeHtml(item.runId)}</strong>
      <span>${formatNumber(item.modelCalls)} model · ${formatNumber(item.toolCalls)} tool · ${formatNumber(item.totalTokens)} tok</span>
    `;
    button.addEventListener("click", () => {
      selectedRunId = item.runId;
      replaceObserveUrl();
      renderRunFeed();
      loadRunDetail(item.runId);
    });
    runFeed.append(button);
  }
}

async function loadRunDetail(runId) {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  renderRunDetail(data);
}

function renderRunDetail(data) {
  const perf = data.performance || {};
  const obs = data.observability || perf.observability || {};
  const events = obs.events || [];
  const agents = obs.by_agent || {};
  const files = data.files || [];
  const artifacts = data.artifacts || [];

  pageTitle.textContent = data.runId || "运行详情";
  metricGrid.innerHTML = `
    <div class="metric"><span>模型调用</span><strong>${formatNumber(obs.model_calls || perf.model_calls)}</strong></div>
    <div class="metric"><span>工具调用</span><strong>${formatNumber(obs.tool_calls || perf.tool_calls)}</strong></div>
    <div class="metric"><span>总 Token</span><strong>${formatNumber(obs.total_tokens || perf.total_tokens)}</strong></div>
    <div class="metric"><span>耗时</span><strong>${formatSeconds(perf.duration_seconds)}</strong></div>
  `;

  renderAgents(agents);
  renderEvents(events);
  renderGroupedFiles(data.runId, artifacts, files);
}

function renderAgents(agents) {
  const entries = Object.entries(agents);
  agentCount.textContent = `${entries.length} agents`;
  if (!entries.length) {
    agentGrid.innerHTML = '<div class="empty-state">暂无 Agent 调用数据。旧版本运行可能只有 performance.json。</div>';
    return;
  }

  agentGrid.innerHTML = "";
  for (const [name, counts] of entries.sort((a, b) => totalCalls(b[1]) - totalCalls(a[1]))) {
    const model = Number(counts.model || 0);
    const tool = Number(counts.tool || 0);
    const total = Math.max(1, model + tool);
    const row = document.createElement("div");
    row.className = "agent-row";
    row.style.setProperty("--model-share", `${Math.max(1, model / total * 100)}fr`);
    row.style.setProperty("--tool-share", `${Math.max(1, tool / total * 100)}fr`);
    row.innerHTML = `
      <strong>${escapeHtml(name)}</strong>
      <div class="agent-bars" aria-hidden="true"><span></span><span></span></div>
      <div class="agent-counts"><span>${model} model</span><span>${tool} tool</span></div>
    `;
    agentGrid.append(row);
  }
}

function totalCalls(counts) {
  return Number(counts.model || 0) + Number(counts.tool || 0);
}

function renderEvents(events) {
  eventCount.textContent = `${events.length} events`;
  if (!events.length) {
    timeline.innerHTML = '<div class="empty-state">暂无模型或工具事件。</div>';
    return;
  }

  timeline.innerHTML = "";
  for (const event of events.slice().reverse()) {
    const type = event.type || "event";
    const title = event.tool ? `${type}: ${event.tool}` : type;
    const tokens = event.tokens
      ? ` · ${event.tokens.input_tokens || 0}/${event.tokens.output_tokens || 0} tok`
      : "";
    const error = event.error ? ` · error: ${event.error}` : "";
    const row = document.createElement("div");
    row.className = "event-row";
    row.innerHTML = `
      <span class="event-type ${type === "tool" ? "tool" : ""}">${escapeHtml(type.slice(0, 4))}</span>
      <div class="event-body">
        <strong>${escapeHtml(title)}</strong>
        <span class="event-meta">${escapeHtml(event.agent || "agent")}${tokens}${error}</span>
      </div>
      <span class="event-time">${Number(event.elapsed_ms || 0).toFixed(1)} ms</span>
    `;
    timeline.append(row);
  }
}

function renderFiles(runId, artifacts, files) {
  fileCount.textContent = `${files.length} files`;
  if (!files.length) {
    fileList.innerHTML = '<div class="empty-state">这次运行没有落盘文件。</div>';
    return;
  }

  const artifactPaths = new Set(artifacts.map((item) => item.path));
  fileList.innerHTML = "";
  for (const file of files) {
    const row = document.createElement("div");
    row.className = "file-row";
    const href = `/api/runs/${encodeURIComponent(runId)}/artifact?path=${encodeURIComponent(file.path)}`;
    const action = artifactPaths.has(file.path) ? `<a href="${href}">下载</a>` : `<a href="${href}">打开</a>`;
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(file.path)}</strong>
        <span class="file-meta">${formatNumber(file.size)} bytes</span>
      </div>
      ${action}
    `;
    fileList.append(row);
  }
}

function renderGroupedFiles(runId, artifacts, files) {
  fileCount.textContent = `${files.length} files`;
  if (!files.length) {
    fileList.innerHTML = '<div class="empty-state">这次运行没有落盘文件。</div>';
    return;
  }

  const artifactPaths = new Set(artifacts.map((item) => item.path));
  const groups = groupFiles(files, artifactPaths);
  fileList.innerHTML = "";
  for (const group of groups) {
    if (!group.files.length) continue;
    const section = document.createElement("section");
    section.className = "file-group";
    section.innerHTML = `
      <button class="file-group-head" type="button">
        <strong>${escapeHtml(group.label)}</strong>
        <span>${group.files.length} files</span>
      </button>
      <div class="file-group-body"></div>
    `;
    const body = section.querySelector(".file-group-body");
    section.querySelector(".file-group-head").addEventListener("click", () => {
      section.classList.toggle("collapsed");
    });
    for (const file of group.files) {
      body.append(renderFileRow(runId, file, artifactPaths));
    }
    fileList.append(section);
  }
}

function groupFiles(files, artifactPaths) {
  const groups = [
    { label: "主要产物", files: [] },
    { label: "outputs", files: [] },
    { label: "inputs", files: [] },
    { label: "skills", files: [] },
    { label: "其他文件", files: [] },
  ];
  for (const file of files) {
    const path = file.path || "";
    if (artifactPaths.has(path)) {
      groups[0].files.push(file);
    } else if (path.startsWith("outputs/")) {
      groups[1].files.push(file);
    } else if (path.startsWith("inputs/")) {
      groups[2].files.push(file);
    } else if (path.startsWith("skills/")) {
      groups[3].files.push(file);
    } else {
      groups[4].files.push(file);
    }
  }
  return groups;
}

function renderFileRow(runId, file, artifactPaths) {
  const row = document.createElement("div");
  row.className = "file-row";
  const href = `/api/runs/${encodeURIComponent(runId)}/artifact?path=${encodeURIComponent(file.path)}`;
  const action = artifactPaths.has(file.path) ? "下载" : "打开";
  row.innerHTML = `
    <div>
      <strong>${escapeHtml(file.path)}</strong>
      <span class="file-meta">${formatNumber(file.size)} bytes</span>
    </div>
    <a href="${href}">${action}</a>
  `;
  return row;
}

function renderEmptyDetail() {
  pageTitle.textContent = "暂无运行";
  metricGrid.innerHTML = `
    <div class="metric"><span>模型调用</span><strong>0</strong></div>
    <div class="metric"><span>工具调用</span><strong>0</strong></div>
    <div class="metric"><span>总 Token</span><strong>0</strong></div>
    <div class="metric"><span>耗时</span><strong>0.0s</strong></div>
  `;
  agentCount.textContent = "0 agents";
  eventCount.textContent = "0 events";
  fileCount.textContent = "0 files";
  agentGrid.innerHTML = '<div class="empty-state">暂无 Agent 调用数据。</div>';
  timeline.innerHTML = '<div class="empty-state">暂无模型或工具事件。</div>';
  fileList.innerHTML = '<div class="empty-state">运行 agent 后这里会显示落盘文件。</div>';
}

refreshBtn.addEventListener("click", loadRuns);
routeFilter.addEventListener("change", () => {
  selectedRunId = "";
  renderRunFeed();
  const firstVisible = routeFilter.value ? runs.find((item) => item.route === routeFilter.value) : runs[0];
  if (firstVisible) {
    selectedRunId = firstVisible.runId;
    replaceObserveUrl();
    renderRunFeed();
    loadRunDetail(selectedRunId);
  } else {
    selectedRunId = "";
    replaceObserveUrl();
    renderEmptyDetail();
  }
});

setupStaticIcons();
loadHealth();
setupCollapsibleSurfaces();
loadRuns();
