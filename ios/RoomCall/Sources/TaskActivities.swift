import ActivityKit
import Foundation

/// Keeps one Live Activity per open task while the app runs. Without a push service, a task
/// done elsewhere ends its activity the next time the app refreshes.
enum TaskActivities {
    @MainActor
    static func sync(_ open: [RoomTask]) {
        #if DEBUG
        if Demo.enabled { return }
        #endif
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { return }
        let wanted = Set(open.map(\.id))
        for activity in Activity<TaskActivityAttributes>.activities where !wanted.contains(activity.attributes.id) {
            Task { await activity.end(nil, dismissalPolicy: .immediate) }
        }
        let running = Set(Activity<TaskActivityAttributes>.activities.map(\.attributes.id))
        for task in open where !running.contains(task.id) {
            _ = try? Activity.request(
                attributes: TaskActivityAttributes(id: task.id, name: task.name, task: task.task),
                content: ActivityContent(state: .init(opened: Timestamp.parse(task.opened)), staleDate: nil))
        }
    }
}
