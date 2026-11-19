import SwiftUI
import AppKit
import Combine

// The command-line SDK provides the State property wrapper without Xcode’s macro plug-in.
private typealias ViewState<Value> = State<Value>

struct Article: Decodable, Identifiable {
    let id, title, source, url, summary, coverage, problem, discovered: String
    let published: Int
    let seen: Int
    let feeds: [String]
}
struct Feed: Decodable, Identifiable {
    let id, name, url: String
    let failed: Bool
}
struct Snapshot: Decodable {
    let articles: [Article]
    let feeds: [Feed]
    let progress: [String: String]
    let busy: Bool
    let mode, url, model: String
    let hasKey: Bool
}

enum Bridge {
    static let project = URL(fileURLWithPath: Bundle.main.object(forInfoDictionaryKey: "ProjectDirectory") as! String)
    static func call(_ args: [String], input: Data? = nil) async throws -> Data {
        try await Task.detached {
            let process = Process(), output = Pipe(), errors = Pipe(), stdin = Pipe()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            process.arguments = [project.appendingPathComponent("app.py").path] + args
            process.currentDirectoryURL = project
            process.standardOutput = output
            process.standardError = errors
            process.standardInput = stdin
            try process.run()
            if let input { stdin.fileHandleForWriting.write(input) }
            try stdin.fileHandleForWriting.close()
            let bytes = output.fileHandleForReading.readDataToEndOfFile()
            let problem = errors.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            if process.terminationStatus != 0 {
                let msg = String(decoding: problem, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
                throw NSError(domain: "BlogDaily", code: Int(process.terminationStatus),
                              userInfo: [NSLocalizedDescriptionKey: msg.isEmpty ? "操作未完成" : msg])
            }
            return bytes
        }.value
    }
}

@MainActor
final class Reader: ObservableObject {
    @Published var data: Snapshot?
    @Published var working = false
    @Published var message = ""
    @Published var error = ""
    var fetching = false
    var busy: Bool { working || data?.busy == true }

    func reload() async {
        guard !fetching else { return }
        fetching = true
        defer { fetching = false }
        do { data = try JSONDecoder().decode(Snapshot.self, from: try await Bridge.call(["snapshot"])) }
        catch { self.error = error.localizedDescription }
    }

    func perform(_ args: [String], input: Data? = nil) async -> Bool {
        guard !working else { return false }
        working = true; error = ""; message = ""
        defer { working = false }
        do {
            let response = try await Bridge.call(args, input: input)
            message = try JSONDecoder().decode(String.self, from: response)
            await reload()
            return true
        } catch {
            self.error = error.localizedDescription
            await reload()
            return false
        }
    }

    func open(_ article: Article) {
        guard let url = URL(string: article.url), ["https", "http"].contains(url.scheme),
              let safari = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.apple.Safari")
        else { error = "无法找到 Safari 或文章网址"; return }
        NSWorkspace.shared.open([url], withApplicationAt: safari, configuration: NSWorkspace.OpenConfiguration()) { _, problem in
            Task { @MainActor in
                if let problem { self.error = problem.localizedDescription; return }
                do { _ = try await Bridge.call(["read", article.id]); await self.reload() }
                catch { self.error = error.localizedDescription }
            }
        }
    }
}

private func dayKey() -> String {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.dateFormat = "yyyy-MM-dd"
    return f.string(from: Date())
}

struct Story: View {
    let a: Article
    let open: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack {
                Text(a.source).fontWeight(.semibold).foregroundStyle(.teal)
                if a.published > 0 {
                    Text(Date(timeIntervalSince1970: Double(a.published)), format: .dateTime.year().month().day())
                } else { Text("发布日期未标注") }
                if a.seen > 0 { Text("已读") }
                Spacer()
            }.font(.caption).foregroundStyle(.secondary)
            Button(action: open) {
                Text(a.title).font(.system(size: 22, weight: .medium, design: .serif))
                    .multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
            }.buttonStyle(.plain)
            Text(a.summary.isEmpty ? (a.problem.isEmpty ? "中文摘要准备中…" : "摘要待重试 · "+a.problem) : a.summary)
                .font(.body).lineSpacing(4).textSelection(.enabled)
            HStack {
                Button(action: open) { Label("用 Safari 阅读", systemImage: "safari") }.buttonStyle(.link)
                Spacer()
                Text(a.coverage+"节选").foregroundStyle(.secondary)
            }.font(.caption)
            Divider().padding(.top, 6)
        }.padding(.vertical, 6)
    }
}

enum Editor: String, Identifiable { case feed, provider; var id: String { rawValue } }

