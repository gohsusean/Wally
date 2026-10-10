"""Build an ad-hoc signed, private native review app; never launch authentication.

Run with the existing interpreter. Prints only the helper locator and integrity
pin. The destination must be new so an installed/pinned helper is never replaced.
"""

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    destination = args.destination
    if not destination.is_absolute() or destination.exists():
        parser.error("Choose a new absolute destination directory.")
    destination.mkdir(parents=True, mode=0o700)
    app = destination / "Wally Approval.app"
    contents = app / "Contents"
    executable = contents / "MacOS" / "WallyApproval"
    executable.parent.mkdir(parents=True, mode=0o700)
    info = {
        "CFBundleExecutable": "WallyApproval",
        "CFBundleIdentifier": "com.wally.approval.local",
        "CFBundleName": "Wally Approval",
        "CFBundleDisplayName": "Wally Approval",
        "CFBundleVersion": "1",
        "CFBundlePackageType": "APPL",
        "LSUIElement": True,
        "NSFaceIDUsageDescription": "Authenticate the exact Wally action you reviewed.",
    }
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    source = Path(__file__).resolve().parents[1] / "src/wally/adapters/macos/ConfirmReview.swift"
    subprocess.run(
        [
            "/usr/bin/swiftc",
            str(source),
            "-o",
            str(executable),
            "-module-cache-path",
            str(destination / "swift-cache"),
        ],
        check=True,
    )
    executable.chmod(0o700)
    subprocess.run(["/usr/bin/codesign", "--sign", "-", str(app)], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)], check=True)
    print(
        json.dumps(
            {
                "helper": str(executable),
                "helper_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "owner_uid": os.getuid(),
            }
        )
    )


if __name__ == "__main__":
    main()
