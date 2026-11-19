import SwiftUI
import AppKit
import Combine

// The command-line SDK provides the State property wrapper without Xcode’s macro plug-in.
private typealias ViewState<Value> = State<Value>

struct Article: Decodable, Identifiable {
    let id, title, source, url, summary, coverage, problem, discovered: String
    let originalAbstract: String?
    let published: Int
    let seen, starred, queued: Int
    let feeds: [String]
}
struct Feed: Decodable, Identifiable {
    let id, name, url: String
    let folder: Int?
    let avatarPath: String?
    let failed: Bool
}
private struct SiteAvatar: View {
    let feed: Feed
    @ViewState private var icon: NSImage?
    private let palette: [Color] = [.indigo, .teal, .brown, .purple, .blue]

    var body: some View {
        ZStack {
            if let icon {
                RoundedRectangle(cornerRadius: 4).fill(Color(white: 0.97))
                Image(nsImage: icon).renderingMode(.original).resizable()
                    .interpolation(.high).scaledToFit().padding(1)
            } else {
                let index = feed.url.utf8.reduce(0) { ($0 + Int($1)) % palette.count }
                RoundedRectangle(cornerRadius: 4).fill(palette[index])
                Text(String(feed.name.prefix(1)).uppercased())
                    .font(.system(size: 13, weight: .bold)).foregroundStyle(.white)
            }
        }
        .frame(width: 20, height: 20).clipShape(RoundedRectangle(cornerRadius: 4))
        .accessibilityHidden(true)
        .task(id: feed.id + dayKey()) {
            if let cached = feed.avatarPath { icon = NSImage(contentsOfFile: cached) }
            guard let bytes = try? await Bridge.call(["avatar",feed.id]),
                  let path = try? JSONDecoder().decode(String.self, from: bytes),
                  !Task.isCancelled else { return }
            icon = NSImage(contentsOfFile: path)
        }
    }
}

struct SourceFolder: Decodable, Identifiable {
    let id: Int
    let name: String
    var tag: String { "folder/\(id)" }
}

struct UpdatePlan: Codable {
    var frequency = "daily"
    var hour = 9
    var minute = 0
    var weekday = 1
}

struct Snapshot: Decodable {
    let schedule: UpdatePlan
    let folders: [SourceFolder]
    let articles: [Article]
    let feeds: [Feed]
    let progress: [String: String]
    let busy: Bool
    let mode, url, model: String
    let hasKey, needsSetup: Bool
}

