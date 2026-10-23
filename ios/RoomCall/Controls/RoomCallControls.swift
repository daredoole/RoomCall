import ActivityKit
import AppIntents
import SwiftUI
import WidgetKit

@main
struct RoomCallWidgets: WidgetBundle {
    var body: some Widget {
        RoomCallControl()
        RoomsWidget()
        TasksWidget()
        TaskLiveActivity()
    }
}

struct RoomCallControl: ControlWidget {
    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: "com.daredoole.RoomCall.open") {
            ControlWidgetButton(action: OpenRoomCallIntent()) {
                Label("ios_open", systemImage: "mic.fill")
            }
        }
        .displayName("RoomCall")
        .description("ios_open_description")
    }
}

// MARK: Home-screen widgets

/// Rooms and open tasks the app last saw. When re-signing removed the shared app group the
/// widgets show a plain "Open RoomCall" instead of stale or empty buttons.
struct Snapshot: TimelineEntry {
    let date: Date
    let rooms: [Room]
    let tasks: [RoomTask]
}

struct SnapshotProvider: TimelineProvider {
    func placeholder(in context: Context) -> Snapshot {
        Snapshot(date: .now, rooms: [Room(area_id: "kitchen", name: "Kitchen")], tasks: [])
    }
    func getSnapshot(in context: Context, completion: @escaping (Snapshot) -> Void) {
        completion(current())
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<Snapshot>) -> Void) {
        completion(Timeline(entries: [current()], policy: .after(.now.addingTimeInterval(900))))
    }
    private func current() -> Snapshot {
        Snapshot(date: .now, rooms: RoomCache.load(), tasks: TaskCache.load().filter(\.isOpen))
    }
}

struct RoomsWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "com.daredoole.RoomCall.rooms", provider: SnapshotProvider()) { entry in
            RoomsWidgetView(entry: entry).containerBackground(.fill.tertiary, for: .widget)
                .tint(ThemeSettings.accent)
        }
        .configurationDisplayName("ios_widget_rooms")
        .description("ios_widget_rooms_description")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

struct RoomsWidgetView: View {
    let entry: Snapshot
    @Environment(\.widgetFamily) private var family

    var body: some View {
        let rooms = Array(entry.rooms.prefix(family == .systemSmall ? 2 : 4))
        if rooms.isEmpty {
            OpenAppView().widgetURL(URL(string: "roomcall://open"))
        } else {
            VStack(alignment: .leading, spacing: 6) {
                Label("RoomCall", systemImage: "waveform").font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6),
                                         count: family == .systemSmall ? 1 : 2), spacing: 6) {
                    ForEach(rooms) { room in
                        Button(intent: OpenRoomCallIntent(target: RoomEntity(id: room.id, name: room.name))) {
                            Label(room.name, systemImage: room.id == "all" ? "house.fill" : "mic.fill")
                                .font(.caption.weight(.medium))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .lineLimit(1)
                        }
                        .tint(ThemeSettings.accent)
                    }
                }
            }
        }
    }
}

struct TasksWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "com.daredoole.RoomCall.tasks", provider: SnapshotProvider()) { entry in
            TasksWidgetView(entry: entry)
                .containerBackground(.fill.tertiary, for: .widget)
                .tint(ThemeSettings.accent)
                .widgetURL(URL(string: "roomcall://todo"))
        }
        .configurationDisplayName("ios_widget_tasks")
        .description("ios_widget_tasks_description")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

struct TasksWidgetView: View {
    let entry: Snapshot

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("ios_tab_tasks", systemImage: "checklist").font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            if entry.tasks.isEmpty {
                Spacer()
                Label("ios_nothing_to_do", systemImage: "checkmark.seal.fill")
                    .font(.callout).foregroundStyle(.green)
                Spacer()
            } else {
                ForEach(entry.tasks.prefix(3)) { task in
                    VStack(alignment: .leading, spacing: 0) {
                        Text(verbatim: task.name).font(.callout.weight(.semibold)).lineLimit(1)
                        Text(verbatim: task.task).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct OpenAppView: View {
    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: "waveform.circle.fill").font(.largeTitle).foregroundStyle(.tint)
            Text("ios_open").font(.caption.weight(.medium))
        }
    }
}

// MARK: Live Activity

struct TaskLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: TaskActivityAttributes.self) { context in
            HStack(spacing: 12) {
                Image(systemName: "checklist").font(.title2).foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 2) {
                    Text(verbatim: context.attributes.name).font(.headline)
                    if let opened = context.state.opened {
                        Text(opened, style: .relative).font(.caption).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                DoneButton(attributes: context.attributes)
            }
            .padding()
            .activityBackgroundTint(Color(.systemBackground).opacity(0.85))
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Image(systemName: "checklist").foregroundStyle(.orange)
                }
                DynamicIslandExpandedRegion(.center) {
                    Text(verbatim: context.attributes.name).font(.headline)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    DoneButton(attributes: context.attributes)
                }
            } compactLeading: {
                Image(systemName: "checklist").foregroundStyle(.orange)
            } compactTrailing: {
                Text(verbatim: context.attributes.name).font(.caption2).lineLimit(1)
            } minimal: {
                Image(systemName: "checklist").foregroundStyle(.orange)
            }
        }
    }
}

/// Confirms the task in place: the intent runs in the app, which holds the sign-in.
struct DoneButton: View {
    let attributes: TaskActivityAttributes

    var body: some View {
        Button(intent: CompleteTaskIntent(task: TaskEntity(id: attributes.id, name: attributes.name,
                                                           task: attributes.task))) {
            Label(attributes.task, systemImage: "checkmark").lineLimit(1)
        }
        .tint(.green)
    }
}
