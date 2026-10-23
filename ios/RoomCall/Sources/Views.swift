import AVFoundation
import SwiftUI
import VisionKit

// MARK: Root

struct RootView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        Group {
            if model.connected { MainTabs(model: model, talk: model.talk) } else { SignInView(model: model) }
        }
        .overlay(alignment: .bottom) { Toast(model: model) }
        .sheet(item: Binding(get: { model.doorbell.map(DoorbellItem.init) },
                             set: { if $0 == nil { model.doorbell = nil } })) { item in
            DoorbellSheet(model: model, bell: item.bell)
        }
        .alert("ios_error", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
            Button("ios_ok") { model.error = nil }
        } message: { Text(verbatim: model.error ?? "") }
    }
}

private struct DoorbellItem: Identifiable {
    let bell: Doorbell
    var id: String { bell.entity }
}

struct MainTabs: View {
    @ObservedObject var model: AppModel
    @ObservedObject var talk: TalkController

    var body: some View {
        TabView(selection: $model.tab) {
            TalkScreen(model: model, talk: talk)
                .tabItem { Label("ios_tab_talk", systemImage: "mic.fill") }.tag(Tab.talk)
            MessageScreen(model: model)
                .tabItem { Label("ios_tab_message", systemImage: "text.bubble.fill") }.tag(Tab.message)
            TasksScreen(model: model)
                .tabItem { Label("ios_tab_tasks", systemImage: "checklist") }.tag(Tab.tasks)
                .badge(model.openTasks.count)
            HistoryScreen(model: model)
                .tabItem { Label("ios_tab_history", systemImage: "clock.fill") }.tag(Tab.history)
            SettingsScreen(model: model)
                .tabItem { Label("ios_tab_settings", systemImage: "gearshape.fill") }.tag(Tab.settings)
        }
        .task { await model.refresh() }
        .task(id: model.connected) { await model.poll() }
    }
}

// MARK: Talk

struct TalkScreen: View {
    @ObservedObject var model: AppModel
    @ObservedObject var talk: TalkController

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    ForEach(model.openTasks) { task in TaskBanner(model: model, task: task) }
                    TargetPicker(model: model)
                    VStack(spacing: 14) {
                        Text(verbatim: String(format: L("ios_to"), model.target.name))
                            .font(.headline)
                        TalkButton(talk: talk, urgent: model.urgent) {
                            guard let service = model.service else { return }
                            talk.press(service, target: model.target, urgent: model.urgent,
                                       from: model.myRoom.isEmpty ? nil : model.myRoom)
                        } release: { talk.release() }
                        Text(verbatim: talk.status)
                            .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
                            .accessibilityIdentifier("talk-status")
                    }
                    .frame(maxWidth: .infinity)
                }
                .padding()
            }
            .navigationTitle("RoomCall")
            .toolbar {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Toggle(isOn: $model.urgent) { Label("ios_urgent", systemImage: "exclamationmark.triangle.fill") }
                        .toggleStyle(.button).tint(.red)
                        .accessibilityIdentifier("urgent")
                    if model.overview.dnd_entity != nil {
                        Button { Task { await model.toggleDoNotDisturb() } } label: {
                            Label("ios_dnd", systemImage: model.overview.dnd == true ? "bell.slash.fill" : "bell.fill")
                        }
                        .tint(model.overview.dnd == true ? .orange : .accentColor)
                        .accessibilityValue(Text(LocalizedStringKey(model.overview.dnd == true ? "ios_on" : "ios_off")))
                    }
                }
            }
            .refreshable { await model.refresh() }
        }
    }
}

