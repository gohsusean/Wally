"""Native review followed by fresh biometric authentication; no agent yes input.

The operator must separately build/review/pin the bundled Swift helper and bind
this Mac login's enrolled biometrics to the Wally owner. This is an opt-in local
trust boundary, not a hosted ChatGPT authenticator or a sandbox against code
running as the owner. No credentials or tokens are emitted by the helper.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import subprocess
import time
from pathlib import Path

from wally.runtime.confirmation import HumanReview, canonical


class MacOSBiometricConfirmation:
    @property
    def method(self) -> str:
        return (
            "macos_native_review_fresh_biometry:uid:"
            + str(self.owner_uid)
            + ":helper:"
            + self.helper_sha256
        )

    def __init__(self, helper: Path, *, helper_sha256: str, owner_uid: int):
        self.helper = helper
        self.helper_sha256 = helper_sha256
        self.owner_uid = owner_uid

    def confirm(self, review: HumanReview) -> bool:
        if platform.system() != "Darwin" or os.getuid() != self.owner_uid:
            return False
        if not self.helper.is_absolute() or self.helper.is_symlink():
            return False
        metadata = self.helper.stat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != self.owner_uid
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or hashlib.sha256(self.helper.read_bytes()).hexdigest() != self.helper_sha256
        ):
            return False
        timeout = review.expires_at - time.time()
        if timeout <= 0:
            return False
        # All review data goes over stdin, never command arguments or shell code.
        result = subprocess.run(
            [str(self.helper)],
            input=canonical(
                {
                    "review": json.loads(review.presentation),
                    "digest": review.fingerprint,
                    "nonce": review.nonce,
                    "expires_at": review.expires_at,
                    "purpose": review.purpose,
                    "owner_uid": self.owner_uid,
                }
            ),
            capture_output=True,
            text=True,
            timeout=min(timeout, 300),
            check=False,
            env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
        )
        if result.returncode != 0:
            return False
        try:
            response = json.loads(result.stdout)
        except (ValueError, TypeError):
            return False
        if not isinstance(response, dict):
            return False
        return (
            response.get("confirmed") is True
            and response == {"confirmed": True, "nonce": review.nonce, "digest": review.fingerprint}
            and time.time() < review.expires_at
        )
