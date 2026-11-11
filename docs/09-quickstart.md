# 09 使用说明（0.1.0）

## 1. 安装与自测

面向 macOS/Linux；本次主机实测为 macOS，Linux 尚未实测。Python 3.11+；自测覆盖 Python 3.11.15 与 3.13.12，依赖版本由 `uv.lock` 固定。

```bash
uv sync --frozen
uv run ruff check src tests
uv run pytest -q
uv build
```

也可在自己的虚拟环境 `pip install .` 安装 `aat`。包未发布 PyPI，不要使用 `pip install android-agent-testing` 假设会安装本项目。

所有全局参数放在子命令前：`aat --project path/to/android-testing.yaml --json <command>`。当前无论是否带 `--json` 均输出 JSON，方便 Agent 调用。

## 2. Android 环境

合成样例使用 AGP 8.7.3、compile/targetSdk 35、minSdk 28、JUnit 4.13.2、AndroidX runner 1.6.2。本次验证 Gradle 8.10、JDK 21、Android API 35 ARM64、Maestro 2.12.1。

样例的 `gradle.sh` 调用 `GRADLE_BIN` 或 PATH 中的 `gradle`，避免复制其他项目的 wrapper。使用者安装 Gradle 8.10 并配置 Android SDK：

```bash
export ANDROID_HOME=/path/to/android-sdk
export GRADLE_BIN=/path/to/gradle-8.10/bin/gradle
export PATH="$ANDROID_HOME/platform-tools:/path/to/maestro/bin:$PATH"
export MAESTRO_CLI_NO_ANALYTICS=1
export MAESTRO_CLI_ANALYSIS_NOTIFICATION_DISABLED=true
adb devices -l
```

`/path/to/...` 是需替换的示例。真实项目优先在 argv 中调用它自己的 `./gradlew`，不强制使用样例 Gradle 版本。

Maestro 从其官方发布/安装说明获取；Android SDK 需安装匹配平台与 build-tools。工具本身不静默下载 SDK 或模拟器。设备序列号必须选择实际 `adb devices` 列出的设备，多个设备时不指定则阻塞。

## 3. 两个源码样例

```bash
uv run aat --project examples/counter-app/android-testing.yaml cases list
uv run aat --project examples/counter-app/android-testing.yaml cases search counter
uv run aat --project examples/counter-app/android-testing.yaml cases validate
uv run aat --project examples/counter-app/android-testing.yaml doctor

# 本地 JUnit + Lint
uv run aat --project examples/counter-app/android-testing.yaml run --tag fast

# 替换 DEVICE_SERIAL：设备测试会构建并安装合成 App/test APK
uv run aat --project examples/counter-app/android-testing.yaml run --suite full --device DEVICE_SERIAL

# 第二种结构：sdk library + host application，显式构建 DAG
uv run aat --project examples/worker-sdk/android-testing.yaml run --device DEVICE_SERIAL
```

两个样例均为本仓库自编 Java/View 工程，不代表 Compose 已验证；SDK 样例使用 project dependency。另在私有项目实测了 AAR 复制到宿主的构建 DAG，源码未复制到公共仓库。

## 4. APK-only

先构建 counter-app 的 APK，再复制到独立黑盒测试目录。输入 APK 不提交 Git。

```bash
mkdir -p examples/apk-only/input
cp examples/counter-app/app/build/outputs/apk/debug/app-debug.apk examples/apk-only/input/app.apk
uv run aat --project examples/apk-only/android-testing.yaml run --tag smoke --device DEVICE_SERIAL
```

不带 `--tag smoke` 会同时选择一个故意缺 instrumentation 能力的内部用例。预期 UI PASS、内部用例 BLOCKED、整体退出码 3；这是能力边界验证，不是预期全绿的套件。

## 5. 计划、历史与版本

```bash
uv run aat --project examples/counter-app/android-testing.yaml plan --case counter.increment
# 把输出中的 run_id 代入后续命令
uv run aat --project examples/counter-app/android-testing.yaml run --run-id RUN_ID
uv run aat --project examples/counter-app/android-testing.yaml report RUN_ID
```

计划保存到 `<project>/.aat/runs/<run-id>/plan.json`。执行后生成 `result.json`、`report.md`、`junit.xml`；证据保存在 tasks/cases/products 下。JSON 的 evidence_manifest 记录证据哈希和大小。失败/重试使用新 run ID，原记录不覆盖。

冻结时保存源码与测试资产快照和哈希；执行前后检查输入变化。事务保存、档位选择、Git diff、源码重放、能力探针、日志脱敏及证据保留的完整命令见 [11 生命周期与 Agent 接入](11-lifecycle-and-agent-integration.md)。

```bash
aat cases save update.yaml --expected-revision 1
aat cases history counter.increment
aat run --profile full --device DEVICE_SERIAL
aat replay RUN_ID --destination /new/replay-directory --execute --device DEVICE_SERIAL
aat prune
```

## 6. 使用边界

instrumentation 要求精确 Class#method；构建配置选择单 APK，split/AAB 需项目自行产出可安装单 APK。只执行 active 用例，选中的其他状态报告 BLOCKED。设备在同一主机排他使用，结束时 force-stop 本次安装的 App/test 包，不自动卸载或恢复系统级状态。

Maestro 字面量嵌套 flow/script 自动追踪，动态 helper 仍需 assets 声明。截图、源码与二进制不自动脱敏。源码重放要求对应外部工具和依赖可用。真实模拟器矩阵和未测平台见 [验证记录](10-validation-2026-11-11.md)。
