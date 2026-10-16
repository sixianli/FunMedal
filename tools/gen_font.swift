import CoreText
import Foundation

let args = CommandLine.arguments
guard args.count == 5, let size = Double(args[3]) else {
    FileHandle.standardError.write("usage: gen_font <font name> <characters> <pixel size> <output.c>\n".data(using: .utf8)!)
    exit(64)
}
let fontName = args[1]
let characters = Array(Set(args[2].unicodeScalars)).sorted { $0.value < $1.value }
let output = args[4]
let font = CTFontCreateWithName(fontName as CFString, size, nil)
let ascent = Int(ceil(CTFontGetAscent(font)))
let descent = Int(ceil(CTFontGetDescent(font)))
let lineHeight = ascent + descent

var bitmapBytes: [UInt8] = []
var entries: [String] = []
for scalar in characters {
    var utf16 = Array(String(scalar).utf16)
    var glyphs = [CGGlyph](repeating: 0, count: utf16.count)
    guard CTFontGetGlyphsForCharacters(font, &utf16, &glyphs, utf16.count) else {
        FileHandle.standardError.write("missing glyph U+\(String(scalar.value, radix: 16))\n".data(using: .utf8)!)
        exit(65)
    }
    var advance = CGSize.zero
    CTFontGetAdvancesForGlyphs(font, .horizontal, &glyphs, &advance, 1)
    let width = max(1, Int(ceil(advance.width)))
    let height = lineHeight
    var pixels = [UInt8](repeating: 0, count: width * height)
    let space = CGColorSpaceCreateDeviceGray()
    pixels.withUnsafeMutableBytes { buffer in
        let context = CGContext(data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8,
                                bytesPerRow: width, space: space, bitmapInfo: CGImageAlphaInfo.none.rawValue)!
        context.setFillColor(gray: 1, alpha: 1)
        context.setAllowsFontSmoothing(false)
        var position = CGPoint(x: 0, y: CGFloat(descent))
        CTFontDrawGlyphs(font, &glyphs, &position, 1, context)
    }
    let offset = bitmapBytes.count
    for row in 0..<height {
        for col in stride(from: 0, to: width, by: 2) {
            let a = pixels[row * width + col] >> 4
            let b = col + 1 < width ? pixels[row * width + col + 1] >> 4 : 0
            bitmapBytes.append(a << 4 | b)
        }
    }
    entries.append("    {0x\(String(scalar.value, radix: 16)), \(width), \(offset)},")
}

var text = "#include \"font.h\"\n\n"
text += "const uint8_t font_line_height = \(lineHeight);\n"
text += "const uint16_t font_glyph_count = \(entries.count);\n\n"
text += "const font_glyph_t font_glyphs[] = {\n" + entries.joined(separator: "\n") + "\n};\n\n"
text += "const uint8_t font_bitmaps[] = {\n"
for chunkStart in stride(from: 0, to: bitmapBytes.count, by: 16) {
    let chunk = bitmapBytes[chunkStart..<min(chunkStart + 16, bitmapBytes.count)]
    text += "    " + chunk.map { String(format: "0x%02x,", $0) }.joined(separator: " ") + "\n"
}
text += "};\n"
try! text.write(toFile: output, atomically: true, encoding: .utf8)
