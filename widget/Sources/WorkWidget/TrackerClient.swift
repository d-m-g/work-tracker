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

    /// The planner task the session is for, when the planner started it.
    var taskId: String?

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
    let taskId: String?
}

/// The planned block the widget offers: the one on now, or else the next one.
struct PlannedBlock: Equatable {
    enum When { case now, next }

    let when: When
    let taskId: Int
    let title: String
    let start: Date
    let end: Date
}

/// A task you are likely about to start, and why -- the start chooser's options.
struct Guess: Identifiable, Equatable, Decodable {
    let id: Int
    let title: String
    let reason: String
}

/// The part of `planner.py --json list` the widget reads.
private struct PlanPayload: Decodable {
    struct Block: Decodable {
        let task_id: Int
        let title: String
        let start: String
        let end: String
    }

    let now: Block?
    let next: Block?
    /// Absent from a planner too old to suggest anything; then there is nothing to offer.
    let suggestions: [Guess]?
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

    /// The environment every `tracker.py` is spawned with. It carries the
    /// remote-driving switch: when a VM is configured, the CLI drives it over SSH
    /// (and falls back to local when it cannot be reached); when nothing is
    /// configured, this is just our own environment and the widget reads and
    /// writes local files exactly as before. See ``subprocessEnvironment()``.
    let environment: [String: String]

    var script: URL { root.appendingPathComponent("tracker.py") }

    /// The planner, beside the tracker. Optional: a checkout without one simply
    /// has no plan to show, and the widget is the widget it always was.
    var planner: URL { root.appendingPathComponent("planner.py") }

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

        let environment = subprocessEnvironment()
        for candidate in candidateRoots() {
            let root = URL(fileURLWithPath: (candidate as NSString).expandingTildeInPath)
            if FileManager.default.fileExists(atPath: root.appendingPathComponent("tracker.py").path) {
                return TrackerClient(root: root, python: python, environment: environment)
            }
        }

        throw TrackerError.notFound(candidateRoots().first ?? "the usual places")
    }

    /// Build the environment for the `tracker.py` subprocesses.
    ///
    /// Our own environment, plus two things. First, a guaranteed `PATH`: a `.app`
    /// launched from Finder inherits almost none, and the CLI must be able to find
    /// `ssh` and `rsync` to reach a VM. Second, the remote-driving switch: if a VM
    /// is configured -- by the `WORK_TRACKER_SSH` environment variable, or the
    /// `WorkTrackerRemote` user default, the same way the data directory is set --
    /// the corresponding `WORK_TRACKER_SSH*` variables are passed through so the
    /// CLI drives the VM. Configure nothing and none are set, so the CLI stays
    /// purely local, exactly as before.
    private static func subprocessEnvironment() -> [String: String] {
        var environment = ProcessInfo.processInfo.environment

        let existingPath = environment["PATH"] ?? ""
        if existingPath.isEmpty {
            environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        }

        func configured(_ variable: String, _ defaultsKey: String) -> String? {
            if let value = ProcessInfo.processInfo.environment[variable], !value.isEmpty {
                return value
            }
            if let value = UserDefaults.standard.string(forKey: defaultsKey), !value.isEmpty {
                return value
            }
            return nil
        }

        guard let destination = configured("WORK_TRACKER_SSH", "WorkTrackerRemote") else {
            return environment  // no VM configured: purely local, as before.
        }
        environment["WORK_TRACKER_SSH"] = destination
        if let key = configured("WORK_TRACKER_SSH_KEY", "WorkTrackerRemoteKey") {
            environment["WORK_TRACKER_SSH_KEY"] = key
        }
        if let remotePath = configured("WORK_TRACKER_SSH_PATH", "WorkTrackerRemotePath") {
            environment["WORK_TRACKER_SSH_PATH"] = remotePath
        }
        return environment
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

    /// The block to offer: on now, or next. Never throws -- no planner, an
    /// unreadable reply, or an empty plan all mean the same thing here: nothing
    /// to offer, and the widget carries on as a plain tracker.
    func plan() -> (block: PlannedBlock?, guesses: [Guess]) {
        guard FileManager.default.fileExists(atPath: planner.path),
              let out = try? run(["--json", "list"], script: planner),
              let payload = try? JSONDecoder().decode(PlanPayload.self, from: out)
        else { return (nil, []) }

        let iso = ISO8601DateFormatter()
        func block(_ raw: PlanPayload.Block?, _ when: PlannedBlock.When) -> PlannedBlock? {
            guard let raw, let start = iso.date(from: raw.start), let end = iso.date(from: raw.end) else {
                return nil
            }
            return PlannedBlock(when: when, taskId: raw.task_id, title: raw.title, start: start, end: end)
        }
        return (block(payload.now, .now) ?? block(payload.next, .next), payload.suggestions ?? [])
    }

    /// Whether there is a planner to start tasks through at all.
    var hasPlanner: Bool { FileManager.default.fileExists(atPath: planner.path) }

    /// Something not on the list: the planner makes it a task and starts it now,
    /// parsing the words in the background so the clock does not wait for Claude.
    func startNew(_ text: String) -> String? {
        attempt(["start-new", text], script: planner)
    }

    /// Start a planned task: the planner starts the tracker with its task id and
    /// project attached, ending whatever session was running.
    func startPlanned(_ taskId: Int) -> String? {
        attempt(["start", String(taskId)], script: planner)
    }

    // MARK: - writing (through the CLI, which stays the only writer)

    /// Start, pause or resume -- whichever the state calls for. Like ⌘F8, and
    /// like a play/pause button, it can never end the session.
    func toggle() -> String? { attempt(["toggle"]) }

    /// End the session and archive it. A separate, deliberate act.
    func stop() -> String? { attempt(["stop"]) }

    /// Returns nil on success, or the CLI's own one-line complaint on failure.
    private func attempt(_ arguments: [String], script: URL? = nil) -> String? {
        do {
            _ = try run(arguments, script: script ?? self.script)
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
    private func run(_ arguments: [String], script: URL? = nil) throws -> Data {
        let process = Process()
        process.executableURL = python
        process.arguments = [(script ?? self.script).path] + arguments
        process.currentDirectoryURL = root
        process.environment = environment

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
            task: payload.task,
            taskId: payload.taskId
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
