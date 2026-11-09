from seahorse import payfast


def test_encoding_matches_php_urlencode():
    assert payfast.pf_encode("Seahorse Ride ~ test") == "Seahorse+Ride+%7E+test"
    assert payfast.pf_encode("a/b?c=d&e") == "a%2Fb%3Fc%3Dd%26e"
    assert payfast.pf_encode(" trimmed ") == "trimmed"


def test_signature_ignores_blank_fields_and_appends_passphrase():
    pairs = [("merchant_id", "10000100"), ("merchant_key", "46f0cd694581a"), ("name_last", ""), ("amount", "100.00"),
             ("item_name", "Test Item")]
    s = payfast.param_string(pairs, "pass phrase")
    assert s == "merchant_id=10000100&merchant_key=46f0cd694581a&amount=100.00&item_name=Test+Item&passphrase=pass+phrase"
    assert len(payfast.sign(pairs, "x")) == 32


def test_checkout_fields_are_signed():
    cfg = payfast.PayfastConfig("payfast", "10000100", "46f0cd694581a", "jt7NOE43FZPn", True, "https://example.com")
    out = payfast.build_checkout(cfg, m_payment_id="SR-1-P1", amount_cents=123456, item_name="Ride", item_description="d",
                                 customer_name="Thandi Mokoena", email="t@example.com", return_url="https://e/r",
                                 cancel_url="https://e/c", notify_url="https://e/n", booking_ref="SR-1")
    assert out["action"] == "https://sandbox.payfast.co.za/eng/process"
    fields = dict(out["fields"])
    assert fields["amount"] == "1234.56" and fields["name_first"] == "Thandi" and fields["name_last"] == "Mokoena"
    unsigned = [(k, v) for k, v in out["fields"] if k != "signature"]
    assert fields["signature"] == payfast.sign(unsigned, cfg.passphrase)
