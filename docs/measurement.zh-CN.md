# 统计与估价口径

[English](measurement.md) · [首页](../README.zh-CN.md)

## 统计已报告的用量

SuperLight 读取 Codex 通过 OTLP JSON 导出的完成事件。输入、输出来自 Agent 报告的计数，不对正文重新分词，也不根据加密网络流量推测 Token。

- 总 Token = 输入 + 输出。
- 缓存输入属于输入，推理输出属于输出，不重复累加。
- 缺失值为 `null`，与明确报告的零区分。
- 启动阶段或后台完成事件可能被纳入，因此记录总量可能高于 CLI 某一轮显示的用量。

有响应 ID 时，按服务、会话和响应 ID 去重；没有时，按相同时间戳与标准化用量去除重复导出。重新生成时间戳的重复事件不一定能被识别。

已使用 ChatGPT 登录验证 Codex CLI 0.144.1。该版本有时将标准 OTLP 时间戳置零，SuperLight 同时兼容 `event.timestamp` 属性中的 RFC3339 时间，保留纳秒精度。

## 日历时段

今日从本机零点开始，本周从周一开始，本月从一号开始，按接收器所在机器的时区划分，包括夏令时变化。面板排除时间位于未来的记录。这些是日历时段，不是滚动的最近 24 小时、7 天或 30 天。

仅统计实际收到的完成事件。未启用导出、传输丢失、请求中断且没有报告用量、启动采集前的活动都不会被补算。缺失用量或单价时，界面明确标注估算不完整。

## 估算 API 等价成本

```text
USD = ((输入 - 缓存输入) × 输入单价
     + 缓存输入 × 缓存单价
     + 输出 × 输出单价) / 1,000,000
```

单价单位为每百万 Token 的美元价格。逐个响应选取适用单价（含配置的长上下文档），再汇总各时段估值。**这不是订阅账单，也不用于推算剩余配额。**

内置[价格快照](../superlight/prices.json)于 2026-09-22 根据 [OpenAI 价格页](https://developers.openai.com/api/docs/pricing)核对，不自动联网更新。模型 ID 精确匹配，不猜测未知别名对应的价格。

估算不包含工具调用、Fast 模式、区域、多模态专项费用和缓存写入溢价。对存在缓存写入单价的模型，当前适配器尚无足够字段分离这部分费用。修改价格后，面板、汇总和导出会按选定价格表重新估算历史 Token，并展示价格快照日期。

## 自定义单价

复制内置价格表并修改，然后在 App 设置中选取，或使用 `--prices`。自定义文件会**完整替换**内置目录。

```json
{
  "currency": "USD",
  "as_of": "2026-09-22",
  "basis": "my_reference_prices",
  "models": {
    "my-model": {
      "input": "2.00",
      "cached_input": "0.20",
      "output": "12.00",
      "long_context": {
        "above_input_tokens": 272000,
        "input": "4.00",
        "cached_input": "0.40",
        "output": "18.00"
      }
    }
  }
}
```

长上下文档可选，仅在某个响应的输入量大于阈值时使用。使用十进制字符串保存精确单价。修改当前价格文件后，重启接收器。

## 数据与隐私

SQLite 是用量记录的权威来源。保存时间、模型、Token 数，以及去重需要的服务/会话/响应标识。提示词、回答、凭据、用户邮箱和工具输出会被丢弃，不落盘。面板导出时段汇总，CLI JSONL 导出标准化记录。

参考：[Codex 遥测](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry)、[OTLP 协议](https://opentelemetry.io/docs/specs/otlp/)。
