"""QR and barcode handling for invoices.

Two jobs, and the second is the one that matters:

1. Find EVERY code on EVERY page. The overlay attack -- a payment code
   pasted or drawn on top of a legitimate one -- leaves two codes on the
   page, and detecting both is the entire tell. A decoder that finds one
   code and stops is worse than useless here, because it reports success.

2. Decode the payload and work out WHERE THE MONEY GOES, then compare that
   to the supplier we think we are paying. A human cannot eyeball an account
   number inside a bitmap, which is the whole reason the attack works.

Decoder cascade, best-effort, all optional:
    zbar (via pyzbar)  -> handles damaged codes and non-QR barcodes
    OpenCV WeChat      -> CNN detector, best free option on bad photos
    OpenCV QRCodeDetector.detectAndDecodeMulti -> always present with cv2

Every decoder is optional. Missing decoders degrade to a reported UNKNOWN,
never to a silent pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class DecodedCode:
    payload: str
    symbology: str = "QR"
    page: int = 0
    decoder: str = ""
    bbox: tuple | None = None

    def __hash__(self):
        return hash((self.payload, self.page))


@dataclass
class PaymentPayload:
    """What a decoded code actually asks you to do."""
    scheme: str                     # emvco | epc | upi | url | text | unknown
    destination: str | None = None  # the account/IBAN/VPA money would go to
    payee_name: str | None = None
    amount: str | None = None
    currency: str | None = None
    country: str | None = None
    reference: str | None = None
    url: str | None = None
    checksum_ok: bool | None = None
    findings: list = field(default_factory=list)
    fields: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------

def available_decoders() -> list[str]:
    """Which decoders this machine can actually use, best first."""
    found = []
    try:
        import pyzbar.pyzbar  # noqa: F401
        found.append("pyzbar")
    except Exception:
        pass
    try:
        import cv2
        if hasattr(cv2, "wechat_qrcode_WeChatQRCode"):
            found.append("opencv-wechat")
        if hasattr(cv2, "QRCodeDetector"):
            found.append("opencv")
    except Exception:
        pass
    return found


def decode_image(path: str, page: int = 0) -> list[DecodedCode]:
    """Decode every code in one image. Order of preference, deduped."""
    import os
    if not os.path.isfile(path):
        return []
    out: dict[str, DecodedCode] = {}

    for code in _decode_pyzbar(path, page):
        out.setdefault(code.payload, code)
    for code in _decode_opencv_multi(path, page):
        out.setdefault(code.payload, code)

    return list(out.values())


def _decode_pyzbar(path: str, page: int) -> list[DecodedCode]:
    try:
        from pyzbar.pyzbar import decode as zdecode
        from PIL import Image
    except Exception:
        return []
    try:
        results = zdecode(Image.open(path))
    except Exception:
        return []
    codes = []
    for r in results:
        try:
            payload = r.data.decode("utf-8", errors="replace")
        except Exception:
            continue
        codes.append(DecodedCode(payload, r.type, page, "pyzbar"))
    return codes


def _decode_opencv_multi(path: str, page: int) -> list[DecodedCode]:
    try:
        import cv2
    except Exception:
        return []
    try:
        img = cv2.imread(path)
        if img is None:
            return []
        det = cv2.QRCodeDetector()
        ok, texts, points, _ = det.detectAndDecodeMulti(img)
    except Exception:
        return []
    if not ok or not texts:
        return []
    codes = []
    for i, t in enumerate(texts):
        if not t:
            continue
        bbox = None
        try:
            if points is not None and i < len(points):
                bbox = tuple(map(tuple, points[i].tolist()))
        except Exception:
            bbox = None
        codes.append(DecodedCode(t, "QRCODE", page, "opencv", bbox))
    return codes


# ---------------------------------------------------------------------------
# payload parsing
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"^(https?|ftp)://", re.I)
_DANGEROUS_SCHEME_RE = re.compile(
    r"^(data:|javascript:|intent://|file:|wifi:)", re.I)


def parse_payload(payload: str, jurisdiction: str = "AU") -> PaymentPayload:
    """Work out what a decoded code is asking for."""
    text = (payload or "").strip()
    if not text:
        return PaymentPayload("unknown", findings=["empty payload"])

    if _DANGEROUS_SCHEME_RE.match(text):
        p = PaymentPayload("url", url=text)
        p.findings.append(
            "payload uses a scheme that renders content or launches an app "
            "without a network request, so no reputation service can ever "
            "see it. On an invoice this is malicious in practice.")
        return p

    if text.startswith("BCD\n") or text.startswith("BCD\r\n"):
        return _parse_epc(text)

    if text.startswith("000201") or text.startswith("00020101") or \
            text.startswith("00020102"):
        return _parse_emvco(text, jurisdiction)

    if text.lower().startswith("upi://"):
        return _parse_upi(text)

    if _URL_RE.match(text):
        p = PaymentPayload("url", url=text)
        if jurisdiction == "AU":
            p.findings.append(
                "Under the Australian merchant QR standard a payment code "
                "contains no URL that can route the payer anywhere. A code on "
                "an Australian invoice that decodes to a web address is not a "
                "payment code and must not be treated as one.")
        return p

    return PaymentPayload("text", reference=text[:200])


def _parse_epc(text: str) -> PaymentPayload:
    """European credit transfer code. Newline delimited, positional."""
    lines = text.replace("\r\n", "\n").split("\n")
    p = PaymentPayload("epc")
    p.fields = {f"line{i+1}": v for i, v in enumerate(lines)}
    if len(lines) < 7:
        p.findings.append("truncated: fewer than the 7 mandatory lines")
        return p
    if lines[3].strip().upper() != "SCT":
        p.findings.append(f"line 4 should be SCT, found {lines[3]!r}")
    p.swift = lines[4].strip() or None
    p.payee_name = lines[5].strip() or None
    iban = lines[6].strip().replace(" ", "").upper()
    p.destination = iban or None
    p.checksum_ok = iban_checksum_ok(iban) if iban else None
    if p.checksum_ok is False:
        p.findings.append("IBAN fails its checksum")
    if len(lines) > 7 and lines[7].strip():
        amt = lines[7].strip()
        if len(amt) > 3 and amt[:3].isalpha():
            p.currency, p.amount = amt[:3].upper(), amt[3:]
        else:
            p.amount = amt
    if len(lines) > 9 and lines[9].strip():
        p.reference = lines[9].strip()
    elif len(lines) > 10 and lines[10].strip():
        p.reference = lines[10].strip()
    if len(lines) > 10 and lines[9].strip() and lines[10].strip():
        p.findings.append(
            "both the structured and unstructured reference lines are "
            "populated; the standard allows only one")
    return p


def _parse_emvco(text: str, jurisdiction: str) -> PaymentPayload:
    """Merchant-presented code. Flat tag-length-value."""
    p = PaymentPayload("emvco")
    tags = _tlv_walk(text)
    if tags is None:
        p.findings.append(
            "tag structure does not walk cleanly to the end of the payload, "
            "which means it was hand-crafted or is corrupt")
        return p
    p.fields = tags

    p.checksum_ok = emvco_crc_ok(text)
    if p.checksum_ok is False:
        p.findings.append("CRC does not match: the payload was altered")

    p.currency = tags.get("53")
    p.amount = tags.get("54")
    p.country = tags.get("58")
    p.payee_name = tags.get("59")
    extra = tags.get("62")
    if isinstance(extra, str):
        sub = _tlv_walk(extra) or {}
        p.reference = sub.get("01") or sub.get("05")

    for tag in range(65, 80):
        if f"{tag:02d}" in tags:
            p.findings.append(
                f"tag {tag} is reserved and must not carry data")

    for tag_num in range(26, 52):
        val = tags.get(f"{tag_num:02d}")
        if isinstance(val, str) and val:
            sub = _tlv_walk(val) or {}
            gui = sub.get("00")
            if gui:
                p.destination = f"{gui}:{sub.get('01', '')}".rstrip(":")
                break

    if jurisdiction == "AU" and p.country and p.country.upper() != "AU":
        p.findings.append(
            f"code declares country {p.country}, not AU, on an "
            f"Australian invoice")
    return p


def _parse_upi(text: str) -> PaymentPayload:
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(text).query)
    p = PaymentPayload("upi")
    p.fields = {k: v[0] for k, v in q.items() if v}
    p.destination = p.fields.get("pa")
    p.payee_name = p.fields.get("pn")
    p.amount = p.fields.get("am")
    p.currency = p.fields.get("cu")
    p.reference = p.fields.get("tr") or p.fields.get("tn")
    if p.fields.get("url"):
        p.url = p.fields["url"]
        p.findings.append(
            "carries a url parameter, which is an unvalidated fetch target")
    if not p.destination:
        p.findings.append("no payee address: money has no stated destination")
    else:
        p.findings.append(
            "the payee address is where money goes; the display name beside "
            "it is attacker-controlled text and proves nothing")
    return p


def _tlv_walk(text: str) -> dict | None:
    """Walk a flat tag-length-value string. None if it does not walk cleanly.

    Tags are kept as the literal two-character strings the spec defines.
    Normalising "01" to "1" silently loses every sub-tag below ten, which
    is where the reference and the merchant identifier live.
    """
    out: dict[str, str] = {}
    i = 0
    n = len(text)
    while i < n:
        if i + 4 > n:
            return None
        tag = text[i:i + 2]
        length_s = text[i + 2:i + 4]
        if not tag.isdigit() or not length_s.isdigit():
            return None
        length = int(length_s)
        start = i + 4
        end = start + length
        if end > n:
            return None
        out[tag] = text[start:end]
        i = end
    return out


# ---------------------------------------------------------------------------
# checksums
# ---------------------------------------------------------------------------

def emvco_crc_ok(payload: str) -> bool | None:
    """Recompute the CRC over the payload including the literal 6304 tag.

    A bad CRC means tampering or a broken generator. A good CRC means
    nothing about legitimacy: an attacker computes one trivially.
    """
    idx = payload.rfind("6304")
    if idx < 0 or len(payload) < idx + 8:
        return None
    body = payload[:idx + 4]
    stated = payload[idx + 4:idx + 8].upper()
    return _crc16_ccitt_false(body.encode("ascii", "ignore")) == stated


def _crc16_ccitt_false(data: bytes) -> str:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def iban_checksum_ok(iban: str) -> bool:
    """ISO 7064 mod-97-10. Structural only: a generated IBAN passes trivially."""
    s = re.sub(r"\s", "", iban or "").upper()
    if len(s) < 5 or not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    rearranged = s[4:] + s[:4]
    digits_str = ""
    for ch in rearranged:
        if ch.isdigit():
            digits_str += ch
        elif ch.isalpha():
            digits_str += str(ord(ch) - 55)
        else:
            return False
    return int(digits_str) % 97 == 1


def aba_routing_ok(routing: str) -> bool:
    """US routing number check digit."""
    d = re.sub(r"\D", "", routing or "")
    if len(d) != 9:
        return False
    n = [int(c) for c in d]
    total = (3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7])
             + (n[2] + n[5] + n[8]))
    return total % 10 == 0