struct TargetPicker: View {
    @ObservedObject var model: AppModel
    private let columns = [GridItem(.adaptive(minimum: 140), spacing: 10)]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("ios_choose_room").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
            LazyVGrid(columns: columns, spacing: 10) {
                ForEach(model.rooms) { room in
                    choice(room.name, icon: room.id == "all" ? "house.fill" : "speaker.wave.2.fill",
                           selected: model.target == .room(room)) { model.target = .room(room) }
                }
            }
            if !model.people.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(model.people) { person in
                            choice(person.name, icon: "person.fill", selected: model.target == .person(person)) {
                                model.target = .person(person)
                            }
                        }
                    }
                }
            }
        }
        .disabled(model.talk.active)
    }

    private func choice(_ title: String, icon: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: icon)
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                .lineLimit(2)
        }
        .buttonStyle(.bordered)
        .tint(selected ? .accentColor : .secondary)
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier(title)
    }
}

/// Press and hold to talk; VoiceOver users double-tap to start and again to stop.
struct TalkButton: View {
    @ObservedObject var talk: TalkController
    let urgent: Bool
    let press: () -> Void
    let release: () -> Void
    @State private var holding = false

    var body: some View {
        let live = talk.phase == .live
        ZStack {
            Circle()
                .fill(live || urgent ? Color.red : Color.accentColor)
                .shadow(color: (live ? Color.red : Color.accentColor).opacity(0.35), radius: live ? 28 : 16)
            VStack(spacing: 8) {
                Image(systemName: "mic.fill").font(.system(size: 44, weight: .semibold))
                Text(LocalizedStringKey(live ? "ios_release_to_send" : "ios_hold_to_talk")).font(.headline)
            }
            .foregroundStyle(.white)
        }
        .frame(width: 200, height: 200)
        .scaleEffect(live ? 0.96 : 1)
        .animation(.easeOut(duration: 0.15), value: live)
        .opacity(talk.phase == .sending ? 0.6 : 1)
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    if !holding { holding = true; press() }
                }
                .onEnded { _ in
                    holding = false
                    release()
                }
        )
        .accessibilityElement()
        .accessibilityLabel(Text(LocalizedStringKey(live ? "ios_release_to_send" : "ios_hold_to_talk")))
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { talk.active ? release() : press() }
        .accessibilityIdentifier("talk-button")
    }
}

struct TaskBanner: View {
    @ObservedObject var model: AppModel
    let task: RoomTask

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "checklist").font(.title2).foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: task.name).font(.headline)
                if let opened = Timestamp.parse(task.opened) {
                    Text(opened, style: .relative).font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 8)
            Button { Task { await model.complete(task) } } label: {
                Label(task.task, systemImage: "checkmark").lineLimit(2)
            }
            .buttonStyle(.borderedProminent).tint(.green)
            .accessibilityIdentifier("done-\(task.id)")
        }
        .padding(12)
        .background(.orange.opacity(0.14), in: RoundedRectangle(cornerRadius: 14))
    }
}

// MARK: Message

struct MessageScreen: View {
    @ObservedObject var model: AppModel
    @State private var mode = 0
    @State private var text = ""
    @State private var satellites: [Satellite] = []
    @State private var chosen: Set<String> = []
    @State private var answers: [String]?
    @State private var asking = false

