import {
  Activity,
  ArrowLeft,
  ChevronRight,
  CloudSun,
  CodeXml,
  FileText,
  Folder,
  Menu,
  Minus,
  PanelRight,
  Plus,
  RefreshCw,
  Settings,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

const SESSION_STORE_KEY = "aperio.chat.sessions.v2";
const LEGACY_SESSION_STORE_KEY = "aperio.chat.sessions.v1";
const ACTIVE_SESSION_KEY = "aperio.chat.activeSession";

function newSession() {
  return {
    id: `chat_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
    title: "新对话",
    messages: [],
    runId: "",
    updatedAt: Date.now(),
  };
}

function loadSessions() {
  for (const key of [SESSION_STORE_KEY, LEGACY_SESSION_STORE_KEY]) {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "[]");
      if (Array.isArray(value) && value.length) return value;
    } catch {
      // Ignore corrupted local state and recreate it.
    }
  }
  return [newSession()];
}

function saveSessions(sessions, activeId) {
  const ordered = [...sessions]
    .sort((a, b) => Number(b.updatedAt || 0) - Number(a.updatedAt || 0))
    .slice(0, 40);
  localStorage.setItem(SESSION_STORE_KEY, JSON.stringify(ordered));
  localStorage.setItem(ACTIVE_SESSION_KEY, activeId);
  return ordered;
}

function App() {
  const [path, setPath] = useState(window.location.pathname);

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback((href) => {
    window.history.pushState(null, "", href);
    setPath(window.location.pathname);
  }, []);

  return path.startsWith("/observability") ? (
    <ObservabilityPage navigate={navigate} />
  ) : (
    <ChatPage navigate={navigate} />
  );
}

function ChatPage({ navigate }) {
  const [sessions, setSessions] = useState(() => loadSessions());
  const [activeId, setActiveId] = useState(() => {
    const requested = new URLSearchParams(window.location.search).get("session");
    const stored = localStorage.getItem(ACTIVE_SESSION_KEY);
    return requested || stored || "";
  });
  const [health, setHealth] = useState(null);
  const [input, setInput] = useState("");
  const [approvalMode, setApprovalMode] = useState("approve");
  const [timeoutSeconds, setTimeoutSeconds] = useState(900);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [artifactsCollapsed, setArtifactsCollapsed] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [artifacts, setArtifacts] = useState([]);
  const [runMeta, setRunMeta] = useState("等待任务");
  const [running, setRunning] = useState(false);
  const [draftAssistant, setDraftAssistant] = useState("");

  const activeSession = useMemo(() => {
    return sessions.find((item) => item.id === activeId) || sessions[0];
  }, [activeId, sessions]);

  useEffect(() => {
    if (!activeSession) {
      const session = newSession();
      setSessions([session]);
      setActiveId(session.id);
      return;
    }
    setSelectedRunId(activeSession.runId || "");
    const query = `/?session=${encodeURIComponent(activeSession.id)}`;
    if (window.location.pathname === "/" && window.location.search !== query.slice(1)) {
      window.history.replaceState(null, "", query);
    }
    saveSessions(sessions, activeSession.id);
  }, [activeSession, sessions]);

  useEffect(() => {
    fetch("/api/health")
      .then((response) => response.json())
      .then(setHealth)
      .catch((error) => setHealth({ ok: false, error: error.message }));
  }, []);

  function updateSessions(updater) {
    setSessions((current) => {
      const next = updater(current);
      saveSessions(next, activeId || next[0]?.id || "");
      return next;
    });
  }

  function patchActiveSession(patch) {
    updateSessions((current) =>
      current.map((session) =>
        session.id === activeSession.id
          ? { ...session, ...patch, updatedAt: Date.now() }
          : session,
      ),
    );
  }

  function addMessage(role, text) {
    const nextMessages = [...(activeSession?.messages || []), { role, text }];
    const title =
      activeSession?.title && activeSession.title !== "新对话"
        ? activeSession.title
        : text.slice(0, 28) || "新对话";
    patchActiveSession({ messages: nextMessages, title });
  }

  function selectSession(id) {
    const session = sessions.find((item) => item.id === id);
    if (!session) return;
    setActiveId(id);
    setSelectedRunId(session.runId || "");
    setArtifacts([]);
    setDraftAssistant("");
    saveSessions(sessions, id);
    navigate(`/?session=${encodeURIComponent(id)}`);
  }

  function createChat() {
    const session = newSession();
    const next = saveSessions([session, ...sessions], session.id);
    setSessions(next);
    setActiveId(session.id);
    setArtifacts([]);
    setSelectedRunId("");
    setDraftAssistant("");
    setRunMeta("等待任务");
    navigate(`/?session=${encodeURIComponent(session.id)}`);
  }

  function clearChat() {
    patchActiveSession({ title: "新对话", messages: [], runId: "" });
    setArtifacts([]);
    setSelectedRunId("");
    setDraftAssistant("");
    setRunMeta("等待任务");
  }

  function deleteSession(id) {
    const remaining = sessions.filter((item) => item.id !== id);
    const nextSessions = remaining.length ? remaining : [newSession()];
    const nextActive = id === activeSession?.id ? nextSessions[0].id : activeSession?.id;
    const ordered = saveSessions(nextSessions, nextActive);
    setSessions(ordered);
    setActiveId(nextActive);
    if (id === activeSession?.id) {
      setArtifacts([]);
      setSelectedRunId(ordered[0]?.runId || "");
      setDraftAssistant("");
      navigate(`/?session=${encodeURIComponent(nextActive)}`);
    }
  }

  function openObservability() {
    const params = new URLSearchParams();
    if (selectedRunId) params.set("run", selectedRunId);
    if (activeSession?.id) params.set("session", activeSession.id);
    navigate(`/observability${params.toString() ? `?${params}` : ""}`);
  }

  async function refreshArtifacts() {
    let runId = selectedRunId;
    if (!runId) {
      const data = await fetchJson("/api/runs?limit=1");
      runId = data.runs?.[0]?.runId || "";
    }
    if (!runId) {
      setRunMeta("暂无可刷新的运行");
      return;
    }
    setRunMeta("正在刷新产物");
    const detail = await fetchJson(`/api/runs/${encodeURIComponent(runId)}`);
    setArtifacts(detail.artifacts || []);
    setSelectedRunId(detail.runId);
    patchActiveSession({ runId: detail.runId });
    setRunMeta("产物已刷新");
  }

  async function submitTask(event) {
    event.preventDefault();
    const message = input.trim();
    if (!message || running) return;

    addMessage("user", message);
    setInput("");
    setRunning(true);
    setDraftAssistant("正在连接后端...");
    setRunMeta("agent 正在处理");

    try {
      const response = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          approval_mode: approvalMode,
          timeout_seconds: Number(timeoutSeconds || 900),
        }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${response.status}`);
      }
      if (!response.body) throw new Error("当前浏览器不支持流式响应");

      await readNdjson(response.body, async (eventData) => {
        if (eventData.type === "status") {
          setDraftAssistant(eventData.message || "agent 正在运行");
          setRunMeta(`运行中 · ${Number(eventData.elapsed || 0).toFixed(1)}s`);
          return;
        }
        if (eventData.type === "error") {
          throw new Error(eventData.message || "后端运行失败");
        }
        if (eventData.type !== "result") return;

        const result = eventData.data || {};
        const answer = result.answer || (result.ok ? "运行完成。" : "运行失败。");
        setDraftAssistant("");
        addMessage("assistant", answer);
        setArtifacts(result.artifacts || []);
        setSelectedRunId(result.run_id || "");
        patchActiveSession({ runId: result.run_id || "" });
        setRunMeta(`完成 · ${Number(result.duration_seconds || 0).toFixed(1)}s · code ${result.return_code}`);
      });
    } catch (error) {
      setDraftAssistant("");
      addMessage("assistant", `请求失败：${error.message}`);
      setRunMeta("运行失败");
    } finally {
      setRunning(false);
    }
  }

  const displayMessages = [...(activeSession?.messages || [])];
  if (draftAssistant) displayMessages.push({ role: "assistant", text: draftAssistant });

  return (
    <main className={`shell ${railCollapsed ? "rail-collapsed" : ""} ${artifactsCollapsed ? "artifacts-collapsed" : ""}`}>
      <aside className="sidebar">
        <Brand subtitle="Agent Workbench" />
        <button className="nav-primary" type="button" onClick={createChat}>
          <Plus size={20} />
          <span>新对话</span>
        </button>
        <button className="nav-primary nav-link" type="button" onClick={openObservability}>
          <Activity size={20} />
          <span>观测平台</span>
        </button>

        <div className="nav-section">
          <p>快捷任务</p>
          <ExampleButton icon={<CloudSun size={20} />} label="天气问答" onUse={setInput} value="我在珠海，明天天气如何？要带伞吗？" />
          <ExampleButton icon={<FileText size={20} />} label="PRD 评审" onUse={setInput} value="为智慧校园导航助手写 PRD 并评审。" />
          <ExampleButton icon={<CodeXml size={20} />} label="代码体检" onUse={setInput} value="对当前项目做一次代码健康检查。" />
        </div>

        <div className="nav-section session-section">
          <p>最近</p>
          <div className="recent-sessions">
            {sessions.slice(0, 10).map((session) => (
              <div className={`recent-session-row ${session.id === activeSession?.id ? "active" : ""}`} key={session.id}>
                <button className="recent-session" type="button" onClick={() => selectSession(session.id)}>
                  <strong>{session.title || "新对话"}</strong>
                  <small>{session.runId || "未运行"}</small>
                </button>
                <button className="recent-delete" type="button" onClick={() => deleteSession(session.id)} title="删除会话">
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
          </div>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <button className="icon-button" type="button" onClick={() => setRailCollapsed((value) => !value)} aria-label="折叠侧栏">
            <Menu size={20} />
          </button>
          <div>
            <h2>{activeSession?.title || "新对话"}</h2>
            <p className="top-hint">AI 生成内容请谨慎核实</p>
          </div>
          <div className="top-actions">
            <button className="icon-button" type="button" onClick={() => setArtifactsCollapsed((value) => !value)} aria-label="切换产物面板">
              <PanelRight size={20} />
            </button>
            <button className="icon-button" type="button" onClick={() => setSettingsOpen((value) => !value)} aria-label="设置">
              <Settings size={20} />
            </button>
            <button className="ghost" type="button" onClick={clearChat}>清空</button>
          </div>
          {settingsOpen && (
            <div className="settings-panel">
              <label htmlFor="approvalMode">审批模式</label>
              <select id="approvalMode" value={approvalMode} onChange={(event) => setApprovalMode(event.target.value)}>
                <option value="approve">自动批准</option>
                <option value="reject">自动拒绝</option>
              </select>
              <label htmlFor="timeoutSeconds">超时秒数</label>
              <input id="timeoutSeconds" type="number" min="30" max="3600" value={timeoutSeconds} onChange={(event) => setTimeoutSeconds(event.target.value)} />
            </div>
          )}
        </header>

        <div className="conversation">
          {!displayMessages.length && <Welcome onUse={setInput} />}
          {displayMessages.map((message, index) => (
            <Message key={`${message.role}-${index}`} role={message.role} text={message.text} />
          ))}
        </div>

        <form className="composer" onSubmit={submitTask}>
          <div className="composer-status">
            <span className={`status-dot ${health?.ok ? "ok" : health?.error ? "error" : ""}`} />
            <strong>{health?.ok ? "已连接" : health?.error ? "服务异常" : "连接中"}</strong>
            <span>{health ? `${health.engine || "agent"} · ${health.model || "model"}` : "检查本地服务"}</span>
          </div>
          <textarea value={input} onChange={(event) => setInput(event.target.value)} rows={4} placeholder="例如：为智慧校园导航助手写 PRD 并评审" />
          <div className="composer-actions">
            <span>{runMeta}</span>
            <button id="sendBtn" type="submit" disabled={running}>{running ? "运行中" : "运行"}</button>
          </div>
        </form>
      </section>

      <aside className="artifacts">
        <div className="artifact-head">
          <div>
            <p className="eyebrow">Trace Console</p>
            <h2>运行台</h2>
          </div>
          <div className="artifact-actions">
            <span>{selectedRunId || "未运行"}</span>
            <button className="icon-button" type="button" onClick={refreshArtifacts} aria-label="刷新产物"><RefreshCw size={18} /></button>
            <button className="icon-button" type="button" onClick={() => setArtifactsCollapsed(true)} aria-label="收起产物面板"><ChevronRight size={18} /></button>
          </div>
        </div>
        <div className="panel-tabs">
          <button className="panel-tab active" type="button">产物</button>
        </div>
        <div className="artifact-list">
          {!artifacts.length ? (
            <div className="empty-state">完成一次任务后，这里会显示 Markdown 报告和性能文件。</div>
          ) : (
            artifacts.map((artifact) => <ArtifactCard key={artifact.path} runId={selectedRunId} artifact={artifact} />)
          )}
        </div>
      </aside>
    </main>
  );
}

function ObservabilityPage({ navigate }) {
  const params = new URLSearchParams(window.location.search);
  const initialRun = params.get("run") || "";
  const session = params.get("session") || "";
  const [health, setHealth] = useState(null);
  const [runs, setRuns] = useState([]);
  const [routeFilter, setRouteFilter] = useState("");
  const [selectedRunId, setSelectedRunId] = useState(initialRun);
  const [detail, setDetail] = useState(null);
  const [collapsed, setCollapsed] = useState({ agents: false, events: false, files: false });

  useEffect(() => {
    fetch("/api/health").then((response) => response.json()).then(setHealth).catch((error) => setHealth({ ok: false, error: error.message }));
    loadRuns();
  }, []);

  useEffect(() => {
    if (selectedRunId) {
      loadRunDetail(selectedRunId);
      const search = new URLSearchParams();
      search.set("run", selectedRunId);
      if (session) search.set("session", session);
      window.history.replaceState(null, "", `/observability?${search}`);
    }
  }, [selectedRunId]);

  async function loadRuns() {
    const data = await fetchJson("/api/runs?limit=100");
    setRuns(data.runs || []);
    if (!selectedRunId && data.runs?.[0]) setSelectedRunId(data.runs[0].runId);
  }

  async function loadRunDetail(runId) {
    setDetail(await fetchJson(`/api/runs/${encodeURIComponent(runId)}`));
  }

  const visibleRuns = routeFilter ? runs.filter((run) => run.route === routeFilter) : runs;
  const perf = detail?.performance || {};
  const obs = detail?.observability || perf.observability || {};
  const events = obs.events || [];
  const agents = Object.entries(obs.by_agent || {}).sort((a, b) => totalCalls(b[1]) - totalCalls(a[1]));
  const files = detail?.files || [];
  const artifacts = detail?.artifacts || [];

  function toggle(key) {
    setCollapsed((current) => ({ ...current, [key]: !current[key] }));
  }

  function backToChat() {
    navigate(session ? `/?session=${encodeURIComponent(session)}` : "/");
  }

  return (
    <main className="observe-shell">
      <aside className="observe-rail">
        <div className="rail-head">
          <Brand subtitle="Observability" />
          <button className="ghost-link" type="button" onClick={backToChat}><ArrowLeft size={17} />返回聊天</button>
        </div>
        <div className="rail-tools">
          <button type="button" onClick={loadRuns}><RefreshCw size={16} />刷新</button>
          <select value={routeFilter} onChange={(event) => setRouteFilter(event.target.value)}>
            <option value="">全部类型</option>
            <option value="general">general</option>
            <option value="code_health">code_health</option>
            <option value="prd">prd</option>
            <option value="error">error</option>
          </select>
        </div>
        <div className="run-feed">
          {!visibleRuns.length ? <div className="empty-state">没有符合条件的运行记录。</div> : visibleRuns.map((run) => (
            <button className={`run-card ${run.runId === selectedRunId ? "active" : ""}`} type="button" key={run.runId} onClick={() => setSelectedRunId(run.runId)}>
              <div className="run-topline">
                <span className={`route-pill ${run.route === "error" ? "error" : ""}`}>{run.route || "unknown"}</span>
                <span>{formatSeconds(run.durationSeconds)}</span>
              </div>
              <strong>{run.runId}</strong>
              <span>{formatNumber(run.modelCalls)} model · {formatNumber(run.toolCalls)} tool · {formatNumber(run.totalTokens)} tok</span>
            </button>
          ))}
        </div>
      </aside>

      <section className="observe-main">
        <header className="observe-topbar">
          <div>
            <p className="eyebrow">Agent Trace</p>
            <h2>{selectedRunId || "选择一次运行"}</h2>
          </div>
          <div className="health-strip">
            <span className={`dot ${health?.ok ? "" : "error"}`} />
            <span>{health ? `${health.engine || "agent"} · ${health.model || "model"}` : "连接中"}</span>
          </div>
        </header>

        <section className="metric-grid">
          <Metric label="模型调用" value={formatNumber(obs.model_calls || perf.model_calls)} />
          <Metric label="工具调用" value={formatNumber(obs.tool_calls || perf.tool_calls)} />
          <Metric label="总 Token" value={formatNumber(obs.total_tokens || perf.total_tokens)} />
          <Metric label="耗时" value={formatSeconds(perf.duration_seconds)} />
        </section>

        <section className="detail-grid">
          <Surface title="Agent 分布" meta={`${agents.length} agents`} collapsed={collapsed.agents} onToggle={() => toggle("agents")} className="agents-surface">
            {!agents.length ? <div className="empty-state">暂无 Agent 调用数据。</div> : agents.map(([name, counts]) => <AgentRow key={name} name={name} counts={counts} />)}
          </Surface>

          <Surface title="事件时间线" meta={`${events.length} events`} collapsed={collapsed.events} onToggle={() => toggle("events")} className="timeline-surface">
            {!events.length ? <div className="empty-state">暂无模型或工具事件。</div> : events.slice().reverse().map((event, index) => <EventRow key={index} event={event} />)}
          </Surface>

          <Surface title="产物与文件" meta={`${files.length} files`} collapsed={collapsed.files} onToggle={() => toggle("files")} className="files-surface">
            <GroupedFiles runId={selectedRunId} files={files} artifacts={artifacts} />
          </Surface>
        </section>
      </section>
    </main>
  );
}

function Brand({ subtitle }) {
  return (
    <div className="brand">
      <div className="brand-mark">A</div>
      <div>
        <h1>Aperio</h1>
        <p>{subtitle}</p>
      </div>
    </div>
  );
}

function ExampleButton({ icon, label, value, onUse }) {
  return (
    <button type="button" onClick={() => onUse(value)}>
      {icon}
      <span>{label}</span>
    </button>
  );
}

function Welcome({ onUse }) {
  return (
    <div className="welcome">
      <h2>有什么我能帮你的吗？</h2>
      <div className="suggestions">
        <ExampleButton icon={<FileText size={18} />} label="写 PRD 并评审" value="为智慧校园导航助手写 PRD 并评审" onUse={onUse} />
        <ExampleButton icon={<CodeXml size={18} />} label="检查代码健康" value="对当前项目做一次代码健康检查。" onUse={onUse} />
        <ExampleButton icon={<CloudSun size={18} />} label="查询天气建议" value="我在珠海，明天天气如何？要带伞吗？" onUse={onUse} />
      </div>
    </div>
  );
}

function Message({ role, text }) {
  return (
    <article className={`message ${role}`}>
      <div className="avatar">{role === "user" ? "ME" : "AI"}</div>
      <div className="bubble">{text}</div>
    </article>
  );
}

function ArtifactCard({ runId, artifact }) {
  return (
    <section className="artifact-card">
      <div className="artifact-title">
        <span>{artifact.path} · {artifact.size} bytes</span>
        <a href={`/api/runs/${encodeURIComponent(runId)}/artifact?path=${encodeURIComponent(artifact.path)}`}>下载</a>
      </div>
      <pre>{artifact.preview || "(empty)"}</pre>
    </section>
  );
}

function Metric({ label, value }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value || "0"}</strong>
    </div>
  );
}

