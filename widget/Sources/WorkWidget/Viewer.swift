// The button that opens the web viewer.
//
// The viewer needs a server, and a button that opened a dead URL because you had
// not started one would be a button that lies. So this starts it on demand, and
// only then opens the browser.
//
// It starts one at most, and only when nothing is answering: if you already have
// `python3 web/server.py` running in a terminal, the widget uses yours and keeps
// its hands off it. It only stops a server it started itself -- quitting the
// widget should not close a window you opened.

import AppKit
import Foundation

@MainActor
final class Viewer {
    private let client: TrackerClient
    private let port: Int

    /// Non-nil only while we are the ones running it.
    private var server: Process?

    init(client: TrackerClient) {
        self.client = client
        // Match `server.py --port`, and let a user who has moved it say so.
        let stored = UserDefaults.standard.integer(forKey: "ViewerPort")
        self.port = stored > 0 ? stored : 8765
    }

    private var url: URL {
        URL(string: "http://127.0.0.1:\(port)")!
    }

    /// Returns nil on success, or a one-line complaint the widget can show.
    func open() async -> String? {
        if await isAnswering() {
            NSWorkspace.shared.open(url)
            return nil
        }

        do {
            try start()
        } catch {
            return "could not start the viewer"
        }

        // http.server binds in well under a second; give it four before giving up
        // rather than opening a browser onto a connection refused.
        for _ in 0..<20 {
            try? await Task.sleep(for: .milliseconds(200))

            if await isAnswering() {
                NSWorkspace.shared.open(url)
                return nil
            }
        }

        return "the viewer did not come up"
    }

    /// Ask the API, not the port: something else could be sitting on 8765, and
    /// opening a browser onto someone else's server would be worse than saying so.
    private func isAnswering() async -> Bool {
        var request = URLRequest(url: url.appendingPathComponent("api/status"))
        request.timeoutInterval = 0.5

        guard let (_, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse
        else { return false }

        return http.statusCode == 200
    }

    private func start() throws {
        let process = Process()
        process.executableURL = client.python
        process.arguments = [
            client.root.appendingPathComponent("web/server.py").path,
            "--port", String(port),
        ]
        process.currentDirectoryURL = client.root

        // The server's chatter has nowhere to go: the widget has no console, and
        // an unread pipe that fills up would wedge the process we depend on.
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice

        try process.run()
        server = process
    }

    /// Called when the widget quits. The tracker goes on running either way --
    /// this only closes the window onto it, and only if we opened it.
    func stop() {
        server?.terminate()
        server = nil
    }
}
