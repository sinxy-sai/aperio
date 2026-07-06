---
name: web-search
description: Use when an Aperio agent needs public web evidence through the internet_search tool, including competitor research, market context, dependency ecosystem checks, official advisories, and rules for saving search evidence without polluting long-term memory.
---

## 角色定位

你负责规范使用 `internet_search` 工具。联网搜索只能补充公开资料，不能替代用户输入、本地代码、项目文件、工具扫描结果或已经落盘的草稿。

## 使用流程

1. 先判断是否真的需要联网：只有当任务需要公开资料、竞品、行业实践、官方公告、依赖生态或漏洞背景时才搜索。
2. 使用具体查询词，避免宽泛问题。例如用 `FastAPI security advisory pydantic CVE`，不要只搜 `security issue`。
3. 默认 `max_results=3`；只有结果不足或主题复杂时才提高到 5。
4. 对需要进入报告的搜索，传入 `save_path` 落盘到本次运行输出区。
5. 阅读返回的 `title`、`snippet`、`url`，只引用与当前任务直接相关的结果。
6. 联网失败或 `ok=false` 时，写成“联网证据未覆盖”，不要编造资料。

## 落盘规则

- 原始搜索结果属于本次运行证据，默认写入 `/outputs/<task>/raw/web_search/<topic>.json`。
- code-health 使用 `/outputs/code_health/raw/web_search/<topic>.json`。
- PRD review 使用 `/outputs/prd_review/raw/web_search/<topic>.json`。
- 文件名使用小写英文、数字和连字符，例如 `competitor-campus-navigation.json`。
- 最终报告和草稿中引用网页信息时，必须保留链接，并标注为“公开网络证据”。

## Memory 规则

- 不要把原始搜索结果写入 `/memories/`。
- 只有稳定、复用价值高、已经被人工确认的结论才适合写入 `/memories/`。
- 示例：长期产品定位、固定竞品清单、团队认可的安全基线可以进入 memory。
- 示例：某次搜索返回的标题、摘要、排名、临时网页片段不进入 memory。

## 证据边界

- 搜索摘要不是漏洞扫描结果；具体 CVE 结论必须来自 pip-audit、官方公告或明确网页证据。
- 搜索摘要不是用户需求；PRD 仍以用户输入和 PRD 原文为准。
- 搜索结果可能过时，涉及版本、漏洞、法规、价格、政策时必须在文字里说明来源和日期（如果网页提供）。
- 如果多个搜索结果冲突，报告冲突并降低置信度，不要强行合并成确定结论。
