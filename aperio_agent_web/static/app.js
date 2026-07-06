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
}

function setStatus(kind, text, detail) {
  statusDot.className = `status-dot ${kind}`;
  statusText.textContent = text;
  statusDetail.textContent = detail;
}

function addMessage(role, text) {
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
}

function renderArtifacts(data) {
  shell.classList.remove("artifacts-collapsed");
  artifactToggle.setAttribute("aria-label", "收起产物面板");
  runId.textContent = data.run_id || "无运行目录";
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
      setStatus("ok", "已连接", `${data.backend || "agent backend"} · ${data.model || "model"}`);
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

bindExampleButtons();

function clearWorkspace() {
  resetConversation();
  artifactList.innerHTML = '<div class="empty-state">完成一次任务后，这里会显示 Markdown 报告和性能文件。</div>';
  runId.textContent = "未运行";
  runMeta.textContent = "等待任务";
}

clearBtn.addEventListener("click", clearWorkspace);
newChatBtn.addEventListener("click", clearWorkspace);

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

form.addEventListener("submit", submitTask);
checkHealth();
