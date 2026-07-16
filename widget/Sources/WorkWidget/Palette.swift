// The widget and the web viewer are two windows onto one instrument, so they
// share one palette. These re-express web/ui/src/styles.css's `:root` -- the
// portfolio's editorial dark scheme, pink accent and all -- in Swift, rather than
// being re-picked by eye. The web is dark always; the widget floats over the
// desktop, so each colour also carries a light-mode value for when the pill sits
// over a bright background.
//
// The rules that come with them, and that this file exists to keep:
//
//   * `live` (the web's `--live`) is the only accent, and it shows only while the
//     session is live. The one thing still moving is the only thing coloured.
//   * Text never wears a data hue. The dot carries the colour; the word stays
//     ink. Colour alone is not an encoding anyone can rely on.

import CoreText
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
    /// The web viewer's `--ink` in the dark; near-black on a light desktop.
    static let ink = dynamic(light: 0x101014, dark: 0xFAFAFA)

    /// Recessive ink: seconds, captions, and a clock that is standing still.
    /// The web viewer's `--ink-2` in the dark.
    static let ink2 = dynamic(light: 0x6B6B70, dark: 0xA1A1AA)

    /// Hairlines and button chrome. The web viewer's `--rule` in the dark.
    static let rule = dynamic(light: 0xD6DAE1, dark: 0x26262C)

    /// The panel's own face -- the web viewer's `--card`. The frost behind the
    /// pill samples whatever the desktop is showing, which over a white document
    /// is white; laid over the frost at near-opacity, this gives the readout a
    /// surface of its own, so its contrast holds instead of washing out when the
    /// pill floats over a pale background.
    static let surface = dynamic(light: 0xFFFFFF, dark: 0x17171C)

    /// The accent, and the only thing that moves. The web viewer's `--live` (its
    /// lighter live pink) in the dark; deepened to the web's `--work` on a light
    /// desktop, where the lighter pink would wash out against white.
    static let live = dynamic(light: 0xEC4899, dark: 0xF472B6)

    /// Something is wrong and the widget will say so rather than guess. The web
    /// viewer's `--fault` in the dark.
    static let fault = dynamic(light: 0xC1121F, dark: 0xF87171)
}

// Zodiak, the face the web is set in, so the two read as one instrument.
//
// It is a variable font: rather than ship a static weight for each use, we pin a
// point on its `wght` axis -- 500 for what is read, 600 for a stamped label --
// matching the weights web/ui/src/styles.css asks for. The binary is bundled in
// the app, never committed (its licence, public/fonts/FFL.txt §02, forbids
// putting it on a public server). When it is absent -- a `swift run` dev build,
// or a checkout without the font -- this falls back to Georgia, the very serif
// the web falls back to, so the widget degrades the same way it does rather than
// snapping to the system sans.
//
// Zodiak's figures are proportional and it has no `tnum` feature, so its digits
// jitter as a clock ticks. The font may not be recut to fix that (FFL §05); the
// web cells the digits in CSS instead (Num.jsx) and so do we -- see `Celled`.
enum Face {
    private static let family = "Zodiak Variable"
    private static let weightAxis = 2003265652  // the 'wght' variation axis id

    /// Register the bundled font once, on first use. True if Zodiak is nameable.
    private static let registered: Bool = {
        guard let url = Bundle.main.url(forResource: "Zodiak-Variable", withExtension: "ttf") else {
            return false
        }
        return CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
    }()

    private static func zodiak(_ size: CGFloat, weight: Int) -> Font {
        _ = registered
        let descriptor = CTFontDescriptorCreateWithAttributes([
            kCTFontFamilyNameAttribute as String: family,
            kCTFontVariationAttribute as String: [weightAxis: weight],
        ] as CFDictionary)
        let font = CTFontCreateWithFontDescriptor(descriptor, size, nil)
        // An unregistered family resolves to a system substitute; only when the
        // name comes back as Zodiak's own is it really Zodiak we are holding.
        if CTFontCopyFamilyName(font) as String == family {
            return Font(font)
        }
        return Font.custom("Georgia", size: size).weight(weight >= 600 ? .semibold : .regular)
    }

    /// Worked time and anything read as a measurement. The web sets its body and
    /// its clock at 500; the digits are celled by `Celled`, not by the font.
    static func data(_ size: CGFloat) -> Font { zodiak(size, weight: 500) }

    /// Every label is a stamped caption, not a heading. The web sets them at 600.
    static func label(_ size: CGFloat) -> Font { zodiak(size, weight: 600) }
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
