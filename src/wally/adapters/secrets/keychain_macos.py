"""Login-keychain secrets for macOS.

References look like ``keychain://service/account``. The item is a generic
password in the current user's login keychain. The access list trusts the
calling executable's code signature and does not trust ``/usr/bin/security``.

macOS identifies that signature, not the Python script. This interpreter is
uv's ad-hoc-signed CPython, so other programs launched with the same binary
can read the item while the login keychain is unlocked. A different binary
is prompted, and a LaunchAgent cannot answer that prompt. Re-run credential
install after the Python binary changes.
"""

from __future__ import annotations

import sys
from ctypes import (
    CDLL,
    Structure,
    byref,
    c_char_p,
    c_int32,
    c_long,
    c_uint32,
    c_void_p,
    create_string_buffer,
    string_at,
)

from wally.exceptions import ProviderUnavailableError
from wally.runtime.secrets_safety import evaluate_secret_reference

_ERR_NOT_FOUND = -25300
_ERR_INTERACTION = -25308
_ERR_AUTH = -25293
_UTF8 = 0x08000100


class _KeyCB(Structure):
    _fields_ = [
        ("version", c_long),
        ("retain", c_void_p),
        ("release", c_void_p),
        ("copyDescription", c_void_p),
        ("equal", c_void_p),
        ("hash", c_void_p),
    ]


class _ValCB(Structure):
    _fields_ = [
        ("version", c_long),
        ("retain", c_void_p),
        ("release", c_void_p),
        ("copyDescription", c_void_p),
        ("equal", c_void_p),
    ]


class _ArrCB(Structure):
    _fields_ = [
        ("version", c_long),
        ("retain", c_void_p),
        ("release", c_void_p),
        ("copyDescription", c_void_p),
        ("equal", c_void_p),
    ]


