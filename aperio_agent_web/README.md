# Aperio Agent Web

独立的本地 Web 工作台，用来调用 `demo/aperio_integrated.py` 里的 Aperio agent。

## 启动

```powershell
conda activate llm-dev
python -m uvicorn aperio_agent_web.app:app --reload --host 127.0.0.1 --port 8088
```

打开：

```text
http://127.0.0.1:8088
```

如果提示 `WinError 10013` 或端口无法绑定，通常是 `8088` 已经被旧服务占用。可以先查并停止旧进程：

```powershell
Get-Process -Id (Get-NetTCPConnection -LocalPort 8088).OwningProcess
Stop-Process -Id (Get-NetTCPConnection -LocalPort 8088).OwningProcess -Force
```

也可以直接换一个端口：

```powershell
python -m uvicorn aperio_agent_web.app:app --reload --host 127.0.0.1 --port 8089
```

## 说明

- 网页不会放在 `demo` 目录里；`demo` 只作为已有 agent 的代码来源。
- `自动批准` 会通过 `APERIO_HITL_MODE=approve` 处理脚本中的 HITL 确认，适合本地沙箱运行。
- `自动拒绝` 会拒绝需要确认的工具调用，适合只想测试普通问答链路。
- 运行产物仍写入 `demo/workspace_integrated/<run_id>/`，页面右侧会预览常见 Markdown 产物。
