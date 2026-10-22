import AppIntents

struct RoomCallShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: OpenRoomCallIntent(), phrases: ["Open \(.applicationName)"],
                    shortTitle: "ios_open", systemImageName: "mic.fill")
        AppShortcut(intent: AnnounceIntent(), phrases: ["Announce with \(.applicationName)"],
                    shortTitle: "ios_announce", systemImageName: "speaker.wave.2.fill")
    }
}
