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
//
// The panel shows the session's task only when you ask for it. A glance holds
// one number, and a line of prose sitting permanently under the clock is a line
// of prose you end up reading instead of glancing at; so the task waits in the
// caption's slot, under the state, and takes it over while the pointer rests on
// the readout. Reaching for it is the asking.
//
// It takes over the slot rather than being given one of its own: the panel is a
// fixed size, and a caption that appeared would move the clock. Nothing here
// moves. The line is one line wide either way, capped, and the tail of a long
// task is left to the tooltip rather than to the layout.

import SwiftUI

struct MiniPlayer: View {
    /// The panel is sized from the view, not the other way round, so there is one
    /// number to change when a control is added.
    static let size = CGSize(width: 276, height: 68)

    /// What the caption line may occupy. The panel is a fixed width and the
    /// controls have first claim on it, so this is what is left once the padding,
    /// the dot, the gaps and three buttons have been paid for. A task longer than
    /// this is truncated, never allowed to push anything.
    private static let captionWidth: CGFloat = 118

    @ObservedObject var model: TrackerModel
    @Environment(\.colorScheme) private var colorScheme
    @State private var breathing = false

    /// The pointer is resting on the readout, so the caption gives its line to
    /// the task.
    @State private var reading = false

    private var snapshot: Snapshot { model.snapshot }

    /// A task, and someone reaching for it. Either half missing and the caption
    /// stays as it was -- there is nothing to swap to on a session that was never
    /// given a name.
    private var showingTask: Bool { reading && snapshot.task != nil }

    var body: some View {
        HStack(spacing: 11) {
            dot
            readout
            Spacer(minLength: 4)
            controls
        }
        .padding(.horizontal, 14)
        .frame(width: MiniPlayer.size.width, height: MiniPlayer.size.height)
        .background(
            // The pill's face is the web's, layered so the readout still holds.
            // The frost alone lets a white desktop bleed through and wash the
            // numerals out; the gradient -- the same bg-gradient.svg the web page
            // wears -- gives the pill the shared identity; and the surface over it
            // is the legibility scrim, thin in the dark where the gradient can
            // show through, near-opaque in the light where near-black text needs a
            // pale ground. A sliver of translucency is left throughout, so the
            // pill still reads as desktop furniture and not a card dropped on top.
            ZStack {
                Frost()
                GradientBackdrop()
                Palette.surface.opacity(colorScheme == .dark ? 0.55 : 0.85)
            }
        )
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(
            // The pink edge the web wears, so the pill has an outline against a
            // pale desktop and reads as the same instrument -- firmer than a
            // hairline, because the drop shadow does little over a bright ground.
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(Palette.live.opacity(0.9), lineWidth: 1)
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
        // The whole readout is the target, not the caption alone: a 9pt line is
        // too small a thing to have to hit, and the clock above it is the part
        // you were already looking at.
        .contentShape(Rectangle())
        .onHover { hovering in
            withAnimation(swap) { reading = hovering }
        }
    }

    /// Long enough to read as a swap rather than a flicker, short enough that the
    /// line is there by the time you have finished reaching for it.
    private var swap: Animation? {
        NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
            ? nil
            : .easeOut(duration: 0.18)
    }

    private var clock: some View {
        let (major, seconds) = Clock.split(snapshot.workedSeconds)
        let held = snapshot.state != .running

        // The minutes are what you read; the seconds only prove it is alive, so
        // the seconds are dimmed. Each is celled, so nothing shifts as it ticks.
        return HStack(spacing: 0) {
            Celled(major, size: 25, colour: held ? Palette.ink2 : Palette.ink)
            Celled(seconds, size: 25, colour: Palette.ink2)
        }
    }

    // Three things want this one line, and they are ranked. A fault outranks
    // everything -- it is the one thing you must not be able to hover away. Then
    // the task, while you are reaching for it. Then the state, which is what the
    // line says the rest of the time.
    //
    // The state and the task are stacked, not switched, so they cross over in
    // place: one lifts out as the other rises in, and neither can resize a slot
    // the other is standing in.
    @ViewBuilder
    private var caption: some View {
        if let fault = snapshot.fault ?? model.complaint {
            line(fault, colour: Palette.fault, kerning: 0.8)
                .help(fault)
        } else {
            ZStack(alignment: .leading) {
                line(captionText, colour: Palette.ink2, kerning: 1.3)
                    .opacity(showingTask ? 0 : 1)
                    .offset(y: showingTask ? -3 : 0)

                if let task = snapshot.task {
                    // Ink, where the state is recessive ink: this is the line you
                    // asked for, so for as long as you hold it, it is the one you
                    // are meant to be reading.
                    //
                    // And set as prose, where the state is stamped: RUNNING and
                    // PAUSED are the instrument's own small vocabulary and can be
                    // stamped like a label, but a task is a sentence you wrote.
                    // Letterspaced capitals would be both harder to read and half
                    // again as wide -- on a line this short, that is the
                    // difference between a task you can read and a task that is
                    // three words and an ellipsis.
                    line(task, colour: Palette.ink, kerning: 0)
                        .opacity(showingTask ? 1 : 0)
                        .offset(y: showingTask ? 0 : 3)
                        // A task may run to 200 characters and the line is one
                        // line. The tail goes to the tooltip rather than to a
                        // marquee: a caption that crawls is a caption you have to
                        // wait for, and this one is meant to be glanced at too.
                        .help(task)
                }
            }
        }
    }

    /// One line of stamped caption, whatever it says. Capped and truncated at the
    /// source, so nothing a session was named can move anything.
    private func line(_ text: String, colour: Color, kerning: CGFloat) -> some View {
        Text(text)
            .font(Face.label(9))
            .kerning(kerning)
            .foregroundStyle(colour)
            .lineLimit(1)
            .truncationMode(.tail)
            .frame(maxWidth: MiniPlayer.captionWidth, alignment: .leading)
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
            // The way out to the history. It survives a fault deliberately: if
            // today's session is unreadable, the archive of every other day is
            // exactly what you would want to go and look at.
            Control(
                symbol: "chart.bar.fill",
                help: "Open the viewer",
                busy: model.openingViewer,
                action: model.openViewer
            )

            Group {
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
    var busy: Bool = false
    let action: () -> Void

    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            face
                .frame(width: 26, height: 26)
                .background(
                    // Pink, the web's accent, so the controls read as the same
                    // instrument's. Soft at rest and filling under the pointer,
                    // the way the web's one filled button lifts on hover.
                    Circle().fill(Palette.live.opacity(hovering ? 0.9 : 0.4))
                )
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .disabled(busy)
        .onHover { hovering = $0 }
        .help(help)
    }

    @ViewBuilder
    private var face: some View {
        if busy {
            // Starting the viewer's server takes a moment. Without this the click
            // looks like it missed, and you click again.
            ProgressView()
                .controlSize(.small)
                .scaleEffect(0.6)
        } else {
            Image(systemName: symbol)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Palette.ink)
        }
    }
}

/// A number set so it holds still: each digit in a fixed-width cell, centred, so
/// a `1` occupies exactly what a `0` does and nothing shifts as the clock ticks.
///
/// This is the widget's take on the web's `Num.jsx`. Zodiak's figures are
/// proportional and its licence forbids recutting a tabular set in, so the web
/// gives each digit an equal CSS cell; here each character is its own view in a
/// zero-spacing row. Only digits are celled -- the colon passes through at its
/// natural width, exactly as on the web -- and rendering each glyph separately
/// sidesteps kerning as a bonus: there is no adjacent pair left for the font to
/// pull together.
private struct Celled: View {
    let text: String
    let size: CGFloat
    let colour: Color

