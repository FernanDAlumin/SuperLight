# SuperLight

**住在菜单栏里的模型用量小工具。**

[English](README.md) · [配置指南](docs/configuration.zh-CN.md) · [统计口径](docs/measurement.zh-CN.md)

SuperLight 记录本机 Codex 的 Token 用量，并估算对应的 API 成本。菜单栏标签显示今日金额，点击即可查看今日、本周、本月的汇总与模型单价。

- **安静常驻。** 后台运行，无需保留日志终端，可选登录时启动。
- **清楚直观。** 输入、缓存、输出、各模型消费及参考单价。
- **数据本地保存。** 使用 SQLite，不保存提示词、回答、凭据或工具输出。
- **轻量技术栈。** 原生 AppKit + 系统 WebKit，后端仅使用 Python 标准库，无 Electron、npm 或 Python 运行时依赖包。
- **中英双语。** 界面一键切换语言。

## macOS 快速开始

从源码构建需要 **macOS 13+**、**Python 3.9+** 和 **Xcode Command Line Tools**。

```bash
# 在项目目录中，仅首次构建需要执行
make app
open dist/SuperLight.app
```

之后可将 `SuperLight.app` 移入“应用程序”，像普通 App 一样打开。需要保留 Python 环境。App 自动启动后台接收器，日志按大小轮转保存。

1. 点击菜单栏 **ϟ** 标签，选择 **接入 Codex**。
2. 将复制的配置加入 `~/.codex/config.toml`，重启 Codex。
3. 完成一次本机任务，即可看到新增记录。

右键菜单栏标签可打开**设置**、数据文件夹，或选择**登录时启动**。HTTP 上游代理可选，例如 `http://127.0.0.1:7897`；留空则直连。用量采集独立于流量转发。

## CLI / 其他平台

```bash
python3 -m superlight serve --quiet
# 另一个终端
python3 -m superlight codex
```

打开 [127.0.0.1:12618](http://127.0.0.1:12618) 使用相同面板，也可以运行：

```bash
python3 -m superlight summary --today
python3 -m superlight export > usage.jsonl
```

## 数字代表什么

**金额为 USD 参考估算，不代表订阅实际扣费。** 缓存属于输入、推理属于输出，不重复累加。未知用量与价格明确标注。只统计实际收到的用量事件。

本周从周一开始，日、周、月均按接收器所在机器的时区划分。单价是可配置的价格快照，不是实时账单。

已验证使用 ChatGPT 登录的 Codex CLI。桌面端的**本机 Codex 任务**可沿用同一遥测配置；远程、云端任务需在实际执行主机接入。其他 Agent 需要各自的用量适配器。

## 文档

| 内容 | 中文 | English |
|---|---|---|
| 接入、数据路径、代理、排错 | [配置指南](docs/configuration.zh-CN.md) | [Configuration](docs/configuration.md) |
| Token、单价与覆盖范围 | [统计口径](docs/measurement.zh-CN.md) | [Measurement](docs/measurement.md) |

## 开发

```bash
make test
make app
```

测试覆盖日历边界与夏令时、估价、去重、持久化、本地浏览器访问、代理转发和 Codex 事件兼容。当前构建仅在本机签名，尚未提供经过公证的分发版本。
