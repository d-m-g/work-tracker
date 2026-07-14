// The widget and the web viewer are two windows onto one instrument, so they
// share one palette. These are the values validated in web/ui/src/styles.css
// (OKLCH lightness band, chroma floor, CVD separation, contrast against the
// surface, in both modes) -- not re-picked by eye, only re-expressed in Swift.
//
// The rules that come with them, and that this file exists to keep:
//
//   * `live` is the only warm colour. The only thing still moving is the only
//     thing that is warm.
//   * Text never wears a data hue. The dot carries the colour; the word stays
//     ink. Colour alone is not an encoding anyone can rely on.

import SwiftUI

enum Palette {
    /// Light value, dark value -- resolved per appearance, so the widget follows
    /// the system the moment it changes rather than at launch.
    private static func dynamic(light: Int, dark: Int) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            return NSColor(hex: isDark ? dark : light)
        })
    }

    /// The face of the instrument: worked time, and anything you actually read.
    static let ink = dynamic(light: 0x16191F, dark: 0xE8EBF1)

    /// Recessive ink: seconds, captions, and a clock that is standing still.
    static let ink2 = dynamic(light: 0x656D7A, dark: 0x8B93A1)

    /// Hairlines and button chrome.
    static let rule = dynamic(light: 0xD6DAE1, dark: 0x2A2E36)

    /// The warm one. The live edge, and nothing else.
    static let live = dynamic(light: 0xB26A00, dark: 0xC0801F)

    /// Something is wrong and the widget will say so rather than guess.
    static let fault = dynamic(light: 0xA03027, dark: 0xF08D81)
}

enum Face {
    /// Every number is mono and tabular, so the digits stop jittering as they tick.
    static func data(_ size: CGFloat) -> Font {
        .system(size: size, weight: .regular, design: .monospaced).monospacedDigit()
    }

    /// Every label is a stamped caption, not a heading.
    static func label(_ size: CGFloat) -> Font {
        .custom("Avenir Next", size: size).weight(.semibold)
    }
}

private extension NSColor {
    convenience init(hex: Int) {
        self.init(
            srgbRed: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1
        )
    }
}
