// What the view watches. It owns the poll loop and nothing else.

import Combine
import Foundation

@MainActor
final class TrackerModel: ObservableObject {
    @Published private(set) var snapshot: Snapshot = .unknown

    /// A refusal from the CLI -- "a session is already in progress", say. Shown
    /// briefly, then cleared: it is a reply to a button press, not a state.
    @Published private(set) var complaint: String?

    /// True while the viewer is being started, so the button can say it heard you.
    /// Starting a server takes long enough to look like nothing happened.
    @Published private(set) var openingViewer = false

    private let client: TrackerClient
    private let viewer: Viewer
    private let queue = DispatchQueue(label: "work-tracker.poll")

    /// A separate lane for button presses, so a `toggle` or `stop` never waits in
    /// line behind a status poll. It only ever mattered once the poll could be
    /// slow; now the CLI answers locally and returns at once, this keeps a button
    /// instant even if a poll is briefly held up. Concurrent poll and act are safe:
    /// the CLI's writes are atomic and O_EXCL-guarded, built for exactly this.
    private let actions = DispatchQueue(label: "work-tracker.act")
    private var timer: Timer?
    private var complaintTask: Task<Void, Never>?

    init(client: TrackerClient) {
        self.client = client
        self.viewer = Viewer(client: client)
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

    /// Open the web viewer, starting its server first if nothing is answering.
    func openViewer() {
        guard !openingViewer else { return }
        openingViewer = true

        Task { @MainActor in
            let complaint = await viewer.open()
            openingViewer = false
            show(complaint)
        }
    }

    /// Only stops a server the widget itself started.
    func closeViewer() {
        viewer.stop()
    }

    private func act(_ command: @escaping (TrackerClient) -> String?) {
        let client = self.client

        actions.async {
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
