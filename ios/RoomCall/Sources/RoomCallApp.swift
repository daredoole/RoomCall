import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    @Published var rooms: [Room] = []
    @Published var selected: Room?
    @Published var connected = false
    @Published var busy = false
    @Published var error: String?
    private(set) var client: HAClient?
    let login = OAuthLogin()

    init() {
        do {
            if let credentials = try SecureStore.load() {
                client = HAClient(credentials)
                connected = true
            }
        } catch { self.error = error.localizedDescription }
    }
    func signIn(_ text: String) async {
        busy = true
        defer { busy = false }
        do {
            client = HAClient(try await login.signIn(server: ServerAddress.parse(text)))
            connected = true
            await refresh()
        } catch { self.error = error.localizedDescription }
    }
    func refresh() async {
        guard let client else { return }
        do {
            let response = try await client.get("api/roomcall/rooms")
            guard response["configured"] as? Bool != false else {
                throw NativeFailure.backend(L("ios_setup_required"))
            }
            let data = try JSONSerialization.data(withJSONObject: response["rooms"] ?? [])
            rooms = [.everywhere] + (try JSONDecoder().decode([Room].self, from: data))
            RoomCache.save(rooms)
            if let selected, !rooms.contains(selected) { self.selected = nil }
            consumePendingRoom()
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
            client = nil
            connected = false
            selected = nil
            rooms = []
            RoomCache.save([])
        } catch { self.error = error.localizedDescription }
    }
    func open(_ url: URL) {
        guard url.scheme == "roomcall", url.host == "open",
              let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let id = parts.queryItems?.first(where: { $0.name == "room" })?.value else { return }
        selected = rooms.first { $0.id == id }
    }
    func consumePendingRoom() {
        if let id = RoomCache.defaults.string(forKey: "pendingRoom"),
           let room = rooms.first(where: { $0.id == id }) {
            selected = room
            RoomCache.defaults.removeObject(forKey: "pendingRoom")
        }
    }
}

@main
struct RoomCallApp: App {
    @StateObject private var model = AppModel()
    @StateObject private var ptt = PTTCoordinator()
    var body: some Scene {
        WindowGroup {
            ContentView(model: model, ptt: ptt)
                .onOpenURL { url in
                    ptt.leave()
                    model.open(url)
                }
                .task { await model.refresh() }
                .onReceive(NotificationCenter.default.publisher(for: Notification.Name("roomcall.openRoom"))) { _ in
                    ptt.leave()
                    model.consumePendingRoom()
                }
        }
    }
}

struct ContentView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var ptt: PTTCoordinator
    @State private var server = ""

    var body: some View {
        NavigationStack {
            Group {
                if model.connected {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 24) {
                            Text("ios_choose_room").font(.headline)
                            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150))], spacing: 12) {
                                ForEach(model.rooms) { room in
                                    Button {
                                        model.selected = room
                                    } label: {
                                        Label(room.name, systemImage: room.id == "all" ? "house.fill" : "speaker.wave.2.fill")
                                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                                    }
                                    .buttonStyle(.bordered)
                                    .tint(model.selected == room ? .accentColor : .secondary)
                                    .accessibilityAddTraits(model.selected == room ? .isSelected : [])
                                    .disabled(ptt.joined)
                                }
                            }
                            VStack(spacing: 20) {
                                if let name = ptt.joined ? ptt.activeRoomName : model.selected?.name { Text(name).font(.title2.bold()) }
                                if ptt.joined {
                                    Button {
                                        if ptt.requested { ptt.stop() } else { ptt.begin() }
                                    } label: {
                                        Label(LocalizedStringKey(ptt.requested ? "ios_stop_talking" : "ios_talk"), systemImage: "mic.fill")
                                            .font(.title3.bold()).frame(maxWidth: .infinity, minHeight: 100)
                                    }
                                    .buttonStyle(.borderedProminent)
                                    .tint(ptt.transmitting ? .red : .accentColor)
                                    .accessibilityHint(Text("ios_talk_hint"))
                                    .disabled(ptt.finishing)
                                    Button("ios_leave", role: .cancel) { ptt.leave() }
                                } else {
                                    Button("ios_join") {
                                        guard let room = model.selected, let client = model.client else { return }
                                        Task { await ptt.join(room: room, client: client) }
                                    }
                                    .buttonStyle(.borderedProminent)
                                    .disabled(model.selected == nil || ptt.finishing)
                                }
                                Text(ptt.status).font(.callout).foregroundStyle(.secondary)
                                    .accessibilityIdentifier("ptt-status")
                                    .accessibilityAddTraits(.updatesFrequently)
                                Text("ios_prototype_limit").font(.footnote).foregroundStyle(.secondary)
                            }.frame(maxWidth: .infinity)
                        }.padding()
                    }
                    .refreshable { await model.refresh() }
                } else {
                    Form {
                        Section {
                            Text("ios_welcome").font(.title2.bold())
                            Text("ios_sign_in_description").foregroundStyle(.secondary)
                            TextField("ios_server_url", text: $server)
                                .keyboardType(.URL).textInputAutocapitalization(.never)
                                .autocorrectionDisabled().accessibilityIdentifier("server-url")
                            Button("ios_sign_in") { Task { await model.signIn(server) } }
                                .disabled(model.busy || (try? ServerAddress.parse(server)) == nil)
                                .accessibilityIdentifier("sign-in")
                            if model.busy { ProgressView() }
                        }
                    }
                }
            }
            .navigationTitle("RoomCall")
            .toolbar {
                if model.connected {
                    Button("ios_sign_out", role: .destructive) {
                        ptt.leave()
                        Task { await model.signOut() }
                    }
                }
            }
            .alert("ios_error", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
                Button("ios_ok") { model.error = nil }
            } message: { Text(model.error ?? "") }
        }
    }
}
