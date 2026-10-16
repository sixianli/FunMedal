import CoreText
import Foundation

func fail(_ message: String, _ code: Int32) -> Never {
    FileHandle.standardError.write(Data("gen_font: \(message)\n".utf8))
    exit(code)
}

let args = CommandLine.arguments
guard args.count == 6, let size = Double(args[4]) else {
    fail("usage: gen_font <font file> <name> <characters> <pixel size> <output.c>", 64)
}
let fontURL = URL(fileURLWithPath: args[1])
let name = args[2]
let characters = Array(Set(args[3].unicodeScalars)).sorted { $0.value < $1.value }
let output = args[5]
guard let descriptors = CTFontManagerCreateFontDescriptorsFromURL(fontURL as CFURL) as? [CTFontDescriptor],
      let descriptor = descriptors.first else {
    fail("cannot load \(fontURL.path)", 66)
}
let font = CTFontCreateWithFontDescriptor(descriptor, size, nil)
let ascent = Int(ceil(CTFontGetAscent(font)))
let descent = Int(ceil(CTFontGetDescent(font)))
let lineHeight = ascent + descent

var bitmapBytes: [UInt8] = []
var entries: [String] = []
for scalar in characters {
    var utf16 = Array(String(scalar).utf16)
    var glyphs = [CGGlyph](repeating: 0, count: utf16.count)
    guard CTFontGetGlyphsForCharacters(font, &utf16, &glyphs, utf16.count) else {
        fail("missing glyph U+\(String(scalar.value, radix: 16))", 65)
    }
    var advance = CGSize.zero
    CTFontGetAdvancesForGlyphs(font, .horizontal, &glyphs, &advance, 1)
    let width = max(1, Int(ceil(advance.width)))
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
    let offset = bitmapBytes.count
    for row in 0..<lineHeight {
        for col in stride(from: 0, to: width, by: 2) {
            let a = pixels[row * width + col] >> 4
            let b = col + 1 < width ? pixels[row * width + col + 1] >> 4 : 0
            bitmapBytes.append(a << 4 | b)
        }
    }
    entries.append("    {0x\(String(scalar.value, radix: 16)), \(width), \(offset)},")
}

var text = "#include \"font.h\"\n\n"
text += "static const font_glyph_t glyphs[] = {\n" + entries.joined(separator: "\n") + "\n};\n\n"
text += "static const uint8_t bitmaps[] = {\n"
for chunkStart in stride(from: 0, to: bitmapBytes.count, by: 16) {
    let chunk = bitmapBytes[chunkStart..<min(chunkStart + 16, bitmapBytes.count)]
    text += "    " + chunk.map { String(format: "0x%02x,", $0) }.joined(separator: " ") + "\n"
}
text += "};\n\n"
text += "const font_t font_\(name) = {\n"
text += "    .line_height = \(lineHeight),\n"
text += "    .glyph_count = \(entries.count),\n"
text += "    .glyphs = glyphs,\n"
text += "    .bitmaps = bitmaps,\n"
text += "};\n"
do {
    try text.write(toFile: output, atomically: false, encoding: .utf8)
} catch {
    fail("cannot write \(output): \(error)", 73)
}
