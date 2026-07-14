// The widget's entire relationship with the tracker: it shells out to the CLI.
//
// It deliberately does not read current.json, and it deliberately does not do
// the duration arithmetic itself. Both would be easy, and both would be a second
// implementation of rules that already exist in one place -- and a second
// implementation is a thing that can drift. The CLI computes the durations, so
// the widget cannot disagree with `tracker.py status`.
//
// It is also not a writer. `toggle` and `stop` run the same commands the
// Shortcuts run, through the same atomic, O_EXCL-guarded code path, so a widget
// button and a keypress on ⌘F8 cannot race each other into a corrupt session.

import Foundation

/// One reading of the tracker: what `--json status` said, or why it could not say.
struct Snapshot: Equatable {
    enum State: String, Decodable {
        case idle, running, paused
    }

    var state: State = .idle
    var workedSeconds: Int = 0
    var pausedSeconds: Int = 0
    var pauses: Int = 0
    var start: Date?

    /// What the session is being spent on, if it was ever named. Optional in the
    /// tracker and optional here: most sessions never get one.
    var task: String?

    /// Set when the tracker could not be read at all. Reported, never repaired --
    /// the widget will not guess what a corrupt file meant.
    var fault: String?

    var isActive: Bool { state != .idle }

    static let unknown = Snapshot()
}

/// The shape `tracker.py --json status` prints.
///
/// It names only the keys the widget uses, and a Decodable ignores what it was
/// not told to expect -- which is what keeps this struct from having to change
/// every time the tracker learns to say something new.
private struct StatusPayload: Decodable {
    let state: Snapshot.State
    let start: String?
    let workedSeconds: Int
    let pausedSeconds: Int
    let pauses: Int

    /// Absent from a payload written before the tracker learned about tasks, and
    /// null whenever the session simply has none. Both mean the same thing.
    let task: String?
}

enum TrackerError: LocalizedError {
    case notFound(String)

    var errorDescription: String? {
        switch self {
        case .notFound(let path): return "no tracker.py under \(path)"
        }
    }
}

struct TrackerClient {
    /// The repository, which is also the default data directory.
    let root: URL

    /// The interpreter. macOS ships 3.9 at /usr/bin/python3 and the tracker
    /// targets it deliberately, so the widget calls it too -- it keeps working
    /// across Homebrew upgrades and clean installs.
    let python: URL

    var script: URL { root.appendingPathComponent("tracker.py") }

    // MARK: - locating the tracker

    /// Resolve the repository, in the order that lets the same binary work from a
    /// build directory, from an .app bundle, and from a copy somewhere else:
    ///
    /// 1. `--root DIR` on the command line;
    /// 2. `WORK_TRACKER_HOME`, the variable the CLI already honours;
    /// 3. the `WorkTrackerHome` user default, which is how the .app is told;
    /// 4. walking up from the executable, which is what makes `swift run` work
    ///    inside a clone with no configuration at all.
    static func locate() throws -> TrackerClient {
        let python = URL(fileURLWithPath: "/usr/bin/python3")

        for candidate in candidateRoots() {
            let root = URL(fileURLWithPath: (candidate as NSString).expandingTildeInPath)
            if FileManager.default.fileExists(atPath: root.appendingPathComponent("tracker.py").path) {
                return TrackerClient(root: root, python: python)
            }
        }

        throw TrackerError.notFound(candidateRoots().first ?? "the usual places")
    }

    private static func candidateRoots() -> [String] {
        var roots: [String] = []

        let args = CommandLine.arguments
        if let flag = args.firstIndex(of: "--root"), args.indices.contains(flag + 1) {
            roots.append(args[flag + 1])
        }
        if let home = ProcessInfo.processInfo.environment["WORK_TRACKER_HOME"] {
            roots.append(home)
        }
        if let stored = UserDefaults.standard.string(forKey: "WorkTrackerHome") {
            roots.append(stored)
        }

        // …/work-tracker/widget/.build/release/WorkWidget -> …/work-tracker
        var dir = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        for _ in 0..<6 {
            dir.deleteLastPathComponent()
            roots.append(dir.path)
        }

        return roots
    }

    // MARK: - reading

    /// Poll the tracker. Never throws: a failure becomes a `fault` on the
    /// snapshot, because a widget that vanishes on error is worse than one that
    /// says what is wrong.
    func status() -> Snapshot {
        do {
            let out = try run(["--json", "status"])
            var snapshot = try decode(out)
            snapshot.fault = nil
            return snapshot
        } catch {
            var snapshot = Snapshot.unknown
            snapshot.fault = message(for: error)
            return snapshot
        }
    }

    // MARK: - writing (through the CLI, which stays the only writer)

    /// Start, pause or resume -- whichever the state calls for. Like ⌘F8, and
    /// like a play/pause button, it can never end the session.
    func toggle() -> String? { attempt(["toggle"]) }

    /// End the session and archive it. A separate, deliberate act.
    func stop() -> String? { attempt(["stop"]) }

    /// Returns nil on success, or the CLI's own one-line complaint on failure.
    private func attempt(_ arguments: [String]) -> String? {
        do {
            _ = try run(arguments)
            return nil
        } catch {
            return message(for: error)
        }
    }

    // MARK: - plumbing

    private struct CommandFailure: Error {
        let output: String
    }

    @discardableResult
    private func run(_ arguments: [String]) throws -> Data {
        let process = Process()
        process.executableURL = python
        process.arguments = [script.path] + arguments
        process.currentDirectoryURL = root

        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr

        try process.run()

        // Read before waiting: a pipe that fills up would deadlock a process we
        // are waiting on. The payloads here are tiny, but the rule is cheap.
        let out = stdout.fileHandleForReading.readDataToEndOfFile()
        let err = stderr.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        guard process.terminationStatus == 0 else {
            let complaint = String(decoding: err.isEmpty ? out : err, as: UTF8.self)
            throw CommandFailure(output: complaint.trimmingCharacters(in: .whitespacesAndNewlines))
        }

        return out
    }

    private func decode(_ data: Data) throws -> Snapshot {
        let payload = try JSONDecoder().decode(StatusPayload.self, from: data)

        return Snapshot(
            state: payload.state,
            workedSeconds: payload.workedSeconds,
            pausedSeconds: payload.pausedSeconds,
            pauses: payload.pauses,
            start: payload.start.flatMap { ISO8601DateFormatter().date(from: $0) },
            task: payload.task
        )
    }

    private func message(for error: Error) -> String {
        switch error {
        case let failure as CommandFailure:
            guard !failure.output.isEmpty else { return "the tracker failed" }
            // The CLI stamps "error: " on its complaints because a terminal has
            // no other way to say so. The widget has colour; it does not need
            // the word as well.
            return failure.output.replacingOccurrences(
                of: "^error:\\s*", with: "", options: [.regularExpression]
            )
        case let tracker as TrackerError:
            return tracker.localizedDescription
        case is DecodingError:
            return "the tracker's reply was not valid JSON"
        default:
            return error.localizedDescription
        }
    }
}
