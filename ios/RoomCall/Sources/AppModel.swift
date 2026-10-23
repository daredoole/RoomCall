import Foundation
import WidgetKit

enum Tab: Hashable { case talk, message, tasks, history, settings }

@MainActor
final class AppModel: ObservableObject {
    @Published var connected = false
    @Published var busy = false
    @Published var error: String?
    @Published var notice: String?
    @Published var tab: Tab = .talk
    @Published var overview = Overview()
    @Published var tasks: [RoomTask] = []
    @Published var history: [CallSummary] = []
    @Published var target: Target = .room(.everywhere)
    @Published var urgent = false
    @Published var doorbell: Doorbell?  // set while someone is at the door
    @Published var serverDraft = ""
    /// The room this phone is usually in: replies and hands-free talk-back come back here.
    @Published var myRoom = UserDefaults.standard.string(forKey: "myRoom") ?? "" {
        didSet { UserDefaults.standard.set(myRoom, forKey: "myRoom") }
    }
    private(set) var service: (any RoomCallService)?
    private var server: URL?
    private var seenRing: Date?
    let login = OAuthLogin()
    let talk = TalkController()

    var rooms: [Room] { [.everywhere] + (overview.rooms ?? []) }
    var people: [Person] { overview.people ?? [] }
    var openTasks: [RoomTask] { tasks.filter(\.isOpen) }
    var serverName: String { server?.host() ?? "" }

    init() {
        #if DEBUG
        if Demo.enabled {
            service = DemoService()
            server = URL(string: "https://homeassistant.local:8123")
            connected = true
            if let id = Demo.value("-RoomCallDemoRoom"), let room = Demo.rooms.first(where: { $0.id == id }) {
                target = .room(room)
            }
            return
        }
        #endif
        do {
            if let credentials = try SecureStore.load() {
                service = HAClient(credentials)
                server = credentials.server
                connected = true
            }
        } catch { self.error = error.localizedDescription }
    }

    // MARK: Connection

    func signIn() async {
        busy = true
        defer { busy = false }
        do {
            let credentials = try await login.signIn(server: ServerAddress.parse(serverDraft))
            service = HAClient(credentials)
            server = credentials.server
            connected = true
            await refresh()
        } catch { self.error = error.localizedDescription }
    }

    func signOut() async {
        do {
            // Revoke this refresh token before dropping local credentials.
            if let credentials = try SecureStore.load() {
                _ = try? await HAClient.token(server: credentials.server,
                    values: ["action": "revoke", "token": credentials.refreshToken])
            }
            try SecureStore.delete()
        } catch { self.error = error.localizedDescription }
        service = nil
        server = nil
        connected = false
        overview = Overview()
        tasks = []
        history = []
        target = .room(.everywhere)
        RoomCache.save([])
        TaskCache.save([])
        TaskActivities.sync([])
    }

    /// `roomcall://connect?server=…` from the card's QR code or a shared link.
    func handle(_ url: URL) {
        guard url.scheme == "roomcall", let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
        else { return }
        let value = { (name: String) in parts.queryItems?.first { $0.name == name }?.value }
        switch url.host() {
        case "connect":
            if let server = value("server"), !connected {
                serverDraft = server
                Task { await signIn() }
            }
        case "open":
            if let id = value("room"), let room = rooms.first(where: { $0.id == id }) {
                target = .room(room)
                tab = .talk
            }
        case "todo": tab = .tasks
        case "door": if let bell = overview.doorbell { doorbell = bell }
        default: break
        }
    }

    func consumePendingRoom() {
        if let id = RoomCache.defaults.string(forKey: "pendingRoom"),
           let room = rooms.first(where: { $0.id == id }) {
            target = .room(room)
            tab = .talk
            RoomCache.defaults.removeObject(forKey: "pendingRoom")
        }
    }

    // MARK: Data

    func refresh() async {
        guard let service else { return }
        do {
            let overview = try await service.overview()
            guard overview.configured != false else { throw NativeFailure.backend(L("ios_setup_required")) }
            self.overview = overview
            RoomCache.save(rooms)
            RoomCache.defaults.set(overview.dnd_entity, forKey: "dndEntity")
            if case .room(let room) = target, !rooms.contains(room) { target = .room(.everywhere) }
            consumePendingRoom()
            await refreshTasks()
            history = try await service.history()
            await checkDoorbell()
        } catch { self.error = error.localizedDescription }
    }

    func refreshTasks() async {
        guard let service, let tasks = try? await service.tasks("list", id: nil) else { return }
        self.tasks = tasks
        TaskCache.save(tasks)
        TaskActivities.sync(openTasks)
        WidgetCenter.shared.reloadAllTimelines()
    }

    /// While the app is open: tasks and the doorbell every few seconds, everything else less often.
    func poll() async {
        var tick = 0
        while !Task.isCancelled, connected {
            try? await Task.sleep(nanoseconds: 5_000_000_000)
            tick += 1
            await checkDoorbell()
            if tick % 2 == 0 { await refreshTasks() }
            if tick % 6 == 0 { await refresh() }
        }
    }

    func checkDoorbell() async {
        guard let service, let bell = overview.doorbell,
              let rang = try? await service.doorbellRang(bell.entity) else { return }
        // Only a recent ring that this phone hasn't shown yet is someone at the door now.
        if rang != seenRing, Date().timeIntervalSince(rang) < 120 {
            seenRing = rang
            doorbell = bell
        }
    }

    // MARK: Actions

    func run(_ action: (any RoomCallService) async throws -> Void, done: String? = nil) async {
        guard let service else { return }
        do {
            try await action(service)
            if let done { notice = done }
        } catch { self.error = error.localizedDescription }
    }

    func send(_ message: String) async -> Bool {
        let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return false }
        guard let service else { return false }
        do {
            try await service.send(target, message: text, urgent: urgent)
            notice = L("ios_queued")
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }

    func complete(_ task: RoomTask) async {
        await run({ _ = try await $0.tasks("complete", id: task.id) }, done: L("ios_task_done"))
        await refreshTasks()
    }

    func snooze(_ task: RoomTask) async {
        await run({ _ = try await $0.tasks("snooze", id: task.id) }, done: L("ios_task_snoozed"))
        await refreshTasks()
    }

    func toggleDoNotDisturb() async {
        guard let entity = overview.dnd_entity else { return }
        let on = !(overview.dnd ?? false)
        await run { try await $0.setDoNotDisturb(entity, on: on) }
        overview.dnd = on
    }

    func reply(to call: CallSummary) {
        guard let from = call.from, let room = rooms.first(where: { $0.id == from }) else { return }
        target = .room(room)
        tab = .talk
    }

    func roomName(_ id: String?) -> String {
        guard let id else { return L("ios_automation") }
        return rooms.first { $0.id == id }?.name ?? id
    }
}
