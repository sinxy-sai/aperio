# 代码坏味道检查参考

使用本参考时，先读取 `/outputs/code_health/raw/tool_results.json`，再按需读取源码。不要把清单机械写满；只报告有证据的坏味道，并标注证据来源。

## 可用证据

- `tools.radon.cc`: 圈复杂度，优先用于 Long Method、Switch Statements、复杂条件分支。
- `tools.radon.mi` 和 `tools.radon.raw`: Maintainability Index、LOC/SLOC，优先用于 Large Class、超大文件、可维护性退化。
- `tools.ruff` 和 `tools.mypy`: 静态质量、类型设计和未使用代码线索。
- `tools.deptry`: 依赖边界、未声明/未使用依赖线索。
- `read_file`/`grep`/`glob`: 复核具体类、函数、调用链、重复片段。

## 指标型异味

| 异味 | 主要证据 | 判断方式 |
|------|----------|----------|
| Duplicated Code | 相似函数、重复分支、重复配置块 | 没有专用重复检测工具时只报告明显重复；需要引用至少两个位置 |
| Long Method | `radon.cc`、函数行数、嵌套层级 | 优先报告复杂度 B 以上或明显过长且承担多职责的函数 |
| Large Class | 类 LOC、方法数量、字段数量、职责数量 | 结合 `radon.raw` 和代码阅读，不仅看行数 |
| Long Parameter List | 函数签名 | 参数过多或同类参数反复出现时报告 |
| Switch Statements | `if/elif` 链、`match`、按类型/状态分支 | 仅当分支频繁变化或应由多态/映射表替代时报告 |
| Comments | 注释解释复杂逻辑、过期注释、注释替代命名 | 区分有价值注释和掩盖坏设计的注释 |

## 设计型异味

| 异味 | 判断线索 | 报告要求 |
|------|----------|----------|
| Divergent Change | 一个模块因多种原因被修改 | 需要从职责混杂、配置集中或历史趋势推断；无历史数据时降低置信度 |
| Shotgun Surgery | 一个变化需要改很多模块 | 需要调用链/模块分散证据；无完整依赖图时不要断言 |
| Feature Envy | 函数频繁访问其他对象/模块的数据 | 引用具体函数和被访问对象 |
| Data Clumps | 多个参数/字段总是成组出现 | 引用重复出现的位置，建议 Value Object/配置对象 |
| Primitive Obsession | 用字符串/数字/字典表达领域概念 | 说明应抽象为枚举、值对象或类型别名的理由 |
| Parallel Inheritance Hierarchies | 两套继承树同步扩展 | 只有看到继承结构时才报告 |
| Lazy Class | 类/模块没有足够职责 | 区分未来扩展点和真实冗余 |
| Speculative Generality | 未被使用的抽象、配置、扩展点 | 结合 ruff/grep 的未使用证据，避免把合理扩展点误报 |
| Temporary Field | 对象字段只在部分流程临时有效 | 需要类状态生命周期证据 |
| Message Chains | 长调用链暴露内部结构 | 引用具体调用链，建议封装查询/门面方法 |
| Middle Man | 类/函数只做转发 | 确认无策略、校验、缓存或边界隔离价值后再报告 |
| Inappropriate Intimacy | 模块互相访问内部细节 | 引用内部属性、私有方法或跨层访问证据 |
| Alternative Classes with Different Interfaces | 功能相似但接口不同 | 引用相似职责和调用差异 |
| Incomplete Library Class | 第三方/内部库能力不足导致绕行 | 区分库限制和项目封装不足 |
| Data Class | 只有字段、缺少行为的数据结构 | 仅当行为散落在外部函数时报告 |
| Refused Bequest | 子类继承但拒绝父类契约 | 需要继承和重写/异常证据 |

## 输出规则

把代码坏味道结果写成小节或表格：

| 异味 | 位置 | 证据类型 | 证据摘要 | 重构建议 |
|------|------|----------|----------|----------|

没有证据的异味不要列为问题；可以在“未覆盖/待增强”中说明需要 clone detector、依赖图或历史提交数据才能更可靠判断。
