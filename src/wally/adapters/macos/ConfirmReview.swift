// Build explicitly with swiftc. Never run this helper during automated tests.
// Shows the exact immutable scope, then requires fresh enrolled biometrics.
import AppKit
import LocalAuthentication
import Foundation
import CryptoKit

if CommandLine.arguments == [CommandLine.arguments[0], "--status"] {
    let context = LAContext()
    var error: NSError?
    let available = context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error)
    let status: [String: Any] = ["owner_uid": getuid(), "biometrics_available": available,
                               "touch_id": context.biometryType == .touchID,
                               "error_code": error?.code ?? 0]
    let encoded = try! JSONSerialization.data(withJSONObject: status)
    FileHandle.standardOutput.write(encoded)
    exit(0)
}

let data = FileHandle.standardInput.readDataToEndOfFile()
guard data.count <= 131072,
      let input = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
      let canonicalReview = input["canonical_review"] as? String,
      let digest = input["digest"] as? String,
      SHA256.hash(data: Data(canonicalReview.utf8)).map({ String(format: "%02x", $0) }).joined() == digest,
      let envelope = try? JSONSerialization.jsonObject(with: Data(canonicalReview.utf8)) as? [String: Any],
      let nonce = envelope["nonce"] as? String,
      let purpose = envelope["purpose"] as? String,
      let expires = envelope["expires_at"] as? Double,
      let principal = envelope["principal"] as? String,
      let channel = envelope["channel"] as? String,
      let presentation = envelope["presentation"] as? String,
      let review = try? JSONSerialization.jsonObject(with: Data(presentation.utf8)) as? [String: Any],
      let owner = input["owner_uid"] as? UInt32,
      owner == getuid(), expires > Date().timeIntervalSince1970,
      let rendered = try? JSONSerialization.data(withJSONObject: review, options: [.prettyPrinted, .sortedKeys]),
      let text = String(data: rendered, encoding: .utf8) else { exit(1) }

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let alert = NSAlert()
alert.messageText = "Wally: review exact scope"
alert.informativeText = "Confirm only the scope below, independently of the conversation.\nPrincipal: \(principal) · Channel: \(channel) · Mac UID: \(owner)\nPurpose: \(purpose) · Review: \(digest)"
alert.addButton(withTitle: "Review accepted — authenticate")
alert.addButton(withTitle: "Cancel")
let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 640, height: 380))
scroll.hasVerticalScroller = true
let view = NSTextView(frame: scroll.bounds)
view.isEditable = false
view.isSelectable = true
view.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
view.isVerticallyResizable = true
view.maxSize = NSSize(width: 640, height: CGFloat.greatestFiniteMagnitude)
view.textContainer?.containerSize = NSSize(width: 640, height: CGFloat.greatestFiniteMagnitude)
view.textContainer?.widthTracksTextView = true
view.string = text
scroll.documentView = view
alert.accessoryView = scroll
app.activate(ignoringOtherApps: true)
guard alert.runModal() == .alertFirstButtonReturn,
      expires > Date().timeIntervalSince1970 else { exit(1) }

let authentication = LAContext()
authentication.touchIDAuthenticationAllowableReuseDuration = 0
authentication.localizedFallbackTitle = ""
var error: NSError?
guard authentication.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error)
else { exit(1) }
// The dialog click alone cannot authorize. No device-password fallback or reuse.
authentication.evaluatePolicy(
    .deviceOwnerAuthenticationWithBiometrics,
    localizedReason: "Confirm Wally \(purpose), review \(digest.prefix(16))"
) { success, _ in
    DispatchQueue.main.async {
        guard success, expires > Date().timeIntervalSince1970 else { exit(1) }
        let output: [String: Any] = ["confirmed": true, "nonce": nonce, "digest": digest]
        guard let encoded = try? JSONSerialization.data(withJSONObject: output) else { exit(1) }
        FileHandle.standardOutput.write(encoded)
        exit(0)
    }
}
app.run()
