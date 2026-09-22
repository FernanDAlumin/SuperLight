import AppKit
let output = CommandLine.arguments[1]
try FileManager.default.createDirectory(atPath: output, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                                      bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                      isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        let transform = NSAffineTransform(); transform.scale(by: CGFloat(pixels) / 1024); transform.concat()
        NSColor(srgbRed: 0.15, green: 0.17, blue: 0.13, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 42, y: 42, width: 940, height: 940), xRadius: 220, yRadius: 220).fill()
        let bolt = NSBezierPath(); bolt.move(to: NSPoint(x: 602, y: 855)); bolt.line(to: NSPoint(x: 285, y: 433)); bolt.line(to: NSPoint(x: 477, y: 433)); bolt.line(to: NSPoint(x: 411, y: 167)); bolt.line(to: NSPoint(x: 748, y: 581)); bolt.line(to: NSPoint(x: 553, y: 581)); bolt.close()
        NSColor(srgbRed: 0.86, green: 0.93, blue: 0.64, alpha: 1).setFill(); bolt.fill()
        NSGraphicsContext.restoreGraphicsState()
        let name = "icon_\(size)x\(size)" + (scale == 2 ? "@2x" : "") + ".png"
        try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output).appendingPathComponent(name))
    }
}