    var body: some View {
        NavigationStack {
            Form {
                Picker("ios_mode", selection: $mode) {
                    Text("ios_type").tag(0)
                    Text("ios_ask").tag(1)
                }
                .pickerStyle(.segmented)
                .listRowBackground(Color.clear)
                if mode == 0 {
                    Section {
                        Picker("ios_send_to", selection: $model.target) {
                            ForEach(model.rooms) { Text(verbatim: $0.name).tag(Target.room($0)) }
                            ForEach(model.people) { Text(verbatim: $0.name).tag(Target.person($0)) }
                        }
                        TextField("ios_message", text: $text, axis: .vertical)
                            .lineLimit(3...8)
                            .accessibilityIdentifier("message-text")
                        Toggle("ios_urgent", isOn: $model.urgent)
                    } footer: { Text("ios_type_hint") }
                    Button {
                        Task { if await model.send(text) { text = "" } }
                    } label: { Label("ios_send", systemImage: "paperplane.fill").frame(maxWidth: .infinity) }
                    .buttonStyle(.borderedProminent)
                    .disabled(text.trimmingCharacters(in: .whitespaces).isEmpty)
                    .listRowBackground(Color.clear)
                } else {
                    Section {
                        if satellites.isEmpty {
                            Text("ios_no_satellites").foregroundStyle(.secondary)
                        }
                        ForEach(satellites) { satellite in
                            Toggle(isOn: Binding(get: { chosen.contains(satellite.id) },
                                                 set: { if $0 { chosen.insert(satellite.id) } else { chosen.remove(satellite.id) } })) {
                                Text(verbatim: satellite.name)
                            }
                        }
                        TextField("ios_question", text: $text, axis: .vertical).lineLimit(2...5)
                    } footer: { Text("ios_ask_hint") }
                    Button {
                        Task {
                            asking = true
                            defer { asking = false }
                            answers = nil
                            await model.run { answers = try await $0.ask(Array(chosen), question: text) }
                        }
                    } label: {
                        Label(LocalizedStringKey(asking ? "ios_listening" : "ios_ask"),
                              systemImage: "questionmark.bubble.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(chosen.isEmpty || text.trimmingCharacters(in: .whitespaces).isEmpty || asking)
                    .listRowBackground(Color.clear)
                    if let answers {
                        Section("ios_answers") {
                            if answers.isEmpty { Text("ios_no_answer").foregroundStyle(.secondary) }
                            ForEach(answers, id: \.self) { Text(verbatim: "“\($0)”") }
                        }
                    }
                }
            }
            .navigationTitle("ios_tab_message")
            .task(id: mode) {
                guard mode == 1, satellites.isEmpty else { return }
                await model.run { satellites = try await $0.satellites() }
                chosen = Set(satellites.prefix(1).map(\.id))
            }
        }
    }
}

// MARK: To do

struct TasksScreen: View {
    @ObservedObject var model: AppModel

    var body: some View {
        NavigationStack {
            List {
                Section {
                    if model.openTasks.isEmpty {
                        Label("ios_nothing_to_do", systemImage: "checkmark.seal.fill").foregroundStyle(.secondary)
                    }
                    ForEach(model.openTasks) { task in
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Text(verbatim: task.name).font(.headline)
                                Spacer()
                                if let opened = Timestamp.parse(task.opened) {
                                    Text(opened, style: .relative).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            HStack {
                                Button { Task { await model.complete(task) } } label: {
                                    Label(task.task, systemImage: "checkmark").frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.borderedProminent).tint(.green)
                                Button { Task { await model.snooze(task) } } label: {
                                    Label("ios_remind_later", systemImage: "alarm")
                                        .labelStyle(.iconOnly)
                                }
                                .buttonStyle(.bordered)
                                .accessibilityLabel(Text("ios_remind_later"))
                            }
                        }
                        .padding(.vertical, 4)
                    }
                } header: { Text("ios_open_tasks") } footer: { Text("ios_tasks_hint") }
                let done = model.tasks.filter { $0.state == "done" }
                if !done.isEmpty {
                    Section("ios_done_recently") {
                        ForEach(done) { task in
                            VStack(alignment: .leading) {
                                Text(verbatim: task.task)
                                Text(verbatim: [task.name, task.done_by].compactMap { $0 }.joined(separator: " · "))
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .navigationTitle("ios_tab_tasks")
            .refreshable { await model.refreshTasks() }
        }
    }
}

// MARK: History

@MainActor
final class ClipPlayer: ObservableObject {
    @Published var playing: String?
    private var player: AVAudioPlayer?

    func play(_ call: CallSummary, with service: (any RoomCallService)?) async throws {
        guard let service else { return }
        let data = try await service.clip(call.id)
        try AVAudioSession.sharedInstance().setCategory(.playback)
        player = try AVAudioPlayer(data: data)
        playing = call.id
        player?.play()
    }
}

struct HistoryScreen: View {
    @ObservedObject var model: AppModel
    @StateObject private var player = ClipPlayer()

    var body: some View {
        NavigationStack {
            List {
                if model.history.isEmpty {
                    Label("ios_no_history", systemImage: "clock").foregroundStyle(.secondary)
                }
                ForEach(model.history) { call in
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: !call.ok ? "exclamationmark.circle"
                              : call.priority == "urgent" ? "exclamationmark.triangle.fill" : "person.wave.2.fill")
                            .foregroundStyle(!call.ok || call.priority == "urgent" ? .red : .accentColor)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(verbatim: "\(model.roomName(call.from)) → \(call.target.split(separator: ",").map { model.roomName(String($0)) }.joined(separator: ", "))")
                                .font(.subheadline.weight(.semibold))
                            if let text = call.text { Text(verbatim: "“\(text)”").font(.callout) }
                            if let at = Timestamp.parse(call.at) {
                                Text(at, style: .relative).font(.caption).foregroundStyle(.secondary)
                            }
                            HStack {
                                if call.has_audio {
                                    Button { Task { await model.run { _ in try await player.play(call, with: model.service) } } }
                                        label: { Label("ios_replay", systemImage: "play.circle") }
                                }
                                if call.requires_ack == true {
                                    Button { Task { await model.run({ try await $0.acknowledge(call.id) }, done: L("ios_acknowledged")) } }
                                        label: { Label("ios_acknowledge", systemImage: "hand.thumbsup") }
                                }
                                if call.from.map({ id in model.rooms.contains { $0.id == id } }) == true {
                                    Button { model.reply(to: call) } label: { Label("ios_reply", systemImage: "arrowshape.turn.up.left") }
                                }
                            }
                            .buttonStyle(.borderless).font(.callout)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
            .navigationTitle("ios_tab_history")
            .refreshable { await model.refresh() }
        }
    }
}

// MARK: Settings

struct SettingsScreen: View {
    @ObservedObject var model: AppModel

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    LabeledContent("ios_server") { Text(verbatim: model.serverName) }
                    Picker("ios_my_room", selection: $model.myRoom) {
                        Text("ios_none").tag("")
                        ForEach(model.rooms.dropFirst()) { Text(verbatim: $0.name).tag($0.id) }
                    }
                } footer: { Text("ios_my_room_hint") }
                if model.overview.dnd_entity != nil {
                    Section {
                        Toggle("ios_dnd", isOn: Binding(get: { model.overview.dnd == true },
                                                        set: { _ in Task { await model.toggleDoNotDisturb() } }))
                    } footer: { Text("ios_dnd_hint") }
                }
                Section {
                    Link(destination: URL(string: "homeassistant://navigate/")!) {
                        Label("ios_open_ha", systemImage: "house")
                    }
                } footer: { Text("ios_notifications_hint") }
                Section {
                    LabeledContent("ios_version") {
                        Text(verbatim: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")
                    }
                    Button("ios_sign_out", role: .destructive) {
                        model.talk.release()
                        Task { await model.signOut() }
                    }
                }
            }
            .navigationTitle("ios_tab_settings")
        }
    }
}

// MARK: Sign in

struct SignInView: View {
    @ObservedObject var model: AppModel
    @State private var scanning = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(alignment: .leading, spacing: 8) {
                        Image(systemName: "waveform.circle.fill").font(.system(size: 48)).foregroundStyle(.tint)
                        Text("ios_welcome").font(.title2.bold())
                        Text("ios_sign_in_description").foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 8)
                }
                Section {
                    Button { scanning = true } label: { Label("ios_scan_qr", systemImage: "qrcode.viewfinder") }
                    Button {
                        if let link = UIPasteboard.general.url ?? UIPasteboard.general.string.flatMap { URL(string: $0) } {
                            if link.scheme == "roomcall" { model.handle(link) } else { model.serverDraft = link.absoluteString }
                        }
                    } label: { Label("ios_paste_link", systemImage: "doc.on.clipboard") }
                } footer: { Text("ios_scan_hint") }
                Section {
                    TextField("ios_server_url", text: $model.serverDraft)
                        .keyboardType(.URL).textInputAutocapitalization(.never)
                        .autocorrectionDisabled().accessibilityIdentifier("server-url")
                    Button("ios_sign_in") { Task { await model.signIn() } }
                        .disabled(model.busy || (try? ServerAddress.parse(model.serverDraft)) == nil)
                        .accessibilityIdentifier("sign-in")
                    if model.busy { ProgressView() }
                }
            }
            .navigationTitle("RoomCall")
            .sheet(isPresented: $scanning) {
                QRScanner { code in
                    scanning = false
                    if let url = URL(string: code) { model.handle(url) }
                }
            }
        }
    }
}

/// Scans the QR code shown by the card's Settings → iPhone app.
struct QRScanner: View {
    let found: (String) -> Void

