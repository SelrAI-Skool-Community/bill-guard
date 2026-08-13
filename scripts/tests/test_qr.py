"""Code decoding and payload parsing.

The job is not "read a QR". It is "work out where the money would go, and
find the SECOND code if there is one".
"""

from harness import test, eq, true, false, main
from billguard import qr


# --- checksums -------------------------------------------------------------

@test
def iban_checksum_accepts_real_ibans():
    true(qr.iban_checksum_ok("DE89370400440532013000"))
    true(qr.iban_checksum_ok("GB29NWBK60161331926819"))
    true(qr.iban_checksum_ok("de89 3704 0044 0532 0130 00"), "spacing, case")


@test
def iban_checksum_rejects_tampering():
    false(qr.iban_checksum_ok("DE89370400440532013001"), "last digit changed")
    false(qr.iban_checksum_ok("DE89370400440532013"), "truncated")
    false(qr.iban_checksum_ok(""))
    false(qr.iban_checksum_ok("XX"))
    false(qr.iban_checksum_ok("DE8937040044053201300!"), "bad character")


@test
def us_routing_checksum():
    true(qr.aba_routing_ok("021000021"))
    true(qr.aba_routing_ok("011401533"))
    false(qr.aba_routing_ok("021000022"))
    false(qr.aba_routing_ok("12345"))


@test
def emvco_crc_detects_alteration():
    # Minimal well-formed payload; CRC computed by the same rule a real
    # generator uses, over the payload INCLUDING the literal 6304 tag.
    body = "00020101021153036365802AU5909Test Shop6304"
    crc = qr._crc16_ccitt_false(body.encode())
    good = body + crc
    true(qr.emvco_crc_ok(good), "freshly computed CRC must verify")

    tampered = good.replace("5909Test Shop", "5909Evil Shop")
    false(qr.emvco_crc_ok(tampered), "altering the payee must break the CRC")


@test
def emvco_crc_missing_returns_none():
    eq(qr.emvco_crc_ok("000201"), None, "no CRC tag at all is unknown")


# --- payload parsing -------------------------------------------------------

@test
def parses_european_payment_payload():
    payload = ("BCD\n002\n1\nSCT\nCMCIFR2A\nFake Supplier Pty Ltd\n"
               "DE89370400440532013000\nEUR4820.00\n\n\nInvoice 12345")
    p = qr.parse_payload(payload)
    eq(p.scheme, "epc")
    eq(p.destination, "DE89370400440532013000")
    eq(p.payee_name, "Fake Supplier Pty Ltd")
    eq(p.currency, "EUR")
    eq(p.amount, "4820.00")
    true(p.checksum_ok, "the IBAN in this payload is valid")


@test
def flags_bad_iban_in_payload():
    payload = ("BCD\n002\n1\nSCT\nCMCIFR2A\nSupplier\n"
               "DE89370400440532013001\nEUR10.00")
    p = qr.parse_payload(payload)
    false(p.checksum_ok)
    true(any("checksum" in f for f in p.findings))


@test
def flags_truncated_payload():
    p = qr.parse_payload("BCD\n002\n1\nSCT")
    true(any("truncated" in f for f in p.findings))


@test
def parses_merchant_payload_and_reads_the_payee():
    body = ("00020101021153030365802AU5913Real Supplier6006Sydney"
            "62070103ABC6304")
    payload = body + qr._crc16_ccitt_false(body.encode())
    p = qr.parse_payload(payload)
    eq(p.scheme, "emvco")
    eq(p.payee_name, "Real Supplier")
    eq(p.currency, "036", "AUD is numeric 036")
    eq(p.country, "AU")
    eq(p.reference, "ABC")
    true(p.checksum_ok)


@test
def merchant_payload_that_does_not_walk_is_flagged():
    # Length says 99 but there are not 99 bytes left: hand-crafted.
    p = qr.parse_payload("000201" + "5399short")
    eq(p.scheme, "emvco")
    true(any("walk" in f for f in p.findings),
         "a payload that does not walk cleanly must be called out")


@test
def merchant_payload_wrong_country_is_flagged():
    body = "00020101021153030365802US5904Shop6304"
    payload = body + qr._crc16_ccitt_false(body.encode())
    p = qr.parse_payload(payload, jurisdiction="AU")
    true(any("not AU" in f for f in p.findings))


@test
def parses_upi_and_names_the_real_destination():
    p = qr.parse_payload(
        "upi://pay?pa=evil@okaxis&pn=Totally%20Real%20Supplier&am=500&cu=INR")
    eq(p.scheme, "upi")
    eq(p.destination, "evil@okaxis")
    true(any("display name" in f for f in p.findings),
         "must say the display name proves nothing")


@test
def upi_url_parameter_is_flagged():
    p = qr.parse_payload("upi://pay?pa=a@b&url=https://evil.example")
    true(p.url is not None)
    true(any("url parameter" in f for f in p.findings))


# --- the Australian rule ---------------------------------------------------

@test
def a_url_on_an_australian_invoice_is_not_a_payment_code():
    p = qr.parse_payload("https://pay.example.com/invoice/123",
                         jurisdiction="AU")
    eq(p.scheme, "url")
    true(any("not a payment code" in f for f in p.findings),
         "the AU standard says a payment code carries no URL")


@test
def url_outside_australia_is_not_flagged_by_that_rule():
    p = qr.parse_payload("https://pay.example.com/x", jurisdiction="DE")
    eq(p.scheme, "url")
    false(any("not a payment code" in f for f in p.findings))


@test
def dangerous_schemes_are_called_out_without_a_lookup():
    for payload in ("data:text/html;base64,PGh0bWw+",
                    "javascript:alert(1)",
                    "intent://scan#Intent;scheme=x;end",
                    "WIFI:S=net;T=WPA;P=pw;;"):
        p = qr.parse_payload(payload)
        eq(p.scheme, "url", payload)
        true(p.findings, f"{payload} must produce a finding")
        true(any("malicious in practice" in f or "no network request" in f
                 for f in p.findings), payload)


@test
def empty_payload_is_unknown_not_a_pass():
    p = qr.parse_payload("")
    eq(p.scheme, "unknown")
    true(p.findings)


@test
def plain_text_payload_is_not_a_payment():
    p = qr.parse_payload("Invoice 12345 thank you for your business")
    eq(p.scheme, "text")
    eq(p.destination, None, "text carries no destination")


# --- tlv walker ------------------------------------------------------------

@test
def tlv_walker_rejects_overruns():
    eq(qr._tlv_walk("0099abc"), None, "length longer than the data")
    eq(qr._tlv_walk("00"), None, "truncated header")
    eq(qr._tlv_walk("ab01x"), None, "non-numeric tag")


@test
def tlv_walker_reads_clean_input():
    eq(qr._tlv_walk("000201"), {"00": "01"})
    eq(qr._tlv_walk("0002015303036"), {"00": "01", "53": "036"})


# --- decoder availability --------------------------------------------------

@test
def decoder_list_is_honest():
    decoders = qr.available_decoders()
    true(isinstance(decoders, list))
    # We do not assert which are present: the point is the function reports
    # reality rather than assuming. A machine with none must say so.
    for d in decoders:
        true(d in ("pyzbar", "opencv-wechat", "opencv"), f"unknown: {d}")


@test
def decoding_a_missing_file_returns_empty_not_a_crash():
    eq(qr.decode_image("/nonexistent/path/nope.png"), [])


if __name__ == "__main__":
    main("test_qr")
