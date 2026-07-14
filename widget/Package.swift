// swift-tools-version:5.9
import PackageDescription

// The widget is an optional component, like web/ui: the tracker, the CLI and the
// Shortcuts never depend on it, and "no third-party dependencies" stays true of
// the Python. This package has no dependencies either -- AppKit and SwiftUI are
// the system frameworks -- so `swift build` needs nothing from the network.
let package = Package(
    name: "WorkWidget",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "WorkWidget", path: "Sources/WorkWidget")
    ]
)