function Surface({ title, meta, collapsed, onToggle, className, children }) {
  return (
    <div className={`surface ${className || ""} ${collapsed ? "collapsed" : ""}`}>
      <div className="surface-head">
        <h3>{title}</h3>
        <span>{meta}</span>
        <button className="collapse-button" type="button" onClick={onToggle} aria-label={collapsed ? "展开面板" : "收纳面板"}>
          {collapsed ? <Plus size={15} /> : <Minus size={15} />}
        </button>
      </div>
      {!collapsed && <div className="surface-body">{children}</div>}
    </div>
  );
}

function AgentRow({ name, counts }) {
  const model = Number(counts.model || 0);
  const tool = Number(counts.tool || 0);
  const total = Math.max(1, model + tool);
  return (
    <div className="agent-row">
      <strong>{name}</strong>
      <div className="agent-bars" style={{ "--model-share": `${Math.max(1, (model / total) * 100)}fr`, "--tool-share": `${Math.max(1, (tool / total) * 100)}fr` }}>
        <span />
        <span />
      </div>
      <div className="agent-counts"><span>{model} model</span><span>{tool} tool</span></div>
    </div>
  );
}

function EventRow({ event }) {
  const type = event.type || "event";
  const title = event.tool ? `${type}: ${event.tool}` : type;
  const tokens = event.tokens ? ` · ${event.tokens.input_tokens || 0}/${event.tokens.output_tokens || 0} tok` : "";
  return (
    <div className="event-row">
      <span className={`event-type ${type === "tool" ? "tool" : ""}`}>{type.slice(0, 4)}</span>
      <div className="event-body">
        <strong>{title}</strong>
        <span className="event-meta">{event.agent || "agent"}{tokens}{event.error ? ` · error: ${event.error}` : ""}</span>
      </div>
      <span className="event-time">{Number(event.elapsed_ms || 0).toFixed(1)} ms</span>
    </div>
  );
}

