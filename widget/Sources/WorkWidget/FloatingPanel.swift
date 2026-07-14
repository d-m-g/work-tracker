// The window. Everything that makes it feel like a mini player rather than a
// small app is a property set here.

import AppKit
import SwiftUI

final class FloatingPanel: NSPanel {
    init(content: NSView) {
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 244, height: 68),
            // .nonactivatingPanel is the important one: clicking the widget does
            // not steal focus, so pause/resume never pulls you out of the editor
            // you were typing in. A mini player you have to click away from is a
            // mini player that costs you the thing it was meant to save.
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )

        isFloatingPanel = true
        level = .floating
        hidesOnDeactivate = false

        // Follow you everywhere: across Spaces, and over a full-screen app --
        // which is where you actually are when you lose track of the time.
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]

        // The rounded corners are drawn by SwiftUI, so the window itself must be
        // transparent -- otherwise a square of grey shows behind them.
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true

        isMovableByWindowBackground = true

        // No title bar to grab, so the panel would have nowhere to remember. It
        // remembers anyway: put it somewhere once and it stays there across
        // launches.
        setFrameAutosaveName("WorkWidgetPanel")

        contentView = content

        if !setFrameUsingName("WorkWidgetPanel") {
            positionAtTopRight()
        }
    }

    /// Borderless windows are not key by default, which would leave the buttons
    /// unclickable.
    override var canBecomeKey: Bool { true }

    private func positionAtTopRight() {
        guard let screen = NSScreen.main else { return }

        let margin: CGFloat = 24
        let visible = screen.visibleFrame
        setFrameOrigin(
            NSPoint(
                x: visible.maxX - frame.width - margin,
                y: visible.maxY - frame.height - margin
            )
        )
    }
}

/// The frosted background. `.hudWindow` is the material the system's own
/// floating controls use, so the widget reads as part of the desktop furniture
/// rather than as a web page someone left on top.
struct Frost: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = .hudWindow
        view.blendingMode = .behindWindow
        view.state = .active
        return view
    }

    func updateNSView(_ view: NSVisualEffectView, context: Context) {}
}
