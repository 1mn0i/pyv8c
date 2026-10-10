"""Which flavour of challenge the edge hands out, by transport fingerprint alone.

    python script/flavour_probe.py

Chrome gets the JSD chain every round (4/4, measured) and clears; our engine gets the managed
challenge (Turnstile) every round (0/5) and never does.  The flavour is decided by the server
at the first ``403``: that body names the chain it wants -- ``orchestrate/chl_page/v1`` for the
managed one, ``scripts/jsd/main.js`` for JSD.

Same URL, same headers, one variable: the curl_cffi profile.  A different flavour means the
signal lives in TLS; the same flavour means it is somewhere else (and this probe has then ruled
TLS out, which is worth as much).
"""
import sys

import curl_cffi.requests

URL = "https://steamdb.info/"

#: Exactly the headers our engine's navigation sends (measured with the transport witness).
HEADERS = {
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
               "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"),
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"),
    "sec-ch-ua": '"Chromium";v="154", "Google Chrome";v="154", "Not A(Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def flavour(body):
    if "scripts/jsd" in body:
        return "jsd"
    if "orchestrate/chl_page" in body:
        return "orchestrate"
    return "other"


def attempt(label, headers=HEADERS, **options):
    """One request with one variable moved.

    ``headers=None`` hands the header set to the impersonation profile -- which is the only
    measured difference left between the two sides' first requests (Chrome sends ``priority``,
    ours did not), and passing our dict would have hidden it.
    """
    try:
        session = curl_cffi.requests.Session(**options)
    except TypeError as error:
        print(f"{label:24} the session refused the options: {str(error)[:60]}")
        return
    try:
        response = session.get(URL, headers=headers, allow_redirects=False, timeout=30)
    except BaseException as error:  # noqa: BLE001
        print(f"{label:24} request failed: {type(error).__name__}: {str(error)[:70]}")
        return
    body = response.text
    version = getattr(response, "http_version", "?")
    print(f"{label:24} status={response.status_code} bytes={len(body):6d} "
          f"http={version} flavour={flavour(body)}")


if __name__ == "__main__":
    sys.stdout.flush()
    attempt("default (no profile)")
    for profile in ("chrome", "chrome150", "chrome136"):
        attempt(f"impersonate={profile}", impersonate=profile)
    #: The other live variable: Chrome's first request carried HTTP/2 pseudo-headers and ours
    #: did not, and the three attempts above all came back over HTTP/3.  curl_cffi names the
    #: option ``http_version`` (the engine's transport forwards it too).
    try:
        from curl_cffi.const import CurlHttpVersion

        forced = CurlHttpVersion.V2_0
    except Exception:  # noqa: BLE001 - older spellings take the string
        forced = "v2"
    attempt("chrome + h2", impersonate="chrome", http_version=forced)
    attempt("chrome + h3", impersonate="chrome", http_version="v3")
    #: The profile's own header set, with our dict out of the way: this is where a Chrome
    #: ``priority`` header (and anything else curl_cffi adds) gets its chance to matter.
    attempt("profile headers only", headers=None, impersonate="chrome")
    attempt("profile headers only 150", headers=None, impersonate="chrome150")
