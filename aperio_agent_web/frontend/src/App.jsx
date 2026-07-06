import {
  Activity,
  ArrowLeft,
  ChevronRight,
  CloudSun,
  CodeXml,
  File as FileIcon,
  FileText,
  Folder,
  FolderOpen,
  Image,
  Menu,
  Minus,
  PanelRight,
  Paperclip,
  Plus,
  RefreshCw,
  Settings,
  Square,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

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
  const [attachments, setAttachments] = useState([]);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef(null);
  const folderInputRef = useRef(null);
  const streamRef = useRef(null);

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
    const targetId = activeSession?.id;
    if (!targetId) return;
    updateSessions((current) =>
      current.map((session) => {
        if (session.id !== targetId) return session;
        const nextMessages = [...(session.messages || []), { role, text }];
        const title =
          session.title && session.title !== "新对话"
            ? session.title
            : text.slice(0, 28) || "新对话";
        return { ...session, messages: nextMessages, title, updatedAt: Date.now() };
      }),
    );
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

  async function deleteSession(id) {
    const target = sessions.find((item) => item.id === id);
    if (!target) return;
    const confirmed = window.confirm("这次删除无法恢复，会同时删除该会话关联的运行记录和落盘文件。确定删除吗？");
    if (!confirmed) return;
    if (id === activeSession?.id && running) {
      stopRunningRun();
    }
    if (target.runId) {
      try {
        const response = await fetch(`/api/runs/${encodeURIComponent(target.runId)}`, { method: "DELETE" });
        if (!response.ok && response.status !== 404) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.detail || `HTTP ${response.status}`);
        }
      } catch (error) {
        window.alert(`删除落盘运行失败：${error.message}`);
        return;
      }
    }
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

  function addAttachments(nextItems) {
    if (!nextItems.length) return;
    setAttachments((current) => {
      const seen = new Set(current.map((item) => attachmentKey(item)));
      const merged = [...current];
      for (const item of nextItems) {
        const key = attachmentKey(item);
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(item);
        }
      }
      return merged.slice(0, 80);
    });
  }

  function removeAttachment(id) {
    setAttachments((current) => current.filter((item) => item.id !== id));
  }

  function handlePickedFiles(fileList) {
    addAttachments(Array.from(fileList || []).map((file) => makeAttachment(file, file.webkitRelativePath || file.name)));
  }

  async function handleDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    setDragActive(false);
    const dropped = await collectDroppedAttachments(event.dataTransfer);
    addAttachments(dropped);
  }

  function stopRunningRun() {
    const stream = streamRef.current;
    if (stream) {
      stream.stopped = true;
      if (stream.runId) {
        void fetch(`/api/runs/${encodeURIComponent(stream.runId)}/cancel`, { method: "POST" }).catch(() => {});
      }
      stream.controller.abort();
      streamRef.current = null;
    }
    setRunning(false);
    setDraftAssistant("");
    setRunMeta("已停止，可重新提问");
    addMessage("assistant", "已停止本次运行。");
  }

  async function submitTask(event) {
    event.preventDefault();
    const pendingAttachments = attachments;
    const message = input.trim() || (pendingAttachments.length ? "请分析我上传的文件。" : "");
    if (!message || running) return;

    addMessage("user", formatUserMessage(message, pendingAttachments));
    setInput("");
    setAttachments([]);
    setRunning(true);
    setDraftAssistant("正在连接后端...");
    setRunMeta("agent 正在处理");
    const stream = { controller: new AbortController(), runId: "", stopped: false };
    streamRef.current = stream;

    try {
      const response = await fetch("/api/chat/stream", requestBody(message, approvalMode, timeoutSeconds, pendingAttachments, stream.controller.signal));
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${response.status}`);
      }
      if (!response.body) throw new Error("当前浏览器不支持流式响应");

      await readNdjson(response.body, async (eventData) => {
        const eventRunId = eventData.run_id || eventData.data?.run_id || "";
        if (eventRunId) {
          stream.runId = eventRunId;
          setSelectedRunId(eventRunId);
          patchActiveSession({ runId: eventRunId });
        }
        if (eventData.type === "status") {
          setDraftAssistant(eventData.message || "agent 正在运行");
          setRunMeta(`运行中 · ${Number(eventData.elapsed || 0).toFixed(1)}s`);
          return;
        }
        if (eventData.type === "cancelled") {
          stream.stopped = true;
          setDraftAssistant("");
          setRunMeta("已停止，可重新提问");
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
      if (stream.stopped || error.name === "AbortError") {
        setDraftAssistant("");
        setRunMeta("已停止，可重新提问");
        return;
      }
      setDraftAssistant("");
      addMessage("assistant", `请求失败：${error.message}`);
      setRunMeta("运行失败");
    } finally {
      if (streamRef.current === stream) {
        streamRef.current = null;
        setRunning(false);
      }
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
                <button className="recent-delete" type="button" onClick={() => void deleteSession(session.id)} title="删除会话">
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

        <form
          className={`composer ${dragActive ? "drag-active" : ""}`}
          onSubmit={submitTask}
          onDragEnter={(event) => {
            event.preventDefault();
            setDragActive(true);
          }}
          onDragOver={(event) => {
            event.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) setDragActive(false);
          }}
          onDrop={handleDrop}
        >
          <div className="composer-status">
            <span className={`status-dot ${health?.ok ? "ok" : health?.error ? "error" : ""}`} />
            <strong>{health?.ok ? "已连接" : health?.error ? "服务异常" : "连接中"}</strong>
            <span>{health ? `${health.engine || "agent"} · ${health.model || "model"}` : "检查本地服务"}</span>
          </div>
          <input ref={fileInputRef} className="hidden-file-input" type="file" multiple onChange={(event) => handlePickedFiles(event.target.files)} />
          <input ref={folderInputRef} className="hidden-file-input" type="file" multiple webkitdirectory="" directory="" onChange={(event) => handlePickedFiles(event.target.files)} />
          {attachments.length > 0 && (
            <div className="attachment-tray">
              <div className="attachment-tray-head">
                <span><Paperclip size={15} />{attachments.length} 个附件 · {formatBytes(attachments.reduce((sum, item) => sum + item.file.size, 0))}</span>
                <button type="button" onClick={() => setAttachments([])}>清空</button>
              </div>
              <div className="attachment-list">
                {attachments.map((item) => (
                  <div className="attachment-chip" key={item.id} title={item.path}>
                    {attachmentIcon(item)}
                    <span>{formatFileLabel(item.path).name}</span>
                    <small>{formatBytes(item.file.size)}</small>
                    <button type="button" onClick={() => removeAttachment(item.id)} aria-label="移除附件"><X size={14} /></button>
                  </div>
                ))}
              </div>
            </div>
          )}
          <textarea value={input} onChange={(event) => setInput(event.target.value)} rows={4} placeholder="例如：为智慧校园导航助手写 PRD 并评审" />
          <div className="composer-actions">
            <div className="composer-tools">
              <button type="button" onClick={() => fileInputRef.current?.click()} title="添加文件"><Paperclip size={17} />文件</button>
              <button type="button" onClick={() => folderInputRef.current?.click()} title="添加文件夹"><FolderOpen size={17} />文件夹</button>
              <span>{runMeta}</span>
            </div>
            <button id="sendBtn" className={running ? "stop" : ""} type={running ? "button" : "submit"} onClick={running ? stopRunningRun : undefined}>
              {running ? <><Square size={15} />停止</> : "运行"}
            </button>
          </div>
          {dragActive && (
            <div className="drop-overlay">
              <Upload size={24} />
              <span>松开后添加到本次对话</span>
            </div>
          )}
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
  const [summary, setSummary] = useState(null);
  const [windowDays, setWindowDays] = useState(7);
  const [routeFilter, setRouteFilter] = useState("");
  const [selectedRunId, setSelectedRunId] = useState(initialRun);
  const [detail, setDetail] = useState(null);
  const [collapsed, setCollapsed] = useState({ agents: false, events: false, files: false });

  useEffect(() => {
    fetch("/api/health").then((response) => response.json()).then(setHealth).catch((error) => setHealth({ ok: false, error: error.message }));
    refreshObservability();
  }, []);

  useEffect(() => {
    loadSummary();
  }, [windowDays]);

  useEffect(() => {
    if (selectedRunId) {
      loadRunDetail(selectedRunId);
      const search = new URLSearchParams();
      search.set("run", selectedRunId);
      if (session) search.set("session", session);
      window.history.replaceState(null, "", `/observability?${search}`);
    }
  }, [selectedRunId]);

  async function refreshObservability() {
    await Promise.all([loadRuns(), loadSummary()]);
  }

  async function loadRuns() {
    const data = await fetchJson("/api/runs?limit=100");
    setRuns(data.runs || []);
    if (!selectedRunId && data.runs?.[0]) setSelectedRunId(data.runs[0].runId);
  }

  async function loadSummary() {
    setSummary(await fetchJson(`/api/observability/summary?days=${windowDays}`));
  }

  async function loadRunDetail(runId) {
    setDetail(await fetchJson(`/api/runs/${encodeURIComponent(runId)}`));
  }

  const visibleRuns = routeFilter ? runs.filter((run) => run.route === routeFilter) : runs;
  const routeSummary = Object.entries(summary?.routeCounts || {}).sort((a, b) => b[1] - a[1]);
  const systemWindow = formatWindowLabel(summary?.windowDays ?? windowDays);
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
          <button type="button" onClick={refreshObservability}><RefreshCw size={16} />刷新</button>
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
            <p className="eyebrow">Observability</p>
            <h2>系统运行观测</h2>
          </div>
          <div className="observe-actions">
            <select className="window-select" value={windowDays} onChange={(event) => setWindowDays(Number(event.target.value))}>
              <option value={7}>近 7 天</option>
              <option value={30}>近 30 天</option>
              <option value={90}>近 90 天</option>
              <option value={0}>全部</option>
            </select>
            <div className="health-strip">
              <span className={`dot ${health?.ok ? "" : "error"}`} />
              <span>{health ? `${health.engine || "agent"} · ${health.model || "model"}` : "连接中"}</span>
            </div>
          </div>
        </header>

        <div className="observe-content">
          <section className="metric-grid">
            <Metric label={`Trace Count · ${systemWindow}`} value={formatNumber(summary?.totalRuns)} meta={`${formatNumber(summary?.successfulRuns)} success`} />
            <Metric label={`Error Rate · ${systemWindow}`} value={formatPercent(summary?.errorRate)} meta={`${formatNumber(summary?.failedRuns)} failed`} tone={Number(summary?.errorRate || 0) > 0 ? "error" : "ok"} />
            <Metric label={`P50 Latency · ${systemWindow}`} value={formatLatency(summary?.p50LatencySeconds)} meta={`avg ${formatLatency(summary?.avgLatencySeconds)}`} />
            <Metric label={`P99 Latency · ${systemWindow}`} value={formatLatency(summary?.p99LatencySeconds)} meta="tail latency" />
            <Metric label={`Total Tokens · ${systemWindow}`} value={formatNumber(summary?.totalTokens)} meta={`${formatNumber(summary?.totalModelCalls)} model calls`} />
            <Metric label="Most Recent Run" value={summary?.latestRunId || "无"} meta={summary?.latestRunRoute || "not available"} />
          </section>

          <section className="observe-overview">
            <div className="route-panel">
              <div className="section-head">
                <div>
                  <p className="eyebrow">Traffic</p>
                  <h3>路由分布</h3>
                </div>
                <span>{systemWindow}</span>
              </div>
              <div className="route-list">
                {!routeSummary.length ? <div className="empty-state">暂无系统运行数据。</div> : routeSummary.map(([route, count]) => (
                  <div className="route-row" key={route}>
                    <div>
                      <strong>{route}</strong>
                      <span>{formatNumber(count)} runs</span>
                    </div>
                    <div className="route-meter">
                      <span style={{ width: `${Math.max(4, (count / Math.max(1, summary?.totalRuns || 0)) * 100)}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="runs-table-panel">
              <div className="section-head">
                <div>
                  <p className="eyebrow">Tracing</p>
                  <h3>运行列表</h3>
                </div>
                <span>{visibleRuns.length} rows</span>
              </div>
              <div className="runs-table">
                <div className="runs-table-head">
                  <span>Name</span>
                  <span>Route</span>
                  <span>Status</span>
                  <span>Latency</span>
                  <span>Tokens</span>
                </div>
                {!visibleRuns.length ? <div className="empty-state">没有符合条件的运行记录。</div> : visibleRuns.slice(0, 12).map((run) => (
                  <button className={`runs-table-row ${run.runId === selectedRunId ? "active" : ""}`} type="button" key={run.runId} onClick={() => setSelectedRunId(run.runId)}>
                    <span>{run.runId}</span>
                    <span>{run.route || "unknown"}</span>
                    <span className={run.ok === false || run.route === "error" ? "status-error" : "status-ok"}>{run.ok === false || run.route === "error" ? "error" : "ok"}</span>
                    <span>{formatLatency(run.durationSeconds)}</span>
                    <span>{formatNumber(run.totalTokens)}</span>
                  </button>
                ))}
              </div>
            </div>
          </section>

          <section className="detail-grid">
            <div className="detail-stack">
              <Surface title="Agent 分布" meta={`${agents.length} agents`} collapsed={collapsed.agents} onToggle={() => toggle("agents")} className="agents-surface">
                {!agents.length ? <div className="empty-state">暂无 Agent 调用数据。</div> : agents.map(([name, counts]) => <AgentRow key={name} name={name} counts={counts} />)}
              </Surface>

              <Surface title="产物与文件" meta={`${files.length} files`} collapsed={collapsed.files} onToggle={() => toggle("files")} className="files-surface">
                <GroupedFiles runId={selectedRunId} files={files} artifacts={artifacts} />
              </Surface>
            </div>

            <Surface title="事件时间线" meta={`${events.length} events`} collapsed={collapsed.events} onToggle={() => toggle("events")} className="timeline-surface">
              {!events.length ? <div className="empty-state">暂无模型或工具事件。</div> : events.slice().reverse().map((event, index) => <EventRow key={index} event={event} />)}
            </Surface>
          </section>
        </div>
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

function Metric({ label, value, meta, tone }) {
  return (
    <div className={`metric ${tone ? `metric-${tone}` : ""}`}>
      <span>{label}</span>
      <strong>{value || "0"}</strong>
      {meta && <small>{meta}</small>}
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
        <details className={`file-group file-group-${group.kind}`} key={group.kind} open={group.defaultOpen}>
          <summary>
            <strong>{group.label}</strong>
            <span>{group.files.length} files</span>
          </summary>
          <div className="file-group-body">
            {group.files.map((file) => (
              <div className="file-row" key={file.path} title={file.path}>
                <div>
                  <strong>{formatFileLabel(file.path).name}</strong>
                  <span className="file-meta">
                    {formatFileLabel(file.path).parent ? `${formatFileLabel(file.path).parent} · ` : ""}
                    {formatNumber(file.size)} bytes
                  </span>
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
    { kind: "outputs", label: "输出产物", files: [], defaultOpen: true },
    { kind: "inputs", label: "输入文件", files: [], defaultOpen: true },
    { kind: "skills", label: "Skills", files: [], defaultOpen: false },
    { kind: "other", label: "其他文件", files: [], defaultOpen: false },
  ];
  const groupByKind = Object.fromEntries(groups.map((group) => [group.kind, group]));
  for (const file of files) {
    const path = file.path || "";
    if (path.startsWith("inputs/")) groupByKind.inputs.files.push(file);
    else if (path.startsWith("skills/")) groupByKind.skills.files.push(file);
    else if (artifactPaths.has(path) || path.startsWith("outputs/")) groupByKind.outputs.files.push(file);
    else groupByKind.other.files.push(file);
  }
  return groups;
}

function makeAttachment(file, path) {
  const normalizedPath = String(path || file.name || "file").replaceAll("\\", "/").replace(/^\/+/, "");
  return {
    id: `${Date.now()}_${Math.random().toString(16).slice(2)}_${normalizedPath}`,
    file,
    path: normalizedPath,
  };
}

function attachmentKey(item) {
  return `${item.path}:${item.file.size}:${item.file.lastModified}`;
}

function attachmentIcon(item) {
  const type = item.file.type || "";
  const name = item.path.toLowerCase();
  if (type.startsWith("image/")) return <Image size={16} />;
  if (name.includes("/")) return <Folder size={16} />;
  if (name.endsWith(".md") || name.endsWith(".txt") || name.endsWith(".pdf") || name.endsWith(".doc") || name.endsWith(".docx")) {
    return <FileText size={16} />;
  }
  return <FileIcon size={16} />;
}

function requestBody(message, approvalMode, timeoutSeconds, attachments, signal) {
  if (!attachments.length) {
    return {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal,
      body: JSON.stringify({
        message,
        approval_mode: approvalMode,
        timeout_seconds: Number(timeoutSeconds || 900),
      }),
    };
  }

  const form = new FormData();
  form.append("message", message);
  form.append("approval_mode", approvalMode);
  form.append("timeout_seconds", String(Number(timeoutSeconds || 900)));
  for (const item of attachments) {
    form.append("files", item.file, item.path);
    form.append("paths", item.path);
  }
  return { method: "POST", body: form, signal };
}

function formatUserMessage(message, attachments) {
  if (!attachments.length) return message;
  const fileLines = attachments
    .slice(0, 12)
    .map((item) => `- ${item.path} (${formatBytes(item.file.size)})`);
  const suffix = attachments.length > 12 ? `\n- 另有 ${attachments.length - 12} 个附件` : "";
  return `${message}\n\n附件：\n${fileLines.join("\n")}${suffix}`;
}

async function collectDroppedAttachments(dataTransfer) {
  const items = Array.from(dataTransfer?.items || []);
  if (items.length && items.some((item) => typeof item.webkitGetAsEntry === "function")) {
    const nested = await Promise.all(
      items
        .map((item) => item.webkitGetAsEntry?.())
        .filter(Boolean)
        .map((entry) => readDroppedEntry(entry, "")),
    );
    return nested.flat();
  }
  return Array.from(dataTransfer?.files || []).map((file) => makeAttachment(file, file.webkitRelativePath || file.name));
}

function readDroppedEntry(entry, prefix) {
  if (entry.isFile) {
    return new Promise((resolve) => {
      entry.file((file) => resolve([makeAttachment(file, `${prefix}${file.name}`)]), () => resolve([]));
    });
  }
  if (!entry.isDirectory) return Promise.resolve([]);

  const reader = entry.createReader();
  const directoryPrefix = `${prefix}${entry.name}/`;
  const batches = [];
  return new Promise((resolve) => {
    const readBatch = () => {
      reader.readEntries(async (entries) => {
        if (!entries.length) {
          resolve(Promise.all(batches).then((groups) => groups.flat()));
          return;
        }
        batches.push(Promise.all(entries.map((child) => readDroppedEntry(child, directoryPrefix))).then((groups) => groups.flat()));
        readBatch();
      }, () => resolve([]));
    };
    readBatch();
  });
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

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatSeconds(value) {
  return `${Number(value || 0).toFixed(1)}s`;
}

function formatLatency(value) {
  const seconds = Number(value || 0);
  if (!seconds) return "0s";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  return `${seconds.toFixed(1)}s`;
}

function formatPercent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function formatWindowLabel(days) {
  return Number(days || 0) ? `${days}d` : "all";
}

function formatFileLabel(path) {
  const normalized = String(path || "").replaceAll("\\", "/");
  const parts = normalized.split("/").filter(Boolean);
  return {
    name: parts.at(-1) || normalized || "文件",
    parent: parts.length > 1 ? parts.slice(0, -1).join("/") : "",
  };
}

function totalCalls(counts) {
  return Number(counts.model || 0) + Number(counts.tool || 0);
}

export default App;
