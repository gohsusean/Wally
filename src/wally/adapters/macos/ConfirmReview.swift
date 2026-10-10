// Build explicitly with swiftc. Never run this helper during automated tests.
// Shows the exact immutable scope, then requires fresh enrolled biometrics.
import AppKit
import LocalAuthentication
import Foundation

let data = FileHandle.standardInput.readDataToEndOfFile()
guard data.count <= 131072,
      let input = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
      let review = input["review"] as? [String: Any],
      let nonce = input["nonce"] as? String,
      let digest = input["digest"] as? String,
      let purpose = input["purpose"] as? String,
      let expires = input["expires_at"] as? Double,
      let owner = input["owner_uid"] as? UInt32,
      owner == getuid(), expires > Date().timeIntervalSince1970,
      let rendered = try? JSONSerialization.data(withJSONObject: review, options: [.prettyPrinted, .sortedKeys]),
      let text = String(data: rendered, encoding: .utf8) else { exit(1) }

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let alert = NSAlert()
alert.messageText = "Wally: review exact scope"
alert.informativeText = "Confirm only the changes shown below. Next, authenticate with biometrics.\nReview: \(digest)"
alert.addButton(withTitle: "Review accepted — authenticate")
alert.addButton(withTitle: "Cancel")
let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 640, height: 380))
scroll.hasVerticalScroller = true
let view = NSTextView(frame: scroll.bounds)
view.isEditable = false
view.isSelectable = true
view.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
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
