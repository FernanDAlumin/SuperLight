# Configuration

[中文](configuration.zh-CN.md) · [Home](../README.md)

## Desktop app

Build once with `make app`, then open `dist/SuperLight.app`. Its Python backend and web assets are copied into the app bundle, so the app does not need the repository after building. A compatible Python installation is still required.

Click the menu bar amount for the compact panel. **Open dashboard** opens the full window. Right-click the label for settings, the data folder, restart, and optional launch at login. Closing the dashboard leaves the app running; quitting SuperLight stops the collector it started. An independently started collector is left running.

Settings support:

| Setting | Default / example |
|---|---|
| Local port | `12618`, loopback only |
| Upstream HTTP proxy | Empty for direct; e.g. `http://127.0.0.1:7897` |
| Database | `~/.local/share/superlight/usage.sqlite3` |
| Custom prices | Empty for bundled prices; otherwise an absolute JSON path |

To keep an earlier database, select its absolute path in Settings. For example, a development database may be at `<repo>/.superlight/live-usage.sqlite3`. Settings apply when the app restarts its collector. When connecting to an independently started collector, change its launch options instead.

The menu bar shows today's estimated cost, rounded to cents. `*` means some responses could not be priced; `—` means unavailable. The dashboard retains more precision. Logs are stored beside the selected database as `superlight.log`, with three backups of up to 1 MiB each.

## Connect Codex

Merge this into **user-level** `~/.codex/config.toml`. If `[otel]` already exists, modify that section rather than adding another:

```toml
[otel]
log_user_prompt = false
exporter = { otlp-http = { endpoint = "http://127.0.0.1:12618/v1/logs", protocol = "json" } }
```

Restart Codex and complete a local task. Keep SuperLight running. This exports usage events; your existing ChatGPT login and model network configuration stay intact. The collector does not decrypt HTTPS. [Official configuration documentation](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry).

This also applies to the desktop app's local Codex backend. Remote tasks read configuration on the remote host, where `127.0.0.1` means that host. Use a collector there or an appropriate loopback tunnel. Cloud task usage is not automatically captured.

## Command line

```bash
python3 -m superlight serve --quiet --log-file .superlight/events.log
python3 -m superlight codex -- -C /path/to/project
python3 -m superlight summary --today
python3 -m superlight export --today > usage.jsonl
```

To chain forwarding through Clash, add `--upstream http://127.0.0.1:7897` to `serve`. Use Clash's HTTP or mixed port; SOCKS-only and authenticated upstream proxies are not supported. Starting the server alone does not configure a client.

`serve`, `summary`, and `export` accept `--db` and `--prices`; use the same files across commands. `serve` and `codex` accept `--port`. CLI storage honors `XDG_DATA_HOME`, falling back to `~/.local/share/superlight`.

## Check the connection

```bash
curl --noproxy '*' http://127.0.0.1:12618/health
```

| Symptom | Check |
|---|---|
| No menu bar label | Open the app again; make room in the menu bar if crowded. |
| Port already in use | Quit the old collector, or choose another port in both SuperLight and Codex. |
| Empty dashboard | Finish a task after enabling telemetry; allow a few seconds for export. Verify the database path. |
| Telemetry received, no usage | Check the Codex version and whether it exports completion counts. |
| Amount is unknown | Add the exact model ID to your price file, or check missing cache details. |
| Menu shows offline | Open Settings and verify Python, port, database, and custom price paths. |

`last_telemetry_ns` tracks batches received since startup. `recorded_responses` counts persisted completion records. The UI refreshes roughly every 10 seconds. Only same-origin local browser requests can read the dashboard API; third-party browser requests cannot submit telemetry.

## Build and verify

`make app` uses Swift, AppKit, WebKit, ServiceManagement, and Python. Install Xcode Command Line Tools if `xcrun swiftc` is unavailable. Builds target the Mac's architecture and macOS 13+. The local signature is not Apple notarization.

`make test` runs standard-library tests using temporary databases and local servers. It makes no model API calls.
