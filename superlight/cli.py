import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from . import __version__
from .pricing import Prices
from .proxy import parse_upstream
from .server import LocalServer
from .store import Store


def default_database():
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "superlight" / "usage.sqlite3"


def codex_command(args, port, executable, environment):
    """Only change this child process's proxy/export settings; preserve its login."""
    proxy = "http://127.0.0.1:{}".format(port)
    env = dict(environment)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env[name] = proxy
    bypass = ["127.0.0.1", "localhost", "::1"]
    for name in ("NO_PROXY", "no_proxy"):
        bypass.extend(x.strip() for x in environment.get(name, "").split(",") if x.strip())
    env["NO_PROXY"] = env["no_proxy"] = ",".join(dict.fromkeys(bypass))
    exporter = 'otel.exporter={otlp-http={endpoint="' + proxy + '/v1/logs",protocol="json"}}'
    arguments = args[1:] if args[:1] == ["--"] else args
    return [executable, "-c", exporter, "-c", "otel.log_user_prompt=false", *arguments], env


def _print_summary(groups):
    if not groups:
        print("尚未收到用量。请通过 superlight codex 启动 Codex，完成一次模型响应后再查询。")
        return
    header = ("MODEL", "INPUT", "CACHED*", "OUTPUT", "TOTAL", "EST. USD")
    rows = []
    for group in groups:
        cost = group["estimated_cost_usd"]
        if cost is None:
            cost = "unknown"
        elif group["unpriced_responses"]:
            cost += " (partial)"
        cached = str(group["cached_input_tokens"])
        if group["missing_cached_detail"]:
            cached += "+?"
        rows.append((group["model"], str(group["input_tokens"]), cached,
                     str(group["output_tokens"]), str(group["total_tokens"]), cost))
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)))
    print("* Cached 已包含在 Input 中；推理 token 已包含在 Output 中。")
    print("金额为标准 API 文本 token 参考价估算，非订阅扣费；仅包含可定价的记录。")
    missing = sum(g["missing_usage_responses"] for g in groups)
    if missing:
        print("有 {} 条响应缺少完整用量，未计入 token 合计。".format(missing))


def _serve(args, prices):
    upstream = parse_upstream(args.upstream)
    if upstream and upstream[1] == args.port and upstream[0] in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("upstream cannot point back to SuperLight")
    store = Store(args.db)
    log_lock = threading.Lock()
    file_log = None
    if args.log_file:
        path = args.log_file.expanduser()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(path), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(fd)
        file_log = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")

    def logger(record):
        with log_lock:
            line = json.dumps(record, ensure_ascii=False)
            if file_log:
                file_log.emit(logging.LogRecord("superlight", logging.INFO, "", 0, line, (), None))
            if not args.quiet:
                print(line, flush=True)

    server = None
    watcher_stop = threading.Event()
    old_signal = signal.getsignal(signal.SIGTERM)

    def stop(*_args):
        raise KeyboardInterrupt

    try:
        server = LocalServer(args.port, store, prices, upstream, logger)
        if args.parent_pid:
            def watch_parent():
                while not watcher_stop.wait(0.5):
                    if os.getppid() != args.parent_pid:
                        server.shutdown()
                        return
            threading.Thread(target=watch_parent, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        print("SuperLight listening on http://127.0.0.1:{}; upstream: {}".format(
            server.server_port, args.upstream or "direct"), file=sys.stderr)
        print("Usage database: {}".format(store.path.resolve()), file=sys.stderr)
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            pass
    finally:
        watcher_stop.set()
        signal.signal(signal.SIGTERM, old_signal)
        if server:
            server.server_close()
        store.close()
        if file_log:
            file_log.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="SuperLight: 本地转发、Token 用量与模型成本估算")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="启动本地代理与用量接收器")
    serve.add_argument("--port", type=int, default=12618)
    serve.add_argument("--upstream", help="可选 HTTP 上游代理，例如 http://127.0.0.1:7897")
    serve.add_argument("--quiet", action="store_true", help="不在终端打印用量日志")
    serve.add_argument("--log-file", type=Path, help="轮转日志文件（每份 1 MiB，保留 3 份备份）")
    serve.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    codex = commands.add_parser("codex", help="以本次进程配置启动已登录的 Codex CLI")
    codex.add_argument("--port", type=int, default=12618)
    codex.add_argument("args", nargs=argparse.REMAINDER, help="-- 后面的参数原样传给 Codex")
    summary = commands.add_parser("summary", help="按模型汇总用量与估算金额")
    summary.add_argument("--json", action="store_true")
    export = commands.add_parser("export", help="导出用量及估价为 JSONL")
    for command in (serve, summary, export):
        command.add_argument("--db", type=Path, default=default_database())
        command.add_argument("--prices", type=Path, help="自定义模型单价 JSON（完整替换内置价格表）")
    for command in (summary, export):
        command.add_argument("--today", action="store_true", help="按本机时区，仅查询今天的记录")
    args = parser.parse_args(argv)
    try:
        if args.command == "codex":
            executable = shutil.which("codex")
            if not executable:
                raise ValueError("未找到 codex，请先安装并登录 Codex CLI")
            # Explicitly bypass inherited proxy settings for the local health probe.
            opener = build_opener(ProxyHandler({}))
            with opener.open("http://127.0.0.1:{}/health".format(args.port), timeout=3) as response:
                health = json.load(response)
            if health.get("status") != "ok" or "recorded_responses" not in health:
                raise ValueError("该端口上的服务不是 SuperLight")
            command, env = codex_command(args.args, args.port, executable, os.environ)
            try:
                code = subprocess.call(command, env=env)
                return code if code >= 0 else 128 - code
            except KeyboardInterrupt:
                return 130
        prices = Prices(args.prices)
        if args.command == "serve":
            if not 1 <= args.port <= 65535:
                raise ValueError("port must be between 1 and 65535")
            return _serve(args, prices)
        since_ns = 0
        if args.today:
            since_ns = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()) * 10**9
        if not args.db.expanduser().exists():
            if args.command == "summary":
                if args.json:
                    print(json.dumps(dict(currency="USD", price_as_of=prices.catalog.get("as_of"),
                                          basis=prices.catalog.get("basis"), models=[])))
                else:
                    print("尚无用量数据库，请先启动 superlight serve。")
            return 0
        store = Store(args.db)
        try:
            if args.command == "export":
                for record in store.records(since_ns):
                    print(json.dumps(dict(record, **prices.estimate(record)), ensure_ascii=False))
            else:
                groups = prices.summary(store, since_ns)
                if args.json:
                    print(json.dumps(dict(currency="USD", price_as_of=prices.catalog.get("as_of"),
                                          basis=prices.catalog.get("basis"), models=groups), ensure_ascii=False, indent=2))
                else:
                    _print_summary(groups)
        finally:
            store.close()
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        print("SuperLight: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