struct Home: View {
    @StateObject var reader = Reader()
    @ViewState private var selection: String? = "today"
    @ViewState private var editor: Editor?
    @ViewState private var remove: Feed?
    let tick = Timer.publish(every: 8, on: .main, in: .common).autoconnect()
    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                Section("阅读") {
                    Label("今日更新", systemImage: "sun.max").tag("today")
                    Label("全部文章", systemImage: "tray.full").tag("all")
                }
                Section("订阅来源") {
                    ForEach(reader.data?.feeds ?? []) { feed in
                        Label(feed.name,systemImage: feed.failed ? "exclamationmark.circle" : "dot.radiowaves.left.and.right")
                            .tag(feed.id)
                            .contextMenu { Button("取消订阅…") { remove = feed } }
                    }
                }
            }.navigationSplitViewColumnWidth(min: 170, ideal: 205)
            .safeAreaInset(edge: .bottom) {
                Button { editor = .feed } label: { Label("添加订阅",systemImage:"plus") }
                    .disabled(reader.busy).padding()
            }
        } detail: {
            VStack(alignment:.leading,spacing:0) {
                ScrollView {
                    VStack(alignment:.leading,spacing:12) {
                        Text(heading).font(.system(size:32,weight:.semibold))
                        Text(status).font(.callout).foregroundStyle(.secondary)
                        if let problem = reader.data?.progress["error"], !problem.isEmpty {
                            Text(problem).foregroundStyle(.orange).textSelection(.enabled)
                        }
                        if !reader.error.isEmpty { Text(reader.error).foregroundStyle(.red).textSelection(.enabled) }
                        else if !reader.message.isEmpty { Text(reader.message).font(.caption).foregroundStyle(.secondary) }
                        if let sub = reader.data?.feeds.first(where:{ $0.id == selection }) {
                            HStack {
                                Text(sub.url).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                                Spacer()
                                Button("取消订阅…") { remove = sub }.disabled(reader.busy)
                            }
                        }
                        Divider()
                        if let data = reader.data {
                            let rows = filtered(data)
                            if rows.isEmpty {
                                Text(selection == "today" ? "今天还没有发现新文章" : "暂时没有文章")
                                    .font(.title3).padding(.vertical,8)
                                if selection == "today", !data.articles.isEmpty {
                                    HStack {
                                        Text("最近收录").font(.headline)
                                        Spacer()
                                        Button("查看全部") { selection = "all" }.buttonStyle(.link)
                                    }
                                    ForEach(data.articles.prefix(3)) { a in Story(a:a){ reader.open(a) } }
                                }
                            } else {
                                ForEach(rows) { a in Story(a:a){ reader.open(a) } }
                            }
                        } else { ProgressView().controlSize(.small) }
                    }.frame(maxWidth:850,alignment:.leading).padding(28)
                    .frame(maxWidth:.infinity,alignment:.leading)
                }
            }
        }
        .toolbar {
            ToolbarItemGroup {
                Button { Task { _ = await reader.perform(["refresh"]) } } label: { Label("立即检查",systemImage:"arrow.clockwise") }
                    .disabled(reader.busy)
                Button { editor = .provider } label: { Label("摘要设置",systemImage:"slider.horizontal.3") }
                    .disabled(reader.busy)
            }
        }
        .task { await reader.reload() }
        .onReceive(tick) { _ in Task { await reader.reload() } }
        .sheet(item:$editor) { which in
            if which == .feed { AddFeed(reader: reader) }
            else { ProviderForm(reader: reader) }
        }
        .alert("取消订阅“"+(remove?.name ?? "")+"”？",isPresented:Binding(get:{remove != nil},set:{if !$0 {remove=nil}})) {
            Button("保留",role:.cancel) {}
            Button("取消订阅",role:.destructive) {
                guard let feed=remove else { return }
                Task { if await reader.perform(["remove",feed.id]) { selection="all" } }
            }
        } message: { Text("已收录文章仍可在「全部文章」阅读。") }
    }
    var heading: String {
        if selection == "today" { return "今日更新" }
        return reader.data?.feeds.first(where:{$0.id==selection})?.name ?? "全部文章"
    }
    var status: String {
        if reader.busy { return reader.data?.progress["phase"].flatMap{$0.isEmpty ? nil : $0} ?? "正在处理…" }
        let last = reader.data?.progress["last"] ?? "尚未检查"
        return "每天 09:00 自动检查  ·  上次检查 "+last
    }
    func filtered(_ data:Snapshot) -> [Article] {
        if selection == "today" { return data.articles.filter{$0.discovered==dayKey()} }
        if let selection, selection.hasPrefix("feed/") { return data.articles.filter{$0.feeds.contains(selection)} }
        return data.articles
    }
}

struct SubscriptionPreview: Decodable {
    struct Item: Decodable { let title, url: String }
    let kind, name, url: String
    let items: [Item]
    let count: Int?
}

struct AddFeed: View {
    @ObservedObject var reader: Reader
    @Environment(\.dismiss) var dismiss
    @ViewState private var url = ""
    @ViewState private var pattern = ""
    @ViewState private var forceWeb = false
    @ViewState private var inspecting = false
    @ViewState private var preview: SubscriptionPreview?
    @ViewState private var problem = ""