    var body: some View {
        if DataScannerViewController.isSupported && DataScannerViewController.isAvailable {
            Scanner(found: found).ignoresSafeArea()
        } else {
            ContentUnavailableView("ios_scanner_unavailable", systemImage: "camera.fill",
                                   description: Text("ios_scanner_unavailable_hint"))
        }
    }

    private struct Scanner: UIViewControllerRepresentable {
        let found: (String) -> Void

        func makeUIViewController(context: Context) -> DataScannerViewController {
            let scanner = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.qr])],
                                                    isHighlightingEnabled: true)
            scanner.delegate = context.coordinator
            try? scanner.startScanning()
            return scanner
        }
        func updateUIViewController(_ controller: DataScannerViewController, context: Context) {}
        func makeCoordinator() -> Coordinator { Coordinator(found: found) }

        final class Coordinator: NSObject, DataScannerViewControllerDelegate {
            let found: (String) -> Void
            init(found: @escaping (String) -> Void) { self.found = found }
            func dataScanner(_ scanner: DataScannerViewController, didAdd items: [RecognizedItem],
                             allItems: [RecognizedItem]) {
                for case .barcode(let code) in items {
                    if let value = code.payloadStringValue, value.hasPrefix("roomcall://") {
                        scanner.stopScanning()
                        found(value)
                        return
                    }
                }
            }
        }
    }
}

