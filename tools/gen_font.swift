import CoreText
import Foundation

func fail(_ message: String, _ code: Int32) -> Never {
    FileHandle.standardError.write(Data("gen_font: \(message)\n".utf8))
    exit(code)
}

let args = CommandLine.arguments
guard args.count == 5, let size = Double(args[3]) else {
    fail("usage: gen_font <font file> <characters> <pixel size> <output.raw>", 64)
}
let fontURL = URL(fileURLWithPath: args[1])
let characters = Array(Set(args[2].unicodeScalars)).sorted { $0.value < $1.value }
let output = args[4]
guard let descriptors = CTFontManagerCreateFontDescriptorsFromURL(fontURL as CFURL) as? [CTFontDescriptor],
      let descriptor = descriptors.first else {
    fail("cannot load \(fontURL.path)", 66)
}
let font = CTFontCreateWithFontDescriptor(descriptor, size, nil)
let ascent = Int(ceil(CTFontGetAscent(font)))
let descent = Int(ceil(CTFontGetDescent(font)))
let lineHeight = ascent + descent

guard lineHeight > 0, lineHeight < 256 else {
    fail("line height \(lineHeight) does not fit in one byte", 65)
}
var raw: [UInt8] = [UInt8(lineHeight)]
for scalar in characters {
    var utf16 = Array(String(scalar).utf16)
    var glyphs = [CGGlyph](repeating: 0, count: utf16.count)
    guard CTFontGetGlyphsForCharacters(font, &utf16, &glyphs, utf16.count) else {
        fail("missing glyph U+\(String(scalar.value, radix: 16))", 65)
    }
    var advance = CGSize.zero
    CTFontGetAdvancesForGlyphs(font, .horizontal, &glyphs, &advance, 1)
    let width = max(1, Int(ceil(advance.width)))
    guard width < 65536 else {
        fail("glyph U+\(String(scalar.value, radix: 16)) is \(width) px wide", 65)
    }
    var pixels = [UInt8](repeating: 0, count: width * lineHeight)
    let space = CGColorSpaceCreateDeviceGray()
    pixels.withUnsafeMutableBytes { buffer in
        let context = CGContext(data: buffer.baseAddress, width: width, height: lineHeight, bitsPerComponent: 8,
                                bytesPerRow: width, space: space, bitmapInfo: CGImageAlphaInfo.none.rawValue)!
        context.setFillColor(gray: 1, alpha: 1)
        context.setAllowsFontSmoothing(false)
        var position = CGPoint(x: 0, y: CGFloat(descent))
        CTFontDrawGlyphs(font, &glyphs, &position, 1, context)
    }
    withUnsafeBytes(of: scalar.value.littleEndian) { raw.append(contentsOf: $0) }
    withUnsafeBytes(of: UInt16(width).littleEndian) { raw.append(contentsOf: $0) }
    raw.append(contentsOf: pixels)
}

do {
    try Data(raw).write(to: URL(fileURLWithPath: output))
} catch {
    fail("cannot write \(output): \(error)", 73)
}