    func check() async {
        inspecting = true
        defer { inspecting = false }
        problem = ""
        do {
            let bytes = try await Bridge.call(["inspect",url,pattern,forceWeb ? "web" : "auto"])
            let result = try JSONDecoder().decode(SubscriptionPreview.self, from:bytes)
            if result.kind == "rss" {
                if await reader.perform(["add",result.url]) { dismiss() }
                else { problem = reader.error }
            } else { preview = result }
        } catch { problem = error.localizedDescription }
    }

    var body: some View {
        VStack(alignment:.leading,spacing:14) {
            Text("添加博客订阅").font(.title2.bold())
            Group {
                TextField("博客文章列表、主页或 RSS 地址",text:$url)
                Toggle("指定网页文章路径",isOn:$forceWeb)
                if forceWeb {
                    TextField("可选，如 /research/* 或 /blog/*.html",text:$pattern)
                }
            }.textFieldStyle(.roundedBorder).disabled(inspecting || reader.working)
            if let preview {
                Text("网页订阅 · "+preview.name).font(.headline)
                Text("识别到 "+String(preview.count ?? preview.items.count)+" 个链接，以下为列表开头的文章。")
                    .font(.callout).foregroundStyle(.secondary)
                ScrollView {
                    VStack(alignment:.leading,spacing:8) {
                        ForEach(preview.items,id:\.url) { item in
                            VStack(alignment:.leading) {
                                Text(item.title).lineLimit(2)
                                Text(item.url).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                        }
                    }.frame(maxWidth:.infinity,alignment:.leading)
                }.frame(maxHeight:210)
                Text("首次收录前三篇；以后新出现的文章进入当天日报。").font(.caption)
            } else {
                Text("优先识别 RSS。普通网页会先预览文章链接。").foregroundStyle(.secondary)
            }
            if !problem.isEmpty { Text(problem).foregroundStyle(.red).textSelection(.enabled) }
            HStack {
                if inspecting || reader.working { ProgressView().controlSize(.small) }
                Spacer()
                Button("关闭") { dismiss() }.disabled(inspecting || reader.working)
                Button(preview == nil ? "识别订阅" : "订阅此网页") {
                    Task {
                        if let preview {
                            if await reader.perform(["add-web",preview.url,pattern]) { dismiss() }
                            else { problem = reader.error }
                        } else { await check() }
                    }
                }.keyboardShortcut(.defaultAction)
                    .disabled(url.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty || reader.busy || inspecting)
            }
        }
        .onChange(of:url) { _,_ in preview=nil; problem="" }
        .onChange(of:pattern) { _,_ in preview=nil; problem="" }
        .onChange(of:forceWeb) { _,enabled in preview=nil; problem=""; if !enabled { pattern="" } }
        .padding(24).frame(width:510)
    }
}

struct ProviderForm: View {
    @ObservedObject var reader: Reader
    @Environment(\.dismiss) var dismiss
    @ViewState private var mode = "local"
    @ViewState private var url = ""
    @ViewState private var model = ""
    @ViewState private var key = ""
    var body: some View {
        VStack(alignment:.leading,spacing:14) {
            Text("中文摘要").font(.title2.bold())
            Picker("生成方式",selection:$mode) {
                Text("免费本机模型").tag("local"); Text("自有 API").tag("api")
            }.pickerStyle(.segmented)
            if mode == "api" {
                Text("支持 Chat Completions 兼容接口。生成摘要时会把文章节选发送给所选服务。").foregroundStyle(.secondary)
                TextField("HTTPS 接口地址，例如 https://example.com/v1", text:$url)
                TextField("模型名称",text:$model)
                SecureField(reader.data?.hasKey == true ? "密钥已保存，留空沿用" : "API 密钥",text:$key)
            } else { Text("Qwen 在 Mac 上生成摘要，无需 API 密钥。").foregroundStyle(.secondary) }
            if !reader.error.isEmpty { Text(reader.error).foregroundStyle(.red) }
            HStack { Spacer(); Button("取消"){dismiss()}
                Button("保存") {
                    let form = ["mode":mode,"url":url,"model":model,"key":key]
                    Task { if await reader.perform(["settings"],input:try? JSONEncoder().encode(form)) { dismiss() } }
                }.keyboardShortcut(.defaultAction).disabled(reader.working)
            }
        }.onAppear { if let s=reader.data { mode=s.mode; url=s.url; model=s.model } }
        .textFieldStyle(.roundedBorder).padding(24).frame(width:480)
    }
}

@main struct BlogDaily: App {
    var body: some Scene {
        Window("博客日报",id:"main") { Home().frame(minWidth:740,minHeight:520).environment(\.locale,Locale(identifier:"zh_CN")) }
            .defaultSize(width:980,height:720)
    }
}