enum Bridge {
    private static let sourcePath = Bundle.main.object(forInfoDictionaryKey: "ProjectDirectory") as? String
    static let packaged = sourcePath == nil
    static let project = sourcePath.map { URL(fileURLWithPath: $0) } ?? Bundle.main.resourceURL!
    static let python = packaged ? project.appendingPathComponent("python/bin/python3.13") : URL(fileURLWithPath: "/usr/bin/python3")
    static func call(_ args: [String], input: Data? = nil) async throws -> Data {
        try await Task.detached {
            let process = Process(), output = Pipe(), errors = Pipe(), stdin = Pipe()
            process.executableURL = python
            process.arguments = ["-B", "-E", "-s", project.appendingPathComponent("app.py").path] + args
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
                throw NSError(domain: "Foliofinch", code: Int(process.terminationStatus),
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
    private var revision = 0
    @Published var changing: Set<String> = []
    var busy: Bool { working || data?.busy == true }

    func reload() async {
        revision += 1
        let ticket = revision
        do {
            let snapshot = try JSONDecoder().decode(Snapshot.self, from: try await Bridge.call(["snapshot"]))
            if revision == ticket { data = snapshot }
        } catch { if revision == ticket { self.error = error.localizedDescription } }
    }

    func change(_ command: String, article: Article) async {
        guard changing.insert(article.id).inserted else { return }
        defer { changing.remove(article.id) }
        do {
            _ = try await Bridge.call([command, article.id])
            await reload()
        } catch { self.error = error.localizedDescription }
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

private struct Story: View {
    let a: Article
    @ObservedObject var reader: Reader
    let confirmDelete: () -> Void
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Circle().fill(a.seen == 0 ? Color.teal : Color.secondary.opacity(0.32))
                    .frame(width: 7, height: 7)
                    .accessibilityLabel(a.seen == 0 ? "未读" : "已读")
                Text(a.source).fontWeight(.semibold).foregroundStyle(.primary).lineLimit(1)
                Spacer()
                if a.published > 0 {
                    Text(Date(timeIntervalSince1970: Double(a.published)), format: .dateTime.year().month().day())
                } else { Text("发布日期未标注") }
            }.font(.caption).foregroundStyle(.secondary)
            Button { reader.open(a) } label: {
                Text(a.title).font(.system(size: 22, weight: .semibold, design: .serif))
                    .multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
            }.buttonStyle(.plain)
            if a.coverage == "论文原摘要" {
                Label("作者摘要 · 中文译文", systemImage: "doc.text").font(.caption).foregroundStyle(.secondary)
            }
            Text(a.summary.isEmpty ? (a.problem.isEmpty ? "中文摘要准备中…" : "摘要待重试 · "+a.problem) : a.summary)
                .font(.body).lineSpacing(5).textSelection(.enabled)
            if let original = a.originalAbstract, !original.isEmpty {
                DisclosureGroup("查看原摘要") {
                    Text(original).font(.body).lineSpacing(5).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }.font(.caption)
            }
            HStack(spacing: 14) {
                Button { reader.open(a) } label: { Label("用 Safari 阅读", systemImage: "safari") }
                    .help(a.coverage == "论文原摘要" ? "打开论文摘要页" : a.coverage + "节选 · 打开原文")
                Spacer()
                Button {
                    Task { await reader.change(a.queued == 0 ? "queue" : "unqueue", article: a) }
                } label: {
                    Label(a.queued == 0 ? "待读" : "已加入待读", systemImage: a.queued == 0 ? "clock" : "clock.fill")
                }.help(a.queued == 0 ? "加入待读" : "移出待读")
                 .accessibilityLabel(a.queued == 0 ? "加入待读" : "移出待读")
                Button {
                    Task { await reader.change(a.seen == 0 ? "read" : "unread", article: a) }
                } label: {
                    Label(a.seen == 0 ? "标为已读" : "已读", systemImage: a.seen == 0 ? "circle" : "checkmark.circle.fill")
                }.help(a.seen == 0 ? "标为已读" : "标为未读")
                 .accessibilityLabel(a.seen == 0 ? "标为已读" : "标为未读")
                Button {
                    Task { await reader.change(a.starred == 0 ? "star" : "unstar", article: a) }
                } label: {
                    Label(a.starred == 0 ? "收藏" : "已收藏", systemImage: a.starred == 0 ? "bookmark" : "bookmark.fill")
                }.tint(a.starred == 0 ? Color.secondary : Color.teal)
                 .help(a.starred == 0 ? "收藏文章" : "取消收藏")
                 .accessibilityLabel(a.starred == 0 ? "收藏文章" : "取消收藏")
                Button(action: confirmDelete) {
                    Label("删除文章", systemImage: "trash").labelStyle(.iconOnly)
                }.tint(.secondary).help("删除文章")
            }.font(.callout).buttonStyle(.borderless).disabled(reader.changing.contains(a.id))
             .padding(.top, 5)
        }
        .padding(18).frame(maxWidth: .infinity, alignment: .leading)
        .background(scheme == .dark ? Color(white: 0.15) : .white, in: RoundedRectangle(cornerRadius: 12))
        .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(.primary.opacity(0.09)) }
    }
}

enum Editor: String, Identifiable { case feed, provider, schedule, folder; var id: String { rawValue } }

struct Home: View {
    @StateObject var reader = Reader()
    @ViewState private var selection: String? = "today"
    @ViewState private var editor: Editor?
    @ViewState private var remove: Feed?
    @ViewState private var deleteCandidate: Article?
    @ViewState private var showSyncDetails = false
    @ViewState private var editFolder: SourceFolder?
    @ViewState private var deleteFolder: SourceFolder?
    @Environment(\.colorScheme) private var scheme
    let tick = Timer.publish(every: 8, on: .main, in: .common).autoconnect()
    private var unreadCount: Int { reader.data?.articles.filter { $0.seen == 0 }.count ?? 0 }
    private var starCount: Int { reader.data?.articles.filter { $0.starred != 0 }.count ?? 0 }

    private var readingSections: some View {
        Section("阅读") {
            Label("今日更新", systemImage: "sun.max").tag("today")
            Label("未读", systemImage: "circle.dotted")
                .badge(unreadCount).tag("unread")
            Label("待读", systemImage: "text.badge.plus")
                .badge(reader.data?.articles.filter { $0.queued != 0 }.count ?? 0).tag("queued")
            Label("收藏", systemImage: "bookmark")
                .badge(starCount).tag("starred")
            Label("全部文章", systemImage: "tray.full").tag("all")
        }
    }

    private func edit(_ folder: SourceFolder? = nil) {
        editFolder = folder
        reader.error = ""
        editor = .folder
    }

