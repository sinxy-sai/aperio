# Aperio Agent Backend

这是 Web UI 使用的独立后端 agent 包，不依赖 `demo/`，也不会启动 `demo/aperio_integrated.py`。

## 配置

从模板创建 `aperio_agent_backend/.env`：

```powershell
Copy-Item aperio_agent_backend/.env.example aperio_agent_backend/.env
```

然后填入：

```env
DEEPSEEK_API_KEY=你的 key
APERIO_MODEL=openai:deepseek-v4-flash
APERIO_BASE_URL=https://api.deepseek.com
```

也可以直接在当前 shell 中设置这些环境变量。

## 工作目录

运行产物会写入：

```text
aperio_agent_backend/workspace/<run_id>/
```

当前支持：

- 通用问答
- PRD 生成与评审矩阵
- 轻量代码健康报告

代码健康报告不使用 Docker，不运行测试或安全扫描工具；它基于项目结构抽样和 LLM 总结生成。
