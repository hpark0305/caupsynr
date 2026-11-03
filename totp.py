"""RFC 6238 time-based one-time passwords (Google Authenticator compatible), stdlib only.

Used as the "일회용 비밀번호" second factor for researchers signing in to the
portal from outside the university network (개인정보의 안전성 확보조치 기준 제6조②).
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6


def new_secret():
    """160-bit random secret, base32 without padding (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def _key(secret):
    clean = secret.replace(' ', '').upper()
    return base64.b32decode(clean + '=' * (-len(clean) % 8))


def code_at(secret, counter):
    digest = hmac.new(_key(secret), struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def verify(secret, code, last_counter=None, now=None, window=1):
    """Return the matched time-step counter, or None.

    Accepts ±`window` steps for clock drift and rejects any counter at or
    below `last_counter`, so a code cannot be replayed after a successful use.
    """
    code = (code or '').replace(' ', '')
    if not (secret and code.isdigit() and len(code) == DIGITS):
        return None
    current = int((now if now is not None else time.time()) // STEP)
    for counter in range(current - window, current + window + 1):
        if last_counter is not None and counter <= int(last_counter):
            continue
        if hmac.compare_digest(code_at(secret, counter), code):
            return counter
    return None


def provisioning_uri(secret, account, issuer='TSL Research Portal'):
    label = quote(f'{issuer}:{account}', safe='')
    return f'otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe="")}&digits={DIGITS}&period={STEP}'
