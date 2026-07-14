// The face of the instrument.
//
// The grammar is the web viewer's, held to deliberately, because they are two
// windows onto one thing:
//
//   * The minutes are what you read; the seconds only prove it is alive, so the
//     seconds are dimmed.
//   * While a pause is open the clock is dimmed whole, because the number really
//     is standing still -- worked time is frozen, and paused time is the one
//     still moving.
//   * The dot carries the colour and the word stays ink.
//   * The warm colour appears only while the session is live.

import SwiftUI

struct MiniPlayer: View {
    @ObservedObject var model: TrackerModel
    @State private var breathing = false

    private var snapshot: Snapshot { model.snapshot }

    var body: some View {
        HStack(spacing: 11) {
            dot
            readout
            Spacer(minLength: 4)
            controls
        }
        .padding(.horizontal, 14)
        .frame(width: 244, height: 68)
        .background(Frost())
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(
            // A hairline, so the pill still has an edge against a pale desktop.
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(Palette.rule.opacity(0.55), lineWidth: 1)
        )
        .onAppear {
            guard !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else { return }
            withAnimation(.easeInOut(duration: 1.2).repeatForever(autoreverses: true)) {
                breathing = true
            }
        }
    }

    // MARK: - the dot

    private var dot: some View {
        Circle()
            .fill(dotColour)
            .frame(width: 7, height: 7)
            .opacity(snapshot.state == .running && breathing ? 0.35 : 1)
    }

    private var dotColour: Color {
        switch snapshot.state {
        case .running: return Palette.live
        case .paused: return Palette.ink2
        case .idle: return Palette.rule
        }
    }

    // MARK: - the numbers

    private var readout: some View {
        VStack(alignment: .leading, spacing: 1) {
            clock
            caption
        }
    }

    private var clock: some View {
        let (major, seconds) = Clock.split(snapshot.workedSeconds)
        let held = snapshot.state != .running

        return HStack(spacing: 0) {
            Text(major)
                .foregroundStyle(held ? Palette.ink2 : Palette.ink)
            Text(seconds)
                .foregroundStyle(Palette.ink2)
        }
        .font(Face.data(25))
        .kerning(-0.5)
    }

    @ViewBuilder
    private var caption: some View {
        if let fault = snapshot.fault ?? model.complaint {
            Text(fault)
                .font(Face.label(9))
                .kerning(0.8)
                .foregroundStyle(Palette.fault)
                .lineLimit(1)
                .truncationMode(.tail)
                .help(fault)
        } else {
            Text(captionText)
                .font(Face.label(9))
                .kerning(1.3)
                .foregroundStyle(Palette.ink2)
                .lineLimit(1)
        }
    }

    private var captionText: String {
        switch snapshot.state {
        case .running:
            return snapshot.pauses == 0
                ? "RUNNING"
                : "RUNNING · \(snapshot.pauses) BREAK\(snapshot.pauses == 1 ? "" : "S")"
        case .paused:
            return "PAUSED · \(Clock.duration(snapshot.pausedSeconds))"
        case .idle:
            return "NO SESSION"
        }
    }

    // MARK: - the controls

    private var controls: some View {
        HStack(spacing: 4) {
            // One button for the whole day, the way a play/pause button works.
            Control(
                symbol: snapshot.state == .running ? "pause.fill" : "play.fill",
                help: toggleHelp,
                action: model.toggle
            )

            // Stopping is a separate, deliberate act, and it only exists when
            // there is something to stop.
            if snapshot.isActive {
                Control(symbol: "stop.fill", help: "End the session and archive it", action: model.stop)
            }
        }
        // Disabled rather than hidden when the tracker cannot be reached: the
        // widget says what is wrong instead of quietly doing nothing.
        .disabled(snapshot.fault != nil)
        .opacity(snapshot.fault != nil ? 0.4 : 1)
    }

    private var toggleHelp: String {
        switch snapshot.state {
        case .running: return "Pause"
        case .paused: return "Resume"
        case .idle: return "Start a session"
        }
    }
}

/// A control with no chrome until you reach for it.
private struct Control: View {
    let symbol: String
    let help: String
    let action: () -> Void

    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Palette.ink)
                .frame(width: 26, height: 26)
                .background(
                    Circle().fill(Palette.rule.opacity(hovering ? 0.9 : 0.35))
                )
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .help(help)
    }
}

enum Clock {
    /// "1:32" and ":11" -- the part you read, and the part that only proves the
    /// thing is alive.
    static func split(_ seconds: Int) -> (major: String, seconds: String) {
        let seconds = max(0, seconds)
        return (
            String(format: "%d:%02d", seconds / 3600, (seconds % 3600) / 60),
            String(format: ":%02d", seconds % 60)
        )
    }

    static func duration(_ seconds: Int) -> String {
        let (major, tail) = split(seconds)
        return major + tail
    }
}