    init(_ text: String, size: CGFloat, colour: Color) {
        self.text = text
        self.size = size
        self.colour = colour
    }

    /// One digit cell. The web measured 0.72em as wide enough for Zodiak's widest
    /// figure at these weights; the same ratio holds here, being the same font.
    private var cell: CGFloat { size * 0.72 }

    var body: some View {
        HStack(spacing: 0) {
            ForEach(Array(text.enumerated()), id: \.offset) { _, character in
                if character.isNumber {
                    Text(String(character)).frame(width: cell)
                } else {
                    Text(String(character))
                }
            }
        }
        .font(Face.data(size))
        .foregroundStyle(colour)
    }
}

/// The pink gradient the web page wears, behind the pill.
///
/// It is the very same asset -- web/ui/public/bg-gradient.svg, a field of blurred
/// blobs on the paper ground -- bundled into the app by make-app.sh and drawn as
/// a vector (AppKit renders SVG natively), so the widget and the page share one
/// backdrop rather than a lookalike. The web sets it `center / 80%`; here it is
/// scaled to fill the pill's width, which for a panel this wide shows the whole
/// breadth of the composition, cropped to a band -- the same artwork, scaled down
/// to pill size. Absent -- a `swift run` dev build with no bundle -- it falls
/// back to the paper ground alone, so the pill still has a solid face.
private struct GradientBackdrop: View {
    private static let image: NSImage? = {
        guard let url = Bundle.main.url(forResource: "bg-gradient", withExtension: "svg") else {
            return nil
        }
        return NSImage(contentsOf: url)
    }()

    var body: some View {
        if let image = GradientBackdrop.image {
            // The pill is far wider than the artwork, so filling it lands one huge
            // blob across the middle -- a hard shape, not the web's soft field. A
            // heavy blur (over the blur the SVG already carries) and a scale past
            // the edges melt it back into an even wash of colour, which is what the
            // gradient reads as at page size and what belongs behind a readout.
            Image(nsImage: image)
                .resizable()
                .scaledToFill()
                .scaleEffect(1.4)
                .blur(radius: 22)
        } else {
            // The gradient's own ground, so the fallback is the same colour the
            // artwork sits on rather than a hole.
            Color(nsColor: NSColor(srgbRed: 0x10 / 255, green: 0x10 / 255, blue: 0x14 / 255, alpha: 1))
        }
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