// MARK: Doorbell and notices

struct DoorbellSheet: View {
    @ObservedObject var model: AppModel
    let bell: Doorbell
    @State private var image: UIImage?

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                Group {
                    if let image {
                        Image(uiImage: image).resizable().scaledToFit()
                    } else {
                        Rectangle().fill(.quaternary).aspectRatio(16 / 9, contentMode: .fit)
                            .overlay { ProgressView() }
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 16))
                .accessibilityLabel(Text("ios_camera"))
                Text(verbatim: String(format: L("ios_someone_at"), bell.name))
                    .font(.title2.bold()).multilineTextAlignment(.center)
                Link(destination: URL(string: "homeassistant://navigate/")!) {
                    Label("ios_answer_in_ha", systemImage: "video.fill").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                Text("ios_answer_hint").font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                Spacer()
            }
            .padding()
            .navigationTitle(Text(verbatim: bell.name))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("ios_close") { model.doorbell = nil } }
            .task {
                // A live-ish view from snapshots: one per second while the sheet is open.
                guard let camera = bell.camera, let service = model.service else { return }
                while !Task.isCancelled {
                    if let data = try? await service.snapshot(camera) { image = UIImage(data: data) }
                    try? await Task.sleep(nanoseconds: 1_000_000_000)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}

struct Toast: View {
    @ObservedObject var model: AppModel

    var body: some View {
        if let notice = model.notice {
            Text(verbatim: notice)
                .font(.callout.weight(.medium))
                .padding(.horizontal, 16).padding(.vertical, 10)
                .background(.thinMaterial, in: Capsule())
                .padding(.bottom, 64)
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .task(id: notice) {
                    try? await Task.sleep(nanoseconds: 2_500_000_000)
                    withAnimation { model.notice = nil }
                }
                .accessibilityAddTraits(.isStaticText)
        }
    }
}
