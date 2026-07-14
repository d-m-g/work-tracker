import AppKit

let app = NSApplication.shared

// .accessory is LSUIElement without the Info.plist: no Dock icon, no menu bar of
// its own, and no place in ⌘-Tab. The widget is furniture, not an app you switch
// to -- which is also why it can be run straight from `swift run`, with no bundle
// to assemble first.
app.setActivationPolicy(.accessory)

// Top-level code is not main-actor isolated as far as the compiler is concerned,
// but it does in fact run on the main thread -- which is exactly what
// assumeIsolated is for.
let delegate = MainActor.assumeIsolated { AppDelegate() }
app.delegate = delegate
app.run()
