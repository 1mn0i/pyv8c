"""用 pyv8c 执行当次 hCaptcha SDK；返回服务端签发的 P1_ Token。"""
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pyv8c"))

from Crypto.Cipher import AES
from loguru import logger
from pyv8c import Browser, pyv8, register_transport
from pyv8c.network.curl_cffi import CurlCffiTransport
from pyv8c.web.bom.crypto import SubtleCrypto, _name_of, _options
from pyv8c.web.encoding import _bytes_of
from pyv8c.web.page_errors import dom_exception

URL = "https://dashboard.hcaptcha.com/login"
SITEKEY = "e4b28873-6852-49c0-9784-7231f004b96b"
PROXY = "http://127.0.0.1:6696"


class _Transport(CurlCffiTransport):
    def _call(self, request):
        response = super()._call(request)
        target = urlsplit(request.url)
        if target.path.endswith(("/api.js", "/hsw.js", "/hcaptcha.html")):
            logger.info("{} {} -> HTTP {}, {} B", request.method, request.url,
                        response.status, len(response.content))
        elif target.path == "/checksiteconfig" and request.method == "POST":
            try:
                config = response.json()
            except ValueError:
                config = {}
            challenge = config.get("c", {})
            version = parse_qs(target.query).get("v", [""])[0]
            logger.info("checksiteconfig HTTP {}, v={}, pass={}, type={}",
                        response.status, version, config.get("pass"), challenge.get("type"))
            logger.info("req={}", challenge.get("req", ""))
        elif target.path.startswith("/getcaptcha/") and request.method == "POST":
            body = request.body.encode() if isinstance(request.body, str) else request.body
            logger.info("POST {} -> HTTP {}, request={} B, response={} B",
                        request.url, response.status, len(body or b""), len(response.content))
        return response


def _gcm(subtle, algorithm, key, data, *, decrypt=False):
    """补充框架尚未实现的标准 AES-GCM，不替换 SDK 或指纹。"""
    operation = "decrypt" if decrypt else "encrypt"
    if _name_of(algorithm) != "AES-GCM":
        return getattr(SubtleCrypto, "fn_" + operation)(subtle, algorithm, key, data)
    key, error = subtle._key_for(key, operation)
    if error is not None:
        return subtle._settle(error=error)
    if key.get_algorithm().get("name") != "AES-GCM":
        return subtle._settle(error=dom_exception("InvalidAccessError", "Wrong key algorithm"))
    options = _options(algorithm)
    try:
        bits = int(options.get("tagLength", 128))
        if bits not in (32, 64, 96, 104, 112, 120, 128):
            raise ValueError("Invalid AES-GCM tagLength")
        cipher = AES.new(key._material, AES.MODE_GCM,
                         nonce=_bytes_of(options["iv"]), mac_len=bits // 8)
        if "additionalData" in options:
            cipher.update(_bytes_of(options["additionalData"]))
        payload = _bytes_of(data)
        if decrypt:
            result = cipher.decrypt_and_verify(payload[:-bits // 8], payload[-bits // 8:])
        else:
            encrypted, tag = cipher.encrypt_and_digest(payload)
            result = encrypted + tag
        return subtle._settle(result)
    except (ValueError, TypeError) as error:
        return subtle._settle(error=dom_exception("OperationError", str(error)))


def _decrypt(subtle, algorithm, key, data):
    return _gcm(subtle, algorithm, key, data, decrypt=True)


def _intercept(url, request):
    target = urlsplit(url)
    host, path = target.hostname or "", target.path
    if target.scheme not in ("http", "https"):
        return None
    allowed = (
        (host == "js.hcaptcha.com" and path == "/1/api.js")
        or (host == "newassets.hcaptcha.com"
            and path.endswith(("/hcaptcha.html", "/hsw.js")))
        or (host == "api.hcaptcha.com"
            and (path == "/checksiteconfig" or path.startswith("/getcaptcha/")))
        or (host.endswith(".w.hcaptcha.com") and path == "/logo.png")
    )
    return None if allowed else False


def run(timeout=60, proxy=PROXY):
    logger.info("url={}, sitekey={}", URL, SITEKEY)
    register_transport("hcaptcha-observed", _Transport, proxy=True)
    browser = Browser(
        proxy=proxy, cache=False, threaded_loaders=True, resource_interceptor=_intercept,
        http_engine="hcaptcha-observed",
        http_options={"impersonate": "chrome150", "timeout": 20},
        interface_overrides={"SubtleCrypto": {"encrypt": _gcm, "decrypt": _decrypt}},
    )
    try:
        page = browser.new_profile().new_page()
        page.set_content('''<!doctype html><html><body><div id="captcha"></div><script>
          globalThis.result = {token:null, error:null};
          addEventListener('error', e => result.error = String(e.message || 'Script load failed'));
          addEventListener('unhandledrejection', e => result.error = String(e.reason));
          function ready() {
            const id = hcaptcha.render('captcha', {
              sitekey:SITEKEY, size:'invisible',
              'error-callback': e => result.error = String(e)
            });
            hcaptcha.execute(id, {async:true}).then(
              value => result.token = value.response,
              error => result.error = String(error));
          }
          </script><script src="https://js.hcaptcha.com/1/api.js?onload=ready&amp;render=explicit&amp;uj=true"></script>
          </body></html>'''.replace("sitekey:SITEKEY", "sitekey:" + json.dumps(SITEKEY)), url=URL)
        page.wait_for("result.token !== null || result.error !== null", timeout=timeout)
        result = json.loads(page.evaluate("JSON.stringify(result)"))
        if not isinstance(result["token"], str) or not result["token"].startswith("P1_"):
            raise RuntimeError(result["error"] or "本次未通过无感校验，未取得 P1_ Token")
        logger.success("Token prefix=P1_, length={}", len(result["token"]))
        return result["token"]
    finally:
        browser.close()


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}",
               filter=lambda record: record["name"] == __name__)
    try:
        print(run(), flush=True)
    except (Exception, pyv8.JSException) as error:
        logger.error("{}", error)
        raise SystemExit(1)
