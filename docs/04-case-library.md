# 04 持久化用例库与版本模型

## 1. 存放位置

```text
<app-or-test-repository>/
  android-testing.yaml
  testing/
    cases/             # 一份 YAML 描述一个逻辑用例
    suites/            # smoke、模块、全量集合
    contracts/         # 业务预期及来源
    maestro/           # 原生 Maestro flow
    fixtures/          # 合成数据、响应、测试资源
  <module>/src/test/    # 原生 JVM 测试，保持标准 Gradle 位置
  <module>/src/androidTest/
  .aat/runs/<run-id>/   # 不可变执行清单、结果、证据
```

Git 管理所有用例和可执行资产，运行目录独立持久化。首期通过目录扫描实现索引，CLI 和 Agent 均能读取；不要求 SQLite 或向量库。

## 2. 用例元数据

```yaml
schema_version: 1
id: todo.item.create
revision: 1
status: draft
title: 创建待办后列表显示新条目
layer: ui
tags: [todo, smoke, regression]
requirements: [todo.create]
contract: testing/contracts/todo.md
impact:
  paths: ['mobile/src/main/**/Todo*']
  symbols: [TodoRepository.create]
implementation:
  engine: maestro
  path: testing/maestro/todo-create.yaml
assets: [testing/fixtures/new-item.json]
applicability:
  modes: [source, apk]
  variants: [demoDebug]
requires: [device, ui_accessibility]
priority: critical
timeout_seconds: 120
origin:
  kind: requirement
  reference: todo.create
```

JUnit/instrumentation 用例使用 `implementation.engine`、`task`、`selector` 和源文件路径绑定原生测试。static 用例绑定检查任务及 issue policy。selector 语法由引擎定义，不能用一个字符串规则假定所有 JUnit 版本一致。

UI 前置条件、步骤和断言保存在原生 flow 与引用契约里，目录只负责检索、来源与运行绑定。一个运行里同一 Gradle task 可以聚合多个 case，但适配器必须从报告映射每个预期 selector；缺失映射不可按 task 成功统一通过。

## 3. 生命周期

生成后立即保存为 draft，包括失败复现或暂不能执行的用例。满足契约有依据、schema/资产正确、runner 可发现、断言与清理已验证后成为 active。active 用例能稳定揭露产品缺陷而 FAIL，不要求当前产品无缺陷。

quarantined 表示已知不稳定，需要原因、问题引用和解除条件；不能因隔离而把必需覆盖变为通过。deprecated 保存淘汰原因、历史和替代 ID。上述状态与 PASS/FAIL 等运行结果独立。

## 4. 复用与去重

每次按需求、路径/符号、标签、层级、能力和适用条件检索，列出复用/更新/新增/排除原因。相同需求的 JVM 与 UI 测试是互补关系。不同参数可用原生参数化测试，不生成大量重复文件。

新 case ID 必须唯一；相同 ID 不同内容拒绝静默覆盖。更新前读取 revision 并比较，发生并发冲突返回错误，重读后合并。首期单写入者锁 + 临时文件校验 + 原子替换；跨多个文件用 journal 标记事务，崩溃恢复前不将部分更新视为有效版本。

工具不会自动 git commit 用户全部工作区。用户/Agent 按正常开发流程提交；即使尚未提交，文件和本次执行快照也必须已持久化。

## 5. 版本与依赖闭包

稳定 ID 不随标题或文件重命名改变。契约、断言、步骤、fixture、适用范围实质变化时 revision 增加。共享 helper/子 flow 改动影响所有引用 case：索引识别显式依赖，重新验证并生成相应版本；动态依赖无法解析时扩大测试范围并标记盲区。

`assets` 只登记测试 helper、fixture、子 flow 等测试依赖。被测业务源码由运行的 source fingerprint 跟踪，不能放入测试资产闭包而要求每次业务修改都升级 case revision。同一版本的测试应能对比缺陷源码与修复源码。

执行锁文件固定：case ID/revision、catalog 内容哈希、原生测试与 helper/fixture 哈希、源码 commit/快照、配置、引擎版本。revision 相同而内容变化必须被检测，禁止历史运行改指最新文件。

重放历史测试需要可恢复的资产快照。工具保存本次测试资产闭包；仅存哈希不能声称可重放。依赖缺失、旧工具不可用或旧 APK 不可获取时报告复现限制。

## 6. 套件与证据

suite 是 case ID/标签选择规则及 required 标记，不复制 case 内容。执行时解析为固定列表，保存全部选择/排除原因。每个 attempt 独立目录，保存结果与证据，重试不覆盖旧 attempt。

报告包含本次新增、复用、更新用例数；需求覆盖、实际执行、失败、阻塞、隔离和未覆盖项。覆盖率只在各自分母明确时计算，不能用“运行用例数/目录文件数”宣称业务覆盖率。

运行摘要追加保存。大文件支持独立保留期与固定保留，删除证据保留 expired 标记。备份范围由项目配置；开源公共仓库只包含合成用例。
