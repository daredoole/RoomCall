import AppIntents

struct RoomCallShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: OpenRoomCallIntent(), phrases: ["Open \(.applicationName)"],
                    shortTitle: "ios_open", systemImageName: "mic.fill")
        AppShortcut(intent: AnnounceIntent(), phrases: ["Announce with \(.applicationName)"],
                    shortTitle: "ios_announce", systemImageName: "speaker.wave.2.fill")
        AppShortcut(intent: CompleteTaskIntent(), phrases: ["Mark a \(.applicationName) task done"],
                    shortTitle: "ios_mark_done", systemImageName: "checkmark.circle.fill")
        AppShortcut(intent: DoNotDisturbIntent(), phrases: ["Set \(.applicationName) Do Not Disturb"],
                    shortTitle: "ios_dnd", systemImageName: "bell.slash.fill")
    }
}
