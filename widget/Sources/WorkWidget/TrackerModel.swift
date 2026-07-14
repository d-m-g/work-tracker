// What the view watches. It owns the poll loop and nothing else.

import Combine
import Foundation

@MainActor
final class TrackerModel: ObservableObject {
    @Published private(set) var snapshot: Snapshot = .unknown

    /// A refusal from the CLI -- "a session is already in progress", say. Shown
    /// briefly, then cleared: it is a reply to a button press, not a state.
    @Published private(set) var complaint: String?

    private let client: TrackerClient
    private let queue = DispatchQueue(label: "work-tracker.poll")
    private var timer: Timer?
    private var complaintTask: Task<Void, Never>?

    init(client: TrackerClient) {
        self.client = client
    }

    // MARK: - polling

    func start() {
        refresh()
        schedule()
    }

    /// The tracker is polled, not watched, so the interval is the whole cost of
    /// the widget: one short-lived `python3` per tick. It is worth pacing.
    ///
    /// While a pause is open, worked time is standing still -- so there is
    /// nothing to animate and no reason to poll every second. Idle costs less
    /// still: nothing changes until you press something.
    private func interval(for state: Snapshot.State) -> TimeInterval {
        switch state {
        case .running: return 1
        case .paused: return 2
        case .idle: return 5
        }
    }

    private func schedule() {
        timer?.invalidate()

        let timer = Timer(timeInterval: interval(for: snapshot.state), repeats: false) { [weak self] _ in
            Task { @MainActor in
                self?.refresh()
                self?.schedule()
            }
        }

        // .common, so the clock keeps ticking while the panel is being dragged.
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    private func refresh() {
        let client = self.client

        queue.async {
            let snapshot = client.status()

            Task { @MainActor in
                self.snapshot = snapshot
            }
        }
    }

    // MARK: - acting

    /// Start, pause or resume. Never stops -- a misclick cannot end your day.
    func toggle() {
        act { $0.toggle() }
    }

    func stop() {
        act { $0.stop() }
    }

    private func act(_ command: @escaping (TrackerClient) -> String?) {
        let client = self.client

        queue.async {
            let complaint = command(client)

            // Re-read rather than assume: the CLI acts on what is on disk, and a
            // Shortcut may have changed it in the meantime.
            let snapshot = client.status()

            Task { @MainActor in
                self.snapshot = snapshot
                self.show(complaint)
                self.schedule()
            }
        }
    }

    private func show(_ complaint: String?) {
        self.complaint = complaint
        complaintTask?.cancel()

        guard complaint != nil else { return }

        complaintTask = Task { @MainActor in
            try? await Task.sleep(for: .seconds(4))
            guard !Task.isCancelled else { return }
            self.complaint = nil
        }
    }
}
