// Draws the Study Desk icon (the app's favicon: a pen on a blue square) as a 1024 px PNG.
// swift make_icon.swift out.png
import AppKit
let size: CGFloat = 1024
let img = NSImage(size: NSSize(width: size, height: size))
img.lockFocus()
let inset: CGFloat = 100
let rect = NSRect(x: inset, y: inset, width: size - 2 * inset, height: size - 2 * inset)
NSColor(red: 0x24/255, green: 0x45/255, blue: 0xC8/255, alpha: 1).setFill()
NSBezierPath(roundedRect: rect, xRadius: 185, yRadius: 185).fill()
// the pen, from the favicon path (32-unit grid, y flipped)
let s = rect.width / 32, ox = rect.minX, oy = rect.minY
func p(_ x: CGFloat, _ y: CGFloat) -> NSPoint { NSPoint(x: ox + x * s, y: oy + (32 - y) * s) }
let pen = NSBezierPath()
pen.move(to: p(9, 22.5)); pen.line(to: p(20.5, 11))
pen.curve(to: p(23.5, 14), controlPoint1: p(21.3, 10.2), controlPoint2: p(24.3, 13.2))
pen.line(to: p(12, 25.5)); pen.line(to: p(8, 26.5)); pen.close()
pen.lineWidth = 2.4 * s; pen.lineJoinStyle = .round
NSColor.white.setStroke(); pen.stroke()
img.unlockFocus()
let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
