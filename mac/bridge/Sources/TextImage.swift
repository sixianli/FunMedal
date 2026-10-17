import CoreGraphics
import CoreText
import Foundation

enum TextImage {
    static let width = 220
    static let height = 100
    static let stride = 28
    static let lineHeight: CGFloat = 20
    static let fontSize: CGFloat = 17
    static let inkThreshold: UInt8 = 128

    static func paragraphStyle() -> CTParagraphStyle {
        var height = lineHeight
        return withUnsafeBytes(of: &height) { bytes in
            let settings = [
                CTParagraphStyleSetting(spec: .minimumLineHeight, valueSize: bytes.count, value: bytes.baseAddress!),
                CTParagraphStyleSetting(spec: .maximumLineHeight, valueSize: bytes.count, value: bytes.baseAddress!),
            ]
            return CTParagraphStyleCreate(settings, settings.count)
        }
    }

    static func attributed(_ text: String) -> CFAttributedString? {
        let font = CTFontCreateUIFontForLanguage(.system, fontSize, "zh-Hans" as CFString)
            ?? CTFontCreateWithName("PingFang SC" as CFString, fontSize, nil)
        let attributes: [CFString: Any] = [
            kCTFontAttributeName: font,
            kCTForegroundColorAttributeName: CGColor(gray: 0, alpha: 1),
            kCTParagraphStyleAttributeName: paragraphStyle(),
        ]
        return CFAttributedStringCreate(nil, text as CFString, attributes as CFDictionary)
    }

    static func draw(_ text: String, in context: CGContext) {
        guard let string = attributed(text), let ellipsis = attributed("…") else { return }
        let length = CFAttributedStringGetLength(string)
        let framesetter = CTFramesetterCreateWithAttributedString(string)
        let path = CGPath(rect: CGRect(x: 0, y: 0, width: width, height: height), transform: nil)
        let frame = CTFramesetterCreateFrame(framesetter, CFRange(location: 0, length: 0), path, nil)
        guard let lines = CTFrameGetLines(frame) as? [CTLine], !lines.isEmpty else { return }
        var origins = [CGPoint](repeating: .zero, count: lines.count)
        CTFrameGetLineOrigins(frame, CFRange(location: 0, length: 0), &origins)
        let visible = CTFrameGetVisibleStringRange(frame)
        let cut = visible.location + visible.length < length
        let token = CTLineCreateWithAttributedString(ellipsis)
        for (index, line) in lines.enumerated() {
            var shown = line
            if cut, index == lines.count - 1 {
                let start = CTLineGetStringRange(line).location
                if let rest = CFAttributedStringCreateWithSubstring(nil, string,
                                                                     CFRange(location: start, length: length - start)) {
                    shown = CTLineCreateTruncatedLine(CTLineCreateWithAttributedString(rest), Double(width), .end,
                                                      token) ?? line
                }
            }
            context.textPosition = origins[index]
            CTLineDraw(shown, context)
        }
    }

    static func render(_ text: String) -> (pixels: Data, ink: Int)? {
        guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width,
                                      space: CGColorSpaceCreateDeviceGray(),
                                      bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return nil }
        context.setFillColor(gray: 1, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        context.setShouldSmoothFonts(false)
        draw(text, in: context)
        guard let gray = context.data?.assumingMemoryBound(to: UInt8.self) else { return nil }
        var pixels = Data(count: stride * height)
        var ink = 0
        for y in 0..<height {
            for x in 0..<width where gray[y * width + x] < inkThreshold {
                pixels[y * stride + x / 8] |= UInt8(0x80 >> (x % 8))
                ink += 1
            }
        }
        return (pixels, ink)
    }
}
