# 配置指南

[English](configuration.md) · [首页](../README.zh-CN.md)

## 桌面 App

首次运行 `make app`，然后打开 `dist/SuperLight.app`。后端和网页资源会被复制到 App 内，构建后无需保留项目目录；仍需保留可用的 Python 环境。

点击菜单栏金额查看小面板，“打开完整面板”进入独立窗口。右键标签可设置、查看数据目录、重启接收器或选择登录时启动。关闭面板后继续常驻；退出 SuperLight 会停止它启动的接收器，独立启动的接收器则继续运行。

| 设置 | 默认值 / 示例 |
|---|---|
| 本机端口 | `12618`，仅监听回环地址 |
| HTTP 上游代理 | 留空则直连，例如 `http://127.0.0.1:7897` |
| 数据库 | `~/.local/share/superlight/usage.sqlite3` |
| 自定义单价 | 留空使用内置价格，或填写 JSON 文件绝对路径 |

需要沿用已有记录时，在设置中选择原数据库的绝对路径。例如开发时可能使用 `<repo>/.superlight/live-usage.sqlite3`。设置通过重启 App 管理的接收器生效；如果接收器由其他进程独立启动，应修改那个进程的启动参数。

菜单栏金额保留两位小数；`*` 表示有未定价记录，`—` 表示不可用，完整面板提供更高精度。日志写入数据库旁的 `superlight.log`，每份最多 1 MiB，保留三个备份。

## 接入 Codex

将以下内容合并到**用户级** `~/.codex/config.toml`。如果已有 `[otel]`，修改原段，不要重复添加：

```toml
[otel]
log_user_prompt = false
exporter = { otlp-http = { endpoint = "http://127.0.0.1:12618/v1/logs", protocol = "json" } }
```

重启 Codex，保持 SuperLight 运行，完成一次本机任务。这项配置用于导出用量事件，沿用现有 ChatGPT 登录和模型网络连接。接收器不解密 HTTPS。[官方配置文档](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry)。

桌面端的本机 Codex 后端也可使用上述配置。远程任务读取远程主机的配置，`127.0.0.1` 指向那台主机，需要在那里部署接收器或建立合适的回环隧道。云端任务用量不会自动接入。

## 命令行

```bash
python3 -m superlight serve --quiet --log-file .superlight/events.log
python3 -m superlight codex -- -C /path/to/project
python3 -m superlight summary --today
python3 -m superlight export --today > usage.jsonl
```

通过 Clash 转发时，为 `serve` 增加 `--upstream http://127.0.0.1:7897`。使用 HTTP 或 mixed 端口；当前不支持 SOCKS-only 或带认证的上游。仅启动服务不会自动配置客户端。

`serve`、`summary`、`export` 支持 `--db` 和 `--prices`，需要使用相同文件。`serve` 和 `codex` 支持 `--port`。CLI 数据目录优先采用 `XDG_DATA_HOME`，默认 `~/.local/share/superlight`。

## 检查连接

```bash
curl --noproxy '*' http://127.0.0.1:12618/health
```

| 现象 | 检查方法 |
|---|---|
| 菜单栏没有标签 | 重新打开 App；菜单栏拥挤时先腾出空间。 |
| 端口被占用 | 退出旧接收器，或同时修改 SuperLight 与 Codex 的端口。 |
| 面板没有记录 | 启用遥测后完成一次任务，等待几秒，并核对数据库路径。 |
| 收到遥测但没有用量 | 核对 Codex 版本是否导出带 Token 计数的完成事件。 |
| 金额显示未知 | 在价格表中加入精确模型 ID，或检查缓存明细是否缺失。 |
| 菜单栏显示未连接 | 在设置中检查 Python、端口、数据库及价格文件路径。 |

`last_telemetry_ns` 表示本次启动最近收到遥测批次的时间，`recorded_responses` 表示持久化记录数。界面约每 10 秒刷新。面板 API 只接受本机同源浏览器读取，第三方网页不能提交遥测。

## 构建与验证

`make app` 使用 Swift、AppKit、WebKit、ServiceManagement 和 Python。若没有 `xcrun swiftc`，先安装 Xcode Command Line Tools。构建面向当前 Mac 架构，要求 macOS 13+。本地签名不等于 Apple 公证。

`make test` 使用临时数据库与本机模拟服务器运行标准库测试，不调用真实模型。
