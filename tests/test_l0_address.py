from services.ingest.normalize.address import decode_mime_words, norm_email, parse_addresses


def test_norm_email_lowercase_and_plus_stripping():
    assert norm_email("A+tag@Example.com") == "a@example.com"
    assert norm_email("  Bob@Foo.COM ") == "bob@foo.com"


def test_parse_single_address():
    addrs = parse_addresses("Jenna Park <j.park@acme.com>")
    assert len(addrs) == 1
    assert addrs[0].email == "j.park@acme.com"
    assert addrs[0].display_names == ["Jenna Park"]


def test_parse_empty():
    assert parse_addresses("") == []


def test_parse_multiple():
    addrs = parse_addresses("A <a@x.com>, B <b@y.com>")
    assert [a.email for a in addrs] == ["a@x.com", "b@y.com"]


def test_rfc2047_decode():
    encoded = "=?utf-8?q?Jenna_Park?= <j.park@acme.com>"
    addrs = parse_addresses(encoded)
    assert addrs[0].display_names == ["Jenna Park"]
    assert decode_mime_words("=?utf-8?q?Jenna_Park?=") == "Jenna Park"
