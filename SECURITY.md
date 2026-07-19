# Security Policy

## Supported Scope

Aperio Agent is designed as a single-user, local-first agent workspace. The current project scope covers local CLI usage, local Web UI usage, packaged agent skills, optional MCP tools, optional Docker-based code scanning, and optional Feishu/Lark gateway integration.

Production, multi-tenant, or public internet deployments require additional controls that are not enabled by default, including authentication, authorization, audit logging, network isolation, rate limiting, secret rotation, and operational monitoring.

## Reporting a Vulnerability

If you discover a security issue, please do not publish exploit details publicly before maintainers have had time to investigate.

Use one of the following channels:

- Open a private security advisory on GitHub, if available for the repository.
- If private advisories are unavailable, open an issue with minimal reproduction details and avoid including secrets, tokens, or working exploit payloads.

Please include:

- Affected version or commit.
- A clear description of the issue.
- Steps to reproduce in a local test environment.
- Expected impact.
- Relevant logs or stack traces with secrets removed.

## Secret Handling

Do not commit secrets to the repository. This includes:

- `.env`
- `~/.aperio/.env`
- `~/.aperio/config.json`
- API keys for model providers
- Feishu/Lark App Secret, Encrypt Key, and Verification Token
- Amap API keys

> [!WARNING]
> If a secret is accidentally committed or shared, rotate it immediately. Removing it from a later commit is not enough because it may still exist in Git history.

## Local Web UI

The Web UI is intended to run on loopback addresses such as `127.0.0.1`.

Recommended default:

```powershell
aperio serve --host 127.0.0.1 --port 8088
```

Avoid binding to `0.0.0.0` unless you have added network-level access controls and understand who can reach the service.

## MCP Tools

MCP tools are disabled by default. When enabled, agents may make external requests through configured tools such as web search or map services.

```env
APERIO_ENABLE_MCP=0
```

Recommendations:

- Keep MCP disabled unless a task requires external evidence or map data.
- Treat MCP results as untrusted input.
- Do not send private source code, credentials, customer data, or sensitive documents to external MCP services unless explicitly approved.
- Record sources and timestamps when using MCP for time-sensitive information.

## Feishu/Lark Gateway

Before connecting the gateway to a real workspace or group chat:

- Configure `channels.feishu.allowFrom` with trusted sender IDs.
- Use `groupPolicy: "mention"` for group chats.
- Keep `maxMediaBytes` bounded.
- Store App ID, App Secret, Encrypt Key, and Verification Token outside the repository.
- Review downloaded attachments before treating them as trusted project inputs.

> [!WARNING]
> Do not expose the Feishu/Lark gateway to untrusted users without caller restrictions. Otherwise, unauthorized users may trigger model calls, file processing, or external tool usage.

## Docker Code Scanning

Docker scanning is used for code-health analysis and dependency/tool isolation. It is not a complete general-purpose sandbox for untrusted code execution.

Recommendations:

- Prefer read-only project mounts.
- Keep `APERIO_INSTALL_PROJECT_DEPS=0` unless dependency installation is necessary.
- Review scanner output before acting on findings.
- Use host scanning only for trusted local projects.

## Safe Execution

The safe execution wrapper is enabled by default:

```env
APERIO_SAFE_EXECUTION_ENABLED=1
```

It is intentionally conservative and allows only a small set of read-only commands. Do not broaden the allowed command set without reviewing shell injection, path traversal, timeout, and workspace boundary risks.

## Dependencies

Keep Python and Node dependencies updated through normal package manager workflows. Review changes before upgrading agent, MCP, browser automation, or integration dependencies because those components may affect filesystem, network, or external service behavior.

Suggested checks:

```powershell
python -m compileall aperio_agent_backend aperio_agent_web
npm run check:frontend
```

## Security Assumptions

The project assumes:

- A trusted local user.
- A trusted local machine.
- Local files are intentionally selected by the user.
- External model providers and MCP services are configured knowingly.
- Generated agent output is reviewed before being used for production changes.

These assumptions should be revisited before any team, server, or public deployment.
