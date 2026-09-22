import AppKit
import WebKit
import ServiceManagement

@MainActor
final class SuperLightApp: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    let defaults = UserDefaults.standard
    var statusItem: NSStatusItem!
    let popover = NSPopover()
    var webView: WKWebView!
    var dashboardWindow: NSWindow?
    var preferencesWindow: NSWindow?
    var fields: [String: NSTextField] = [:]
    var process: Process?
    var timer: Timer?
    var starting = false
    var quitting = false
    var attached = false
    var online = false
    var lastError = ""
    var lang: String { defaults.string(forKey: "language") ?? (Locale.preferredLanguages.first?.hasPrefix("zh") == true ? "zh" : "en") }
    var port: Int { let p = defaults.integer(forKey: "port"); return p == 0 ? 12618 : p }
    var upstream: String { defaults.string(forKey: "upstream") ?? "" }
    var database: String { defaults.string(forKey: "database") ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/share/superlight/usage.sqlite3").path }
    var priceFile: String { defaults.string(forKey: "prices") ?? "" }
    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)")! }
    var dataFolder: URL { URL(fileURLWithPath: database).deletingLastPathComponent() }
    var configSnippet: String { "[otel]\nlog_user_prompt = false\nexporter = { otlp-http = { endpoint = \"http://127.0.0.1:\(port)/v1/logs\", protocol = \"json\" } }\n" }
    lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 3
        config.connectionProxyDictionary = ["HTTPEnable": 0, "HTTPSEnable": 0, "SOCKSEnable": 0]
        return URLSession(configuration: config)
    }()
    func text(_ en: String, _ zh: String) -> String { lang == "zh" ? zh : en }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Command-line options are useful for existing installations and local development.
        let args = CommandLine.arguments
        for (flag, key) in [("--db", "database"), ("--upstream", "upstream"), ("--prices", "prices"), ("--port", "port")] {
            if let i = args.firstIndex(of: flag), i + 1 < args.count {
                if key == "port", let value = Int(args[i + 1]), (1...65535).contains(value) { defaults.set(value, forKey: key) }
                else if key != "port" { defaults.set((args[i + 1] as NSString).expandingTildeInPath, forKey: key) }
            }
        }
        NSApp.setActivationPolicy(.accessory)
        let mainMenu = NSMenu()
        let applicationItem = NSMenuItem(); let applicationMenu = NSMenu(title: "SuperLight")
        applicationMenu.addItem(withTitle: text("Quit SuperLight", "退出 SuperLight"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        applicationItem.submenu = applicationMenu; mainMenu.addItem(applicationItem)
        let editItem = NSMenuItem(); let editMenu = NSMenu(title: text("Edit", "编辑"))
        editMenu.addItem(withTitle: text("Copy", "复制"), action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: text("Paste", "粘贴"), action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: text("Select All", "全选"), action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu; mainMenu.addItem(editItem); NSApp.mainMenu = mainMenu
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.autosaveName = "SuperLight"
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "bolt.fill", accessibilityDescription: "SuperLight")
            button.image?.isTemplate = true
            button.imagePosition = .imageLeading
            button.font = .monospacedDigitSystemFont(ofSize: 12, weight: .medium)
            button.title = " —"
            button.toolTip = "SuperLight"
            button.target = self
            button.action = #selector(togglePopover)
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }
        webView = makeWebView()
        let controller = NSViewController()
        controller.view = webView
        popover.contentViewController = controller
        popover.contentSize = NSSize(width: 420, height: 650)
        popover.behavior = .transient
        connectOrStart()
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in DispatchQueue.main.async { self?.refreshBadge() } }
        if args.contains("--show-window") { DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.showDashboardWindow() } }
    }

    func makeWebView() -> WKWebView {
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "superlight")
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = self
        return view
    }

    @objc func togglePopover() {
        if NSApp.currentEvent?.type == .rightMouseUp { showMenu(); return }
        if popover.isShown { popover.performClose(nil); return }
        guard let button = statusItem.button else { return }
        if !online { showOfflinePanel() }
        else if webView.url?.host != "127.0.0.1" { loadViews() }
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        NSApp.activate(ignoringOtherApps: true)
    }

    func showMenu() {
        let menu = NSMenu()
        let status = NSMenuItem(title: online ? text("Collector running", "接收器运行中") : text("Collector offline", "接收器未连接"), action: nil, keyEquivalent: "")
        status.isEnabled = false; menu.addItem(status)
        if attached { let note = NSMenuItem(title: text("Using an existing collector", "已连接到现有接收器"), action: nil, keyEquivalent: ""); note.isEnabled = false; menu.addItem(note) }
        menu.addItem(.separator())
        for (label, action) in [(text("Open dashboard", "打开完整面板"), #selector(showDashboardWindow)),
                                (text("Copy Codex configuration", "复制 Codex 配置"), #selector(copyConfig)),
                                (text("Settings…", "设置…"), #selector(showPreferences)),
                                (text("Open data folder", "打开数据文件夹"), #selector(openDataFolder)),
                                (text("Restart collector", "重启接收器"), #selector(restartCollector))] {
            let item = NSMenuItem(title: label, action: action, keyEquivalent: ""); item.target = self; menu.addItem(item)
        }
        let login = NSMenuItem(title: text("Launch at login", "登录时启动"), action: #selector(toggleLogin), keyEquivalent: "")
        login.target = self; login.state = SMAppService.mainApp.status == .enabled ? .on : .off; menu.addItem(login)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: text("Quit SuperLight", "退出 SuperLight"), action: #selector(quitApp), keyEquivalent: "q")
        quit.target = self; menu.addItem(quit)
        statusItem.menu = menu
        statusItem.button?.performClick(nil)
        statusItem.menu = nil
    }

    func connectOrStart() {
        guard !starting, !quitting else { return }
        starting = true
        session.dataTask(with: baseURL.appendingPathComponent("health")) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                if let response = response as? HTTPURLResponse {
                    if response.statusCode == 200, let data = data,
                       let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                       object["status"] as? String == "ok", object["dashboard_api_version"] as? Int == 1 {
                        self.starting = false; self.attached = true; self.didConnect(); return
                    }
                    self.starting = false
                    self.fail(self.text("Port \(self.port) is used by another service or an older SuperLight. Stop that service, then choose Restart collector.", "端口 \(self.port) 已被其他服务或旧版 SuperLight 占用。请停止该服务，然后选择“重启接收器”。")); return
                }
                self.launchCollector()
            }
        }.resume()
    }

    func pythonExecutable() -> String? {
        let candidates = [Bundle.main.url(forResource: "python-path", withExtension: "txt").flatMap { try? String(contentsOf: $0, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines) }, "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"].compactMap { $0 }
        for candidate in candidates where FileManager.default.isExecutableFile(atPath: candidate) {
            let probe = Process(); probe.executableURL = URL(fileURLWithPath: candidate)
            probe.arguments = ["-c", "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)"]
            probe.standardOutput = FileHandle.nullDevice; probe.standardError = FileHandle.nullDevice
            do { try probe.run(); probe.waitUntilExit(); if probe.terminationStatus == 0 { return candidate } } catch { continue }
        }
        return nil
    }

    func launchCollector() {
        guard let python = pythonExecutable(), let resources = Bundle.main.resourceURL else {
            starting = false; fail(text("Python 3.9 or newer is required. Install Python, then reopen SuperLight.", "需要 Python 3.9 或更新版本。安装后重新打开 SuperLight。")); return
        }
        do { try FileManager.default.createDirectory(at: dataFolder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700]) }
        catch { starting = false; fail(error.localizedDescription); return }
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        child.currentDirectoryURL = resources.appendingPathComponent("backend")
        var args = ["-m", "superlight", "serve", "--port", String(port), "--db", database, "--quiet", "--log-file", dataFolder.appendingPathComponent("superlight.log").path, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        if !upstream.isEmpty { args += ["--upstream", upstream] }
        if !priceFile.isEmpty { args += ["--prices", priceFile] }
        child.arguments = args
        var env = ProcessInfo.processInfo.environment
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = resources.appendingPathComponent("backend").path
        child.environment = env
        child.standardOutput = FileHandle.nullDevice
        let pipe = Pipe(); child.standardError = pipe
        child.terminationHandler = { [weak self] stopped in
            let output = pipe.fileHandleForReading.readDataToEndOfFile()
            DispatchQueue.main.async {
                guard let self = self, self.process === stopped else { return }
                self.process = nil; self.starting = false; self.online = false; self.updateBadge(nil)
                if !self.quitting && stopped.terminationStatus != 0 {
                    self.fail(String(data: output, encoding: .utf8) ?? self.text("Collector stopped. Check Settings.", "接收器已停止，请检查设置。"))
                }
            }
        }
        do { try child.run(); process = child; attached = false; waitForCollector(remaining: 30) }
        catch { starting = false; fail(error.localizedDescription) }
    }

    func waitForCollector(remaining: Int) {
        guard !quitting, process?.isRunning == true else { starting = false; return }
        session.dataTask(with: baseURL.appendingPathComponent("health")) { [weak self] data, response, _ in
            DispatchQueue.main.async {
                guard let self = self else { return }
                if (response as? HTTPURLResponse)?.statusCode == 200 { self.starting = false; self.didConnect() }
                else if remaining > 0 { DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { self.waitForCollector(remaining: remaining - 1) } }
                else { self.starting = false; self.fail(self.text("The collector did not become ready. Check Settings or the data folder.", "接收器未能就绪。请检查设置或数据文件夹。")) }
            }
        }.resume()
    }

    func didConnect() { online = true; lastError = ""; loadViews(); refreshBadge() }
    func loadViews() {
        webView.load(URLRequest(url: URL(string: baseURL.absoluteString + "/?compact=1")!))
        (dashboardWindow?.contentView as? WKWebView)?.load(URLRequest(url: baseURL))
    }
    func refreshBadge() {
        guard !starting, !quitting else { return }
        session.dataTask(with: baseURL.appendingPathComponent("api/dashboard")) { [weak self] data, response, _ in
            let object = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            DispatchQueue.main.async {
                guard let self = self else { return }
                guard (response as? HTTPURLResponse)?.statusCode == 200,
                      let periods = object?["periods"] as? [String: Any], let day = periods["day"] as? [String: Any],
                      let totals = day["totals"] as? [String: Any] else { self.online = false; self.updateBadge(nil); return }
                let reconnect = !self.online
                self.online = true; self.updateBadge(totals)
                if reconnect { self.loadViews() }
            }
        }.resume()
    }
    func updateBadge(_ totals: [String: Any]?) {
        var label = " —"
        if let value = totals?["estimated_cost_usd"] as? String, let decimal = Decimal(string: value) {
            let formatter = NumberFormatter(); formatter.numberStyle = .currency; formatter.currencyCode = "USD"
            formatter.locale = Locale(identifier: "en_US"); formatter.maximumFractionDigits = 2
            label = decimal > 0 && decimal < Decimal(string: "0.01")! ? " <$0.01" : " " + (formatter.string(from: decimal as NSDecimalNumber) ?? "$0.00")
            if let missing = totals?["unpriced_responses"] as? Int, missing > 0 { label += "*" }
        }
        statusItem.button?.title = label
        statusItem.button?.toolTip = online ? text("SuperLight · Today’s estimated API cost (USD)", "SuperLight · 今日估算 API 成本（USD）") : text("SuperLight · Collector offline", "SuperLight · 接收器未连接")
        statusItem.button?.setAccessibilityLabel("SuperLight" + label)
    }

    func fail(_ message: String) { lastError = message; online = false; updateBadge(nil); showOfflinePanel() }
    func showOfflinePanel() {
        let controller = NSViewController()
        let view = NSView(frame: NSRect(x: 0, y: 0, width: 420, height: 320))
        let title = NSTextField(labelWithString: "SuperLight")
        title.font = .systemFont(ofSize: 25, weight: .semibold); title.frame = NSRect(x: 28, y: 248, width: 350, height: 40)
        let label = NSTextField(wrappingLabelWithString: lastError.isEmpty ? text("Connecting to your local collector…", "正在连接本机接收器…") : lastError)
        label.frame = NSRect(x: 28, y: 110, width: 360, height: 125); label.textColor = .secondaryLabelColor
        let button = NSButton(title: text("Settings…", "设置…"), target: self, action: #selector(showPreferences))
        button.bezelStyle = .rounded; button.frame = NSRect(x: 22, y: 52, width: 150, height: 34)
        let retry = NSButton(title: text("Retry", "重试"), target: self, action: #selector(restartCollector))
        retry.bezelStyle = .rounded; retry.frame = NSRect(x: 240, y: 52, width: 150, height: 34)
        view.addSubview(title); view.addSubview(label); view.addSubview(button); view.addSubview(retry)
        controller.view = view; popover.contentViewController = controller; popover.contentSize = view.frame.size
    }

    @objc func showDashboardWindow() {
        popover.performClose(nil)
        if dashboardWindow == nil {
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 850), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            window.title = "SuperLight"; window.minSize = NSSize(width: 620, height: 560); window.isReleasedWhenClosed = false
            let view = makeWebView(); window.contentView = view; view.load(URLRequest(url: baseURL)); window.center(); dashboardWindow = window
        }
        dashboardWindow?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }

    @objc func showPreferences() {
        popover.performClose(nil)
        if let window = preferencesWindow { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 540, height: 400), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = text("SuperLight Settings", "SuperLight 设置"); window.isReleasedWhenClosed = false
        let view = NSView(frame: window.contentLayoutRect)
        let entries = [("port", text("Local port", "本机端口"), String(port)),
                       ("upstream", text("HTTP proxy · leave empty for direct", "HTTP 上游代理 · 留空则直连"), upstream),
                       ("database", text("Usage database", "用量数据库"), database),
                       ("prices", text("Custom price file · optional", "自定义单价文件 · 可选"), priceFile)]
        for (index, entry) in entries.enumerated() {
            let y = CGFloat(335 - index * 74)
            let label = NSTextField(labelWithString: entry.1); label.font = .systemFont(ofSize: 11); label.textColor = .secondaryLabelColor
            label.frame = NSRect(x: 24, y: y + 24, width: 490, height: 18)
            let field = NSTextField(string: entry.2); field.frame = NSRect(x: 24, y: y - 2, width: 490, height: 25)
            if entry.0 == "upstream" { field.placeholderString = "http://127.0.0.1:7897" }
            fields[entry.0] = field; view.addSubview(label); view.addSubview(field)
        }
        let save = NSButton(title: text("Save and restart", "保存并重启"), target: self, action: #selector(savePreferences)); save.bezelStyle = .rounded; save.keyEquivalent = "\r"; save.frame = NSRect(x: 345, y: 20, width: 175, height: 32); view.addSubview(save)
        window.contentView = view; window.center(); preferencesWindow = window; window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }
    @objc func savePreferences() {
        guard !attached else { alert(text("An external collector is running. Change its launch options, or stop it before applying settings here.", "当前连接的是外部启动的接收器。请修改它的启动参数，或先停止它再在这里应用设置。")); return }
        guard let value = fields["port"]?.stringValue, let port = Int(value), (1...65535).contains(port) else { alert(text("Enter a valid port (1–65535).", "请输入有效端口（1–65535）。")); return }
        let proxy = fields["upstream"]?.stringValue.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !proxy.isEmpty {
            guard let url = URLComponents(string: proxy), url.scheme == "http", let host = url.host, !host.isEmpty,
                  url.user == nil, url.password == nil, url.query == nil, url.fragment == nil, (url.path.isEmpty || url.path == "/") else { alert(text("Use an HTTP proxy URL, for example http://127.0.0.1:7897.", "请输入 HTTP 代理地址，例如 http://127.0.0.1:7897。")); return }
        }
        let db = ((fields["database"]?.stringValue ?? "") as NSString).expandingTildeInPath
        guard db.hasPrefix("/"), !db.hasSuffix("/") else { alert(text("Choose an absolute database file path.", "请输入数据库文件的绝对路径。")); return }
        defaults.set(port, forKey: "port"); defaults.set(proxy, forKey: "upstream"); defaults.set(db, forKey: "database")
        defaults.set(((fields["prices"]?.stringValue ?? "") as NSString).expandingTildeInPath, forKey: "prices")
        preferencesWindow?.close(); preferencesWindow = nil; restartCollector()
    }

    @objc func copyConfig() { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(configSnippet, forType: .string) }
    func exportSummary(_ period: String) {
        guard ["day", "week", "month"].contains(period) else { return }
        session.dataTask(with: baseURL.appendingPathComponent("api/dashboard")) { [weak self] data, _, _ in
            DispatchQueue.main.async {
                guard let self = self, let data = data,
                      let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let periods = object["periods"] as? [String: Any], let summary = periods[period],
                      let output = try? JSONSerialization.data(withJSONObject: ["currency": "USD", "period": period, "summary": summary], options: [.prettyPrinted, .sortedKeys]) else { return }
                self.popover.performClose(nil)
                let panel = NSSavePanel(); panel.nameFieldStringValue = "superlight-\(period).json"
                NSApp.activate(ignoringOtherApps: true)
                if panel.runModal() == .OK, let url = panel.url {
                    do { try output.write(to: url, options: .atomic) } catch { self.alert(error.localizedDescription) }
                }
            }
        }.resume()
    }
    @objc func openDataFolder() { NSWorkspace.shared.open(dataFolder) }
    @objc func restartCollector() {
        if attached { attached = false; starting = false; connectOrStart(); return }
        if let child = process, child.isRunning {
            child.terminationHandler = nil; child.terminate(); process = nil; online = false; starting = true
            DispatchQueue.global().async { child.waitUntilExit(); DispatchQueue.main.async { self.starting = false; self.connectOrStart() } }
        } else { starting = false; connectOrStart() }
    }
    @objc func toggleLogin() {
        do { if SMAppService.mainApp.status == .enabled { try SMAppService.mainApp.unregister() } else { try SMAppService.mainApp.register() } }
        catch { alert(error.localizedDescription) }
    }
    func alert(_ message: String) { let alert = NSAlert(); alert.messageText = "SuperLight"; alert.informativeText = message; alert.addButton(withTitle: "OK"); NSApp.activate(ignoringOtherApps: true); alert.runModal() }
    @objc func quitApp() { NSApp.terminate(nil) }
    func applicationWillTerminate(_ notification: Notification) { quitting = true; timer?.invalidate(); process?.terminationHandler = nil; if process?.isRunning == true { process?.terminate() } }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if dashboardWindow?.isVisible == true { dashboardWindow?.makeKeyAndOrderFront(nil) }
        else if !popover.isShown { togglePopover() }
        return true
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, message.frameInfo.securityOrigin.host == "127.0.0.1", message.frameInfo.securityOrigin.port == port,
              let body = message.body as? [String: String], let action = body["action"] else { return }
        switch action {
        case "copyConfig": copyConfig()
        case "openDashboard": showDashboardWindow()
        case "settings": showPreferences()
        case "export": if let period = body["period"] { exportSummary(period) }
        case "language": if let value = body["value"], ["en", "zh"].contains(value) { defaults.set(value, forKey: "language") }
        default: break
        }
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = navigationAction.request.url, url.scheme == "http", url.host == "127.0.0.1", url.port == port { decisionHandler(.allow) }
        else { decisionHandler(.cancel) }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView === self.webView, popover.contentViewController?.view !== webView {
            let controller = NSViewController(); controller.view = webView; popover.contentViewController = controller; popover.contentSize = NSSize(width: 420, height: 650)
        }
    }
}

@main
struct SuperLightMain {
    @MainActor static func main() {
        let app = NSApplication.shared
        let delegate = SuperLightApp()
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