    private func destinations(for feed: Feed) -> some View {
        Group {
            Button("未分类") { Task { _ = await reader.perform(["folder-move",feed.id,""]) } }
            ForEach(reader.data?.folders ?? []) { folder in
                Button(folder.name) { Task { _ = await reader.perform(["folder-move",feed.id,String(folder.id)]) } }
            }
        }
    }

    private func sourceRow(_ feed: Feed) -> some View {
        Label {
            HStack {
                Text(feed.name)
                if feed.failed { Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.orange).accessibilityLabel("更新失败") }
            }
        } icon: { SiteAvatar(feed: feed) }
        .tag(feed.id)
        .contextMenu {
            Menu("移到文件夹") { destinations(for: feed) }
            Button("取消订阅…") { remove = feed }
        }
    }

    private var selectedFolder: SourceFolder? {
        reader.data?.folders.first { $0.tag == selection }
    }

    private var sidebarList: some View {
        List(selection: $selection) {
            readingSections
            Section("订阅来源") {
                ForEach(reader.data?.folders ?? []) { folder in
                    DisclosureGroup {
                        ForEach((reader.data?.feeds ?? []).filter { $0.folder == folder.id }) { feed in sourceRow(feed) }
                    } label: {
                        Label(folder.name, systemImage: "folder").tag(folder.tag)
                    }
                    .tag(folder.tag)
                    .contextMenu {
                        Button("查看文件夹文章") { selection = folder.tag }
                        Button("重命名…") { edit(folder) }
                        Button("删除文件夹…", role: .destructive) { deleteFolder = folder }
                    }
                }
                ForEach((reader.data?.feeds ?? []).filter { $0.folder == nil }) { feed in sourceRow(feed) }
            }
        }
        .listStyle(.sidebar)
        .clipped()
    }