function GroupedFiles({ runId, files, artifacts }) {
  if (!files.length) return <div className="empty-state">这次运行没有落盘文件。</div>;
  const artifactPaths = new Set(artifacts.map((item) => item.path));
  const groups = groupFiles(files, artifactPaths);
  return (
    <div className="file-list">
      {groups.filter((group) => group.files.length).map((group) => (
        <details className="file-group" key={group.label} open>
          <summary><strong>{group.label}</strong><span>{group.files.length} files</span></summary>
          <div className="file-group-body">
            {group.files.map((file) => (
              <div className="file-row" key={file.path}>
                <div>
                  <strong>{file.path}</strong>
                  <span className="file-meta">{formatNumber(file.size)} bytes</span>
                </div>
                <a href={`/api/runs/${encodeURIComponent(runId)}/artifact?path=${encodeURIComponent(file.path)}`}>{artifactPaths.has(file.path) ? "下载" : "打开"}</a>
              </div>
            ))}
          </div>
        </details>
      ))}
    </div>
  );
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
    if (artifactPaths.has(path)) groups[0].files.push(file);
    else if (path.startsWith("outputs/")) groups[1].files.push(file);
    else if (path.startsWith("inputs/")) groups[2].files.push(file);
    else if (path.startsWith("skills/")) groups[3].files.push(file);
    else groups[4].files.push(file);
  }
  return groups;
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function readNdjson(body, onEvent) {
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
      if (line.trim()) await onEvent(JSON.parse(line));
    }
  }
  if (buffer.trim()) await onEvent(JSON.parse(buffer));
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function formatSeconds(value) {
  return `${Number(value || 0).toFixed(1)}s`;
}

function totalCalls(counts) {
  return Number(counts.model || 0) + Number(counts.tool || 0);
}

export default App;