class MacKeychainSecretsProvider:
    """Resolve ``keychain://`` references through Security.framework."""

    def __init__(self) -> None:
        self._cf = None
        self._sec = None
        self._ready = False
        if sys.platform != "darwin":
            return
        try:
            cf = CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            sec = CDLL("/System/Library/Frameworks/Security.framework/Security")
            self._bind(cf, sec)
            self._cf = cf
            self._sec = sec
            self._key_cb = _KeyCB.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
            self._val_cb = _ValCB.in_dll(cf, "kCFTypeDictionaryValueCallBacks")
            self._arr_cb = _ArrCB.in_dll(cf, "kCFTypeArrayCallBacks")
            self._ready = True
        except (OSError, AttributeError, ValueError):
            self._ready = False

    @property
    def name(self) -> str:
        return "secrets"

    def is_healthy(self) -> bool:
        return self._ready

    def resolve(self, reference: str) -> str:
        service, account = self._identity(reference)
        self._require_ready()
        owned: list[c_void_p] = []
        try:
            query = self._query(service, account, owned, read=True)
            result = c_void_p()
            status = self._sec.SecItemCopyMatching(query, byref(result))
            if status == _ERR_NOT_FOUND:
                raise ProviderUnavailableError(self.name, "Keychain item was not found.")
            self._check(status, "read")
            if not result.value:
                raise ProviderUnavailableError(self.name, "Keychain returned an empty secret.")
            owned.append(result)
            value = self._bytes(result).decode("utf-8")
        finally:
            self._release(owned)
        if not value:
            raise ProviderUnavailableError(self.name, "Keychain returned an empty secret.")
        return value

    def store(self, reference: str, value: str) -> None:
        service, account = self._identity(reference)
        self._require_ready()
        secret = value.encode("utf-8")
        if not secret or b"\n" in secret or b"\r" in secret or b"\x00" in secret:
            raise ProviderUnavailableError(
                self.name,
                "Secret value must be a single non-empty line.",
            )
        owned: list[c_void_p] = []
        try:
            status = self._update(service, account, secret, owned)
            if status == _ERR_NOT_FOUND:
                status = self._add(service, account, secret, owned)
            self._check(status, "write")
        finally:
            self._release(owned)

    def delete(self, reference: str) -> None:
        service, account = self._identity(reference)
        self._require_ready()
        owned: list[c_void_p] = []
        try:
            status = self._sec.SecItemDelete(self._query(service, account, owned))
            if status not in (0, _ERR_NOT_FOUND):
                self._check(status, "delete")
        finally:
            self._release(owned)

    def _require_ready(self) -> None:
        if not self._ready:
            raise ProviderUnavailableError(
                self.name,
                "macOS login keychain is not available in this process.",
            )

    def _identity(self, reference: str) -> tuple[str, str]:
        policy = evaluate_secret_reference(reference)
        if not policy.allowed or not reference.startswith("keychain://"):
            raise ProviderUnavailableError(
                self.name,
                policy.reason or "Invalid secret reference.",
            )
        service, account = reference[len("keychain://") :].split("/", 1)
        return service, account

    def _bind(self, cf: CDLL, sec: CDLL) -> None:
        cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
        cf.CFStringCreateWithCString.restype = c_void_p
        cf.CFDataCreate.argtypes = [c_void_p, c_char_p, c_long]
        cf.CFDataCreate.restype = c_void_p
        cf.CFDataGetBytePtr.argtypes = [c_void_p]
        cf.CFDataGetBytePtr.restype = c_void_p
        cf.CFDataGetLength.argtypes = [c_void_p]
        cf.CFDataGetLength.restype = c_long
        cf.CFRelease.argtypes = [c_void_p]
        cf.CFDictionaryCreate.argtypes = [
            c_void_p,
            c_void_p,
            c_void_p,
            c_long,
            c_void_p,
            c_void_p,
        ]
        cf.CFDictionaryCreate.restype = c_void_p
        cf.CFArrayCreate.argtypes = [c_void_p, c_void_p, c_long, c_void_p]
        cf.CFArrayCreate.restype = c_void_p
        sec.SecItemAdd.argtypes = [c_void_p, c_void_p]
        sec.SecItemAdd.restype = c_int32
        sec.SecItemCopyMatching.argtypes = [c_void_p, c_void_p]
        sec.SecItemCopyMatching.restype = c_int32
        sec.SecItemUpdate.argtypes = [c_void_p, c_void_p]
        sec.SecItemUpdate.restype = c_int32
        sec.SecItemDelete.argtypes = [c_void_p]
        sec.SecItemDelete.restype = c_int32
        sec.SecTrustedApplicationCreateFromPath.argtypes = [c_char_p, c_void_p]
        sec.SecTrustedApplicationCreateFromPath.restype = c_int32
        sec.SecAccessCreate.argtypes = [c_void_p, c_void_p, c_void_p]
        sec.SecAccessCreate.restype = c_int32

    def _const(self, name: str) -> c_void_p:
        return c_void_p.in_dll(self._sec, name)

    def _cfstr(self, text: str, owned: list[c_void_p]) -> c_void_p:
        value = c_void_p(self._cf.CFStringCreateWithCString(None, text.encode(), _UTF8))
        owned.append(value)
        return value

    def _cfdata(self, secret: bytes, owned: list[c_void_p]) -> c_void_p:
        buffer = create_string_buffer(secret)
        value = c_void_p(self._cf.CFDataCreate(None, buffer, len(secret)))
        buffer.raw = b"\x00" * len(buffer)
        owned.append(value)
        return value

    def _dict(self, pairs: list[tuple[c_void_p, c_void_p]], owned: list[c_void_p]) -> c_void_p:
        count = len(pairs)
        keys = (c_void_p * count)(*(key for key, _ in pairs))
        values = (c_void_p * count)(*(value for _, value in pairs))
        made = c_void_p(
            self._cf.CFDictionaryCreate(
                None,
                keys,
                values,
                count,
                byref(self._key_cb),
                byref(self._val_cb),
            )
        )
        owned.append(made)
        return made

    def _query(
        self,
        service: str,
        account: str,
        owned: list[c_void_p],
        *,
        read: bool = False,
    ) -> c_void_p:
        pairs = [
            (self._const("kSecClass"), self._const("kSecClassGenericPassword")),
            (self._const("kSecAttrService"), self._cfstr(service, owned)),
            (self._const("kSecAttrAccount"), self._cfstr(account, owned)),
            (
                self._const("kSecUseAuthenticationUI"),
                self._const("kSecUseAuthenticationUIFail"),
            ),
        ]
        if read:
            pairs.extend(
                [
                    (self._const("kSecReturnData"), c_void_p.in_dll(self._cf, "kCFBooleanTrue")),
                    (self._const("kSecMatchLimit"), self._const("kSecMatchLimitOne")),
                ]
            )
        return self._dict(pairs, owned)

    def _update(self, service: str, account: str, secret: bytes, owned: list[c_void_p]) -> int:
        query = self._query(service, account, owned)
        payload = self._dict(
            [(self._const("kSecValueData"), self._cfdata(secret, owned))],
            owned,
        )
        return int(self._sec.SecItemUpdate(query, payload))

    def _add(self, service: str, account: str, secret: bytes, owned: list[c_void_p]) -> int:
        access = self._access(owned)
        pairs = [
            (self._const("kSecClass"), self._const("kSecClassGenericPassword")),
            (self._const("kSecAttrService"), self._cfstr(service, owned)),
            (self._const("kSecAttrAccount"), self._cfstr(account, owned)),
            (self._const("kSecValueData"), self._cfdata(secret, owned)),
            (self._const("kSecAttrAccess"), access),
            (
                self._const("kSecAttrAccessible"),
                self._const("kSecAttrAccessibleWhenUnlocked"),
            ),
        ]
        return int(self._sec.SecItemAdd(self._dict(pairs, owned), None))

    def _access(self, owned: list[c_void_p]) -> c_void_p:
        app = c_void_p()
        status = int(
            self._sec.SecTrustedApplicationCreateFromPath(None, byref(app))
        )
        if status != 0 or not app.value:
            raise ProviderUnavailableError(
                self.name,
                "Could not restrict the keychain item to this executable.",
            )
        owned.append(app)
        apps = (c_void_p * 1)(app)
        array = c_void_p(self._cf.CFArrayCreate(None, apps, 1, byref(self._arr_cb)))
        owned.append(array)
        label = self._cfstr("Wally", owned)
        access = c_void_p()
        status = int(self._sec.SecAccessCreate(label, array, byref(access)))
        if status != 0 or not access.value:
            raise ProviderUnavailableError(
                self.name,
                "Could not restrict the keychain item to this executable.",
            )
        owned.append(access)
        return access

    def _bytes(self, data: c_void_p) -> bytes:
        length = int(self._cf.CFDataGetLength(data))
        pointer = self._cf.CFDataGetBytePtr(data)
        if length <= 0 or not pointer:
            return b""
        return ctypes_string(pointer, length)

    def _check(self, status: int, action: str) -> None:
        if status == 0:
            return
        if status in {_ERR_INTERACTION, _ERR_AUTH}:
            raise ProviderUnavailableError(
                self.name,
                "Keychain refused a non-interactive "
                f"{action}. Unlock the login keychain and use the Wally Python.",
            )
        raise ProviderUnavailableError(
            self.name,
            f"Keychain {action} failed ({status}).",
        )

    def _release(self, owned: list[c_void_p]) -> None:
        if self._cf is None:
            return
        for item in reversed(owned):
            if item and getattr(item, "value", item):
                self._cf.CFRelease(item)


def ctypes_string(pointer: int, length: int) -> bytes:
    return string_at(pointer, length)
