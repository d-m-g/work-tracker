import AppKit
import Combine
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var panel: FloatingPanel?
    private var status: NSStatusItem?
    private var model: TrackerModel?
    private var watch: AnyCancellable?

    // Held so the menu can be re-titled as the session moves: the same two
    // commands the panel offers, worded for whatever state you are actually in.
    private var toggleItem: NSMenuItem?
    private var stopItem: NSMenuItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let client: TrackerClient

        do {
            client = try TrackerClient.locate()
        } catch {
            // Nothing to show a session in, so say so plainly and leave. A widget
            // that launches into a permanently broken state is worse than one
            // that refuses to launch.
            fatal(error.localizedDescription)
            return
        }

        let model = TrackerModel(client: client)
        self.model = model

        let panel = FloatingPanel(content: NSHostingView(rootView: MiniPlayer(model: model)))
        panel.orderFront(nil)
        self.panel = panel

        makeStatusItem()

        // The README's other future extension, and it costs one binding: the
        // worked time, live, in the menu bar. Minutes only -- a number that
        // repaints every second in the corner of your eye is a distraction, and
        // the panel is right there when you want the seconds.
        watch = model.$snapshot
            .receive(on: RunLoop.main)
            .sink { [weak self] snapshot in
                self?.status?.button?.title = Self.menuBarTitle(for: snapshot)
                self?.retitleMenu(for: snapshot)
            }

        model.start()
    }

    private static func menuBarTitle(for snapshot: Snapshot) -> String {
        guard snapshot.fault == nil else { return "—" }

        switch snapshot.state {
        case .idle:
            return "—"
        case .running, .paused:
            let (major, _) = Clock.split(snapshot.workedSeconds)
            // A dot for a live session, an empty ring for a held one: the state
            // is legible without colour, which the menu bar would flatten anyway.
            return "\(snapshot.state == .running ? "●" : "○") \(major)"
        }
    }

    private func makeStatusItem() {
        let status = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        status.button?.font = .monospacedDigitSystemFont(ofSize: 12, weight: .regular)

        let menu = NSMenu()

        // Enablement is ours to decide, not AppKit's: "Stop" is a real command
        // with a real target even when there is nothing to stop, and we want it
        // greyed rather than silently refusing.
        menu.autoenablesItems = false

        // The same play/pause the panel has, and the same promise: it starts,
        // pauses or resumes, and it can never end the session.
        let toggle = menu.addItem(
            withTitle: "Start Session",
            action: #selector(toggle),
            keyEquivalent: ""
        )
        toggle.target = self
        toggleItem = toggle

        let stop = menu.addItem(
            withTitle: "Stop Session",
            action: #selector(stop),
            keyEquivalent: ""
        )
        stop.target = self
        stopItem = stop

        menu.addItem(.separator())

        menu.addItem(
            withTitle: "Open Viewer",
            action: #selector(openViewer),
            keyEquivalent: ""
        ).target = self

        menu.addItem(
            withTitle: "Show Widget",
            action: #selector(showPanel),
            keyEquivalent: ""
        ).target = self
        menu.addItem(.separator())
        menu.addItem(
            withTitle: "Quit",
            action: #selector(quit),
            keyEquivalent: "q"
        ).target = self

        status.menu = menu
        self.status = status
    }

    /// The menu says what the command will actually do from here, rather than
    /// naming a state you have to translate.
    private func retitleMenu(for snapshot: Snapshot) {
        let reachable = snapshot.fault == nil

        switch snapshot.state {
        case .idle:
            toggleItem?.title = "Start Session"
        case .running:
            toggleItem?.title = "Pause"
        case .paused:
            toggleItem?.title = "Resume"
        }

        toggleItem?.isEnabled = reachable
        stopItem?.isEnabled = reachable && snapshot.isActive
    }

    @objc private func toggle() {
        model?.toggle()
    }

    @objc private func stop() {
        model?.stop()
    }

    @objc private func openViewer() {
        model?.openViewer()
    }

    /// Close the viewer's server, but only if the widget was the one that started
    /// it -- a server you launched in a terminal is yours, and stays up.
    func applicationWillTerminate(_ notification: Notification) {
        model?.closeViewer()
    }

    /// The panel is only ever hidden by being dragged somewhere forgotten, or by
    /// a Space you have left. This puts it back in front of you.
    @objc private func showPanel() {
        panel?.orderFront(nil)
    }

    @objc private func quit() {
        // Quitting the widget has nothing to do with the session: the tracker is
        // files on disk, and it goes on running whether anything is watching it
        // or not.
        NSApp.terminate(nil)
    }

    private func fatal(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "Can't find the tracker"
        alert.informativeText = """
            \(message)

            Point the widget at the repository and try again:

            defaults write com.work-tracker.widget WorkTrackerHome ~/path/to/work-tracker
            """
        alert.alertStyle = .critical
        alert.runModal()

        NSApp.terminate(nil)
    }
}
