"""Cookie jar behaviour for response cookies, including merged Set-Cookie lines."""

import pytest

pytest.importorskip("pyv8c.pyv8", reason="native pyv8 extension is not importable")
pytestmark = pytest.mark.native

from pyv8c.network.cookies import CookieJar, split_set_cookie

URL = "https://www.cde.org.cn/main/xxgk/listpage/9f9c74c73e0f8f56a8bfbc646055026d"
MERGED = (
    "FSSBBIl1UgzbN7N80T=3ns3u6S1U.aOa4_9z2ZN5rY; Path=/; expires=Sat, 13 Sep 2036 01:50:31 GMT, "
    "FSSBBIl1UgzbN7N80S=n76Po_UpRTKrVQx3CacwQhG; Path=/; expires=Sat, 13 Sep 2036 01:50:31 GMT; HttpOnly"
)


def test_split_set_cookie_separates_cookies_but_keeps_date_commas():
    parts = split_set_cookie(MERGED)
    assert [part.strip().split("=", 1)[0] for part in parts] == [
        "FSSBBIl1UgzbN7N80T", "FSSBBIl1UgzbN7N80S"]
    assert split_set_cookie("A=1; Path=/") == ["A=1; Path=/"]


def test_jar_stores_every_cookie_from_a_merged_set_cookie_line():
    jar = CookieJar()
    jar.add_from_response(URL, MERGED)
    names = {cookie.split("=", 1)[0] for cookie in jar.header_for(URL).split("; ") if cookie}
    assert names == {"FSSBBIl1UgzbN7N80T", "FSSBBIl1UgzbN7N80S"}


def test_secure_cookie_prefixes_and_samesite_none_require_secure():
    jar = CookieJar()
    url = "https://example.test/"
    for header in ("__Secure-no=1", "__Host-no=1; Secure; Path=/sub",
                   "__Host-domain=1; Secure; Path=/; Domain=example.test",
                   "none=1; SameSite=None"):
        jar.add_from_response(url, header)
    jar.add_from_response(url, "__Secure-yes=1; Secure")
    jar.add_from_response(url, "__Host-yes=1; Secure; Path=/")
    jar.add_from_response(url, "none=2; SameSite=None; Secure")
    assert set(jar.header_for(url).split("; ")) == {
        "__Secure-yes=1", "__Host-yes=1", "none=2"}


def test_domain_cookies_cannot_claim_a_public_or_private_suffix():
    jar = CookieJar()
    jar.add_from_response("https://evil.github.io/", "bad=1; Domain=github.io; Path=/")
    jar.add_from_response("https://a.example.co.uk/", "bad2=1; Domain=co.uk; Path=/")
    jar.add_from_response("https://a.example.test/", "bad3=1; Domain=test; Path=/")
    jar.add_from_response("https://a.example.co.uk/", "valid=1; Domain=example.co.uk; Path=/")
    assert jar.header_for("https://other.github.io/") == ""
    assert jar.header_for("https://b.example.test/") == ""
    assert jar.header_for("https://b.example.co.uk/") == "valid=1"


def test_jar_marks_the_dropped_cookie_http_only():
    jar = CookieJar()
    jar.add_from_response(URL, MERGED)
    # ``FSSBBIl1UgzbN7N80S`` arrives with HttpOnly, so script reads must not see
    # it even though the network layer keeps it for the next request.
    assert "FSSBBIl1UgzbN7N80S" in jar.header_for(URL)
    assert "FSSBBIl1UgzbN7N80S" not in jar.document_for(URL)


def test_jar_still_accepts_single_and_list_forms():
    jar = CookieJar()
    jar.add_from_response(URL, "A=1; Path=/")
    jar.add_from_response(URL, ["B=2", "C=3"])
    assert {cookie.split("=", 1)[0] for cookie in jar.header_for(URL).split("; ")} == {"A", "B", "C"}


def test_a_cookie_with_a_partitioned_attribute_is_kept():
    """``Partitioned`` (CHIPS) must not make the whole line unparsable.

    Cloudflare's ``cf_clearance`` arrives as
    ``cf_clearance=...; HttpOnly; SameSite=None; Partitioned; Secure; Path=/; Domain=...;
    Expires=Wed, 03 Nov 2027 ...``.  ``http.cookies.SimpleCookie`` answered an empty
    morsel set for exactly that shape and the jar stored nothing, silently, which cost
    a challenge its clearance (measured 2026-11-03).
    """
    header = ("cf_clearance=abc123-1793687011-1.2.1.1-XyZ; HttpOnly; SameSite=None; "
              "Partitioned; Secure; Path=/; Domain=www.cde.org.cn; "
              "Expires=Wed, 03 Nov 2027 06:42:09 GMT")
    jar = CookieJar()
    jar.add_from_response(URL, header)
    assert "cf_clearance=" in jar.header_for(URL)
    #: HttpOnly: the wire sends it, a script may not read it.
    assert "cf_clearance=" not in jar.document_for(URL)


def test_a_value_with_spaces_is_kept_instead_of_dropping_the_line():
    jar = CookieJar()
    jar.add_from_response(URL, "A=one two; Path=/")
    assert "A=one two" in jar.header_for(URL)
