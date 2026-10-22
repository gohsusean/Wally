"""Internal HLB picture gate. No images, credentials or page text are persisted.

Selectors come from reviewed operator/runtime code, never model or page prose.
The owner views enrollment in the existing local browser, not a screenshot.
"""

import base64
import hashlib
import hmac
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from wally.finance.models import FinanceError

HLB_ORIGIN = "https://s.hongleongconnect.my"
REFERENCE = Path(__file__).resolve().parents[4] / "data/private-auth/hlb/picture.sha256"

# Decode the actual, loaded IMG at its intrinsic resolution. No screenshots,
# network re-fetches, filenames, alt text, similarity threshold or LLM decisions.
PIXELS = """async img => {
    if (img.tagName !== 'IMG' ||
        new URL(img.currentSrc).origin !== location.origin) throw Error();
    await Promise.race([img.decode(), new Promise((_, reject) =>
        setTimeout(() => reject(Error()), 5000))]);
    if (!img.complete) throw Error();
    const w = img.naturalWidth, h = img.naturalHeight;
    if (w < 16 || h < 16 || w > 512 || h > 512) throw Error();
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0);
    const pixels = ctx.getImageData(0, 0, w, h).data;
    const colors = new Set();
    for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i+3] !== 255) throw Error();
        colors.add(`${pixels[i]},${pixels[i+1]},${pixels[i+2]}`);
    }
    if (colors.size < 16) throw Error();
    let binary = '';
    for (let i = 0; i < pixels.length; i += 8192)
        binary += String.fromCharCode(...pixels.subarray(i, i+8192));
    return {width:w, height:h, rgba:btoa(binary)};
}"""


def check_hlb_origin(page):
    """Exact official HTTPS origin; Playwright's normal TLS validation is retained."""
    try:
        url = urlsplit(page.url)
        if (
            url.scheme != "https"
            or url.hostname != "s.hongleongconnect.my"
            or url.port not in (None, 443)
            or url.username
            or url.password
            or url.fragment
        ):
            raise ValueError
    except Exception:
        raise FinanceError("HLB HTTPS origin verification failed.") from None


@dataclass(frozen=True)
class HLBSecurityPicture:
    image_selector: str
    checkbox_selector: str

    def __post_init__(self):
        if not self.image_selector or not self.checkbox_selector:
            raise ValueError("HLB picture and checkbox selectors must be reviewed.")

    def capture(self, page):
        """Return only an in-process fingerprint; raw pixels never leave this helper."""
        try:
            check_hlb_origin(page)
            image = page.locator(self.image_selector)
            if image.count() != 1 or not image.is_visible():
                raise ValueError
            sample = image.evaluate(PIXELS)
            width, height = sample["width"], sample["height"]
            if type(width) is not int or type(height) is not int:
                raise ValueError
            if not (16 <= width <= 512 and 16 <= height <= 512):
                raise ValueError
            pixels = base64.b64decode(sample["rgba"], validate=True)
            if len(pixels) != width * height * 4:
                raise ValueError
            result = hashlib.sha256(f"hlb-rgba-v1:{width}:{height}:".encode() + pixels).hexdigest()
            check_hlb_origin(page)
            return result
        except Exception:
            raise FinanceError(
                "HLB security picture missing, ambiguous or unreadable; login stopped."
            ) from None

    def verify(self, page, reference):
        expected = read_reference(reference)
        first = self.capture(page)
        if not hmac.compare_digest(first, expected) or not hmac.compare_digest(
            self.capture(page), expected
        ):
            raise FinanceError("HLB security picture mismatch or change; login stopped.")

    def confirm(self, page, *, reference=REFERENCE, guard):
        """Only an exact stable match can tick the native confirmation checkbox."""
        self.verify(page, reference)
        guard()
        self.verify(page, reference)  # Re-read after any human/credential prompt.
        try:
            check_hlb_origin(page)
            checkbox = page.locator(self.checkbox_selector)
            if checkbox.count() != 1 or not checkbox.is_visible() or not checkbox.is_enabled():
                raise ValueError
            checkbox.check(timeout=5000)
            self.verify(page, reference)
            if not checkbox.is_checked():
                raise ValueError
        except FinanceError:
            raise
        except Exception:
            raise FinanceError("HLB picture confirmation failed; login stopped.") from None

    def enroll(self, page, *, confirm_owner, reference=REFERENCE, replace=False, guard):
        """Explicit local owner ceremony, never invoked by verify or mismatch recovery."""
        previous = read_reference(reference) if reference.exists() else None
        if previous is not None and not replace:
            raise FinanceError("HLB picture already enrolled; explicit replacement is required.")
        candidate = self.capture(page)
        guard()
        if confirm_owner() is not True:
            raise FinanceError("HLB picture enrollment was not confirmed.")
        guard()
        if self.capture(page) != candidate or self.capture(page) != candidate:
            raise FinanceError("HLB picture changed during owner confirmation; enrollment stopped.")
        current = read_reference(reference) if reference.exists() else None
        if current != previous:
            raise FinanceError("HLB reference changed during confirmation; enrollment stopped.")
        write_reference(reference, candidate)


def _private_directory(path):
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise FinanceError("HLB reference directory must be private and local.")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise FinanceError("HLB reference directory must be owner-only (0700).")


def read_reference(path):
    try:
        _private_directory(path.parent)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size != 65
            ):
                raise ValueError
            value = source.read(66)
        if not re.fullmatch(r"[0-9a-f]{64}\n", value):
            raise ValueError
        return value.strip()
    except Exception:
        raise FinanceError(
            "HLB picture reference missing or unsafe; enroll locally before login."
        ) from None


def write_reference(path, fingerprint):
    """Persist only a hash in the ignored, non-synced private authentication area."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _private_directory(path.parent)
        if path.is_symlink():
            raise ValueError
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".picture-")
        temporary = Path(name)
        with os.fdopen(fd, "w") as target:
            target.write(fingerprint + "\n")
        temporary.replace(path)
    except Exception:
        raise FinanceError("HLB private reference could not be saved.") from None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