    private var sidebar: some View {
        VStack(spacing: 0) {
            HStack(spacing: 9) {
                Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 34, height: 34)
                Text("Foliofinch").font(.system(size: 20, weight: .medium, design: .serif))
                Spacer()
            }.padding(.horizontal, 16).padding(.vertical, 10)
            sidebarList
            HStack {
                Button { editor = .feed } label: { Label("添加订阅",systemImage:"plus") }.disabled(reader.busy)
                Button { edit() } label: { Label("新建文件夹",systemImage:"folder.badge.plus").labelStyle(.iconOnly) }
                    .help("新建文件夹")
            }.padding()
        }
        .navigationSplitViewColumnWidth(min: 178, ideal: 214)
    }

    private var articleList: some View {
        VStack(alignment:.leading,spacing:0) {
            ScrollView {
                LazyVStack(alignment:.leading,spacing:14) {
                    Text(heading).font(.system(size:32, weight:.semibold, design:.serif))
                    if let folder = selectedFolder {
                        Button("重命名文件夹…") { edit(folder) }
                    }
                    if let sub = reader.data?.feeds.first(where:{ $0.id == selection }) {
                        HStack {
                            Text(sub.url).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                            Spacer()
                            Menu("移到文件夹") { destinations(for: sub) }
                            Button("取消订阅…") { remove = sub }.disabled(reader.busy)
                        }
                    }
                    Divider()
                    if let data = reader.data {
                        let rows = filtered(data)
                        if rows.isEmpty {
                            Text(emptyTitle)
                                .font(.title3).padding(.vertical,8)
                            if selection == "today", !data.articles.isEmpty {
                                HStack {
                                    Text("最近收录").font(.headline)
                                    Spacer()
                                    Button("查看全部") { selection = "all" }.buttonStyle(.link)
                                }
                                ForEach(data.articles.prefix(3)) { a in Story(a:a, reader:reader){ deleteCandidate = a } }
                            }
                        } else {
                            ForEach(rows) { a in Story(a:a, reader:reader){ deleteCandidate = a } }
                        }
                    } else { ProgressView().controlSize(.small) }
                }.frame(maxWidth:850,alignment:.leading).padding(28)
                .frame(maxWidth:.infinity,alignment:.leading)
            }.background(scheme == .dark ? Color(white: 0.105) : Color(red: 0.965, green: 0.966, blue: 0.95))
        }
    }

    @ViewBuilder private var toolbarControls: some View {
        Button { Task { _ = await reader.perform(["refresh"]) } } label: {
            HStack { Image(systemName: "arrow.clockwise"); Text("立即更新订阅") }
        }.disabled(reader.busy).keyboardShortcut("r", modifiers: .command).help("立即更新订阅（⌘R）")
        Button { showSyncDetails.toggle() } label: {
            Label("更新状态", systemImage: statusIcon)
        }.help("更新状态").popover(isPresented: $showSyncDetails) {
            VStack(alignment:.leading, spacing:10) {
                Text("更新状态").font(.headline)
                Text(status)
                if let problem = reader.data?.progress["error"], !problem.isEmpty { Text(problem).foregroundStyle(.secondary) }
                if !reader.error.isEmpty { Text(reader.error).foregroundStyle(.red) }
            }.textSelection(.enabled).padding().frame(width:330)
        }
        Menu {
            Button("更新计划…") { reader.error = ""; editor = .schedule }
            Button("摘要设置…") { reader.error = ""; editor = .provider }
        } label: { Label("设置",systemImage:"slider.horizontal.3") }.help("设置")
    }

    @ViewBuilder private func sheetContent(_ which: Editor) -> some View {
        switch which {
        case .feed: AddFeed(reader: reader)
        case .provider: ProviderForm(reader: reader)
        case .schedule: ScheduleForm(reader: reader, initial: reader.data?.schedule ?? UpdatePlan())
        case .folder: FolderForm(reader: reader, folder: editFolder)
        }
    }

    private var navigation: some View {
        NavigationSplitView { sidebar } detail: { articleList }
            .tint(.teal)
            .toolbar { ToolbarItemGroup { toolbarControls } }
    }

    private var presentedNavigation: some View {
        navigation
        .task { await reader.reload() }
        .task(id: reader.data?.needsSetup) {
            guard Bridge.packaged, let snapshot = reader.data else { return }
            if snapshot.needsSetup { editor = .provider; return }
            let path = Bundle.main.bundleURL.path
            if path.hasPrefix("/Volumes/") || path.contains("/AppTranslocation/") {
                reader.error = "请把 Foliofinch 拖到「应用程序」并重新打开，以启用每日更新。"
                return
            }
            do { _ = try await Bridge.call(["enable-daily"]) }
            catch { reader.error = "启用每日更新失败：" + error.localizedDescription }
        }
        .onReceive(tick) { _ in Task { await reader.reload() } }
        .sheet(item:$editor, content: sheetContent)
    }

    var body: some View {
        presentedNavigation
        .alert("取消订阅“"+(remove?.name ?? "")+"”？",isPresented:Binding(get:{remove != nil},set:{if !$0 {remove=nil}})) {
            Button("保留",role:.cancel) {}
            Button("取消订阅",role:.destructive) {
                guard let feed=remove else { return }
                Task { if await reader.perform(["remove",feed.id]) { selection="all" } }
            }
        } message: { Text("已收录文章仍可在「全部文章」阅读。") }
        .alert("删除文件夹？", isPresented: Binding(get: { deleteFolder != nil },
               set: { if !$0 { deleteFolder = nil } }), presenting: deleteFolder) { folder in
            Button("取消",role:.cancel) {}
            Button("删除文件夹",role:.destructive) {
                Task {
                    if await reader.perform(["folder-delete",String(folder.id)]), selection == folder.tag { selection = "all" }
                }
            }
        } message: { folder in Text("“" + folder.name + "”中的订阅将移至未分类。") }
        .alert("删除这篇文章？", isPresented: Binding(get: { deleteCandidate != nil },
               set: { if !$0 { deleteCandidate = nil } }), presenting: deleteCandidate) { article in
            Button("取消", role: .cancel) {}
            Button("删除", role: .destructive) { Task { await reader.change("delete", article: article) } }
        } message: { article in
            Text("《" + article.title + "》将从所有列表中移除，以后更新也会跳过这篇文章。")
        }
    }
    var emptyTitle: String {
        switch selection {
        case "today": return "今天还没有发现新文章"
        case "unread": return "未读文章已全部读完"
        case "starred": return "还没有收藏的文章"
        case "queued": return "待读列表为空"
        default: return "暂时没有文章"
        }
    }
    var heading: String {
        if let folder = selectedFolder { return folder.name }
        if selection == "queued" { return "待读" }
        if selection == "unread" { return "未读" }
        if selection == "starred" { return "收藏" }
        if selection == "today" { return "今日更新" }
        return reader.data?.feeds.first(where:{$0.id==selection})?.name ?? "全部文章"
    }
    var statusIcon: String {
        if reader.busy { return "clock.arrow.circlepath" }
        if !reader.error.isEmpty || !(reader.data?.progress["error"] ?? "").isEmpty { return "exclamationmark.circle" }
        return "info.circle"
    }
    var status: String {
        if reader.busy { return reader.data?.progress["phase"].flatMap{$0.isEmpty ? nil : $0} ?? "正在处理…" }
        let last = reader.data?.progress["last"] ?? "尚未检查"
        return "上次检查 "+last
    }
    func filtered(_ data:Snapshot) -> [Article] {
        if let folder = selectedFolder {
            let ids = Set(data.feeds.filter { $0.folder == folder.id }.map(\.id))
            return data.articles.filter { !ids.isDisjoint(with: $0.feeds) }
        }
        if selection == "queued" { return data.articles.filter { $0.queued != 0 } }
        if selection == "unread" { return data.articles.filter { $0.seen == 0 } }
        if selection == "starred" { return data.articles.filter { $0.starred != 0 } }
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
                Text((preview.kind == "papers" ? "论文订阅 · " : "网页订阅 · ")+preview.name).font(.headline)
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
                Text("学术主页可订阅论文原摘要，博客支持 RSS 和文章列表。").foregroundStyle(.secondary)
            }
            if !problem.isEmpty { Text(problem).foregroundStyle(.red).textSelection(.enabled) }
            HStack {
                if inspecting || reader.working { ProgressView().controlSize(.small) }
                Spacer()
                Button("关闭") { dismiss() }.disabled(inspecting || reader.working)
                Button(preview == nil ? "识别订阅" : (preview?.kind == "papers" ? "订阅论文" : "订阅此网页")) {
                    Task {
                        if let preview {
                            if await reader.perform([preview.kind == "papers" ? "add-papers" : "add-web",preview.url,pattern]) { dismiss() }
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

private struct FolderForm: View {
    @ObservedObject var reader: Reader
    let folder: SourceFolder?
    @Environment(\.dismiss) var dismiss
    @ViewState private var name = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(folder == nil ? "新建文件夹" : "重命名文件夹").font(.title2.bold())
            TextField("文件夹名称", text: $name).textFieldStyle(.roundedBorder)
            if !reader.error.isEmpty { Text(reader.error).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("取消") { dismiss() }
                Button("保存") {
                    let args = folder.map { ["folder-rename",String($0.id),name] } ?? ["folder-create",name]
                    Task { if await reader.perform(args) { dismiss() } }
                }.keyboardShortcut(.defaultAction).disabled(reader.working || name.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty)
            }
        }.padding(24).frame(width:340).onAppear { name = folder?.name ?? "" }
    }
}

private struct ScheduleForm: View {
    @ObservedObject var reader: Reader
    let initial: UpdatePlan
    @Environment(\.dismiss) var dismiss
    @ViewState private var plan = UpdatePlan()
    @ViewState private var time = Date()
    private let weekdays = ["周一","周二","周三","周四","周五","周六","周日"]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("更新计划").font(.title2.bold())
            Picker("更新频率", selection: $plan.frequency) {
                Text("每小时").tag("hourly")
                Text("每天").tag("daily")
                Text("每周").tag("weekly")
            }.pickerStyle(.segmented)
            if plan.frequency == "weekly" {
                Picker("更新日", selection: $plan.weekday) {
                    ForEach(1...7,id: \.self) { day in Text(weekdays[day-1]).tag(day) }
                }
            }
            if plan.frequency == "hourly" {
                Stepper("每小时第 \(plan.minute) 分钟", value: $plan.minute, in: 0...59)
            } else {
                DatePicker("本地时间", selection: $time, displayedComponents: .hourAndMinute)
            }
            if !reader.error.isEmpty { Text(reader.error).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("取消") { dismiss() }
                Button("保存") {
                    var value = plan
                    if plan.frequency != "hourly" {
                        value.hour = Calendar.current.component(.hour,from:time)
                        value.minute = Calendar.current.component(.minute,from:time)
                    }
                    Task { if await reader.perform(["schedule"], input:try? JSONEncoder().encode(value)) { dismiss() } }
                }.keyboardShortcut(.defaultAction).disabled(reader.busy)
            }
        }.padding(24).frame(width:380).onAppear {
            plan = initial
            time = Calendar.current.date(bySettingHour:initial.hour, minute:initial.minute, second:0, of:Date()) ?? Date()
        }
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
            } else { Text("首次使用会下载约 1.12 GB 的 Qwen 模型，之后在 Mac 上生成中文摘要，无需 API 密钥。").foregroundStyle(.secondary) }
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

@main struct Foliofinch: App {
    var body: some Scene {
        Window("Foliofinch",id:"main") { Home().frame(minWidth:740,minHeight:520).environment(\.locale,Locale(identifier:"zh_CN")) }
            .defaultSize(width:980,height:720)
    }
}
