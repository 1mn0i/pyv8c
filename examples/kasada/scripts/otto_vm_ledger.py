"""Pair every host call with the exact instruction index, inside the VM's own realm.

The tracer records ``(pc, opcode)`` per dispatched instruction into ``self.__ops``; the
ledger below wraps the host boundary and stores ``self.__ops.length`` at call time, so
the two streams share one counter and need no clock.
"""

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from loguru import logger

from pyv8c import Browser, register_transport
from pyv8c.network.curl_cffi import CurlCffiTransport

URL = "https://www.otto.de/"

TRACER = (
    "function p(e){var _v=t[e.r[0]++]>>5;try{"
    "var _c=self.__c||(self.__c=0);"
    "if(e.__id===undefined){e.__id=(++_c)}self.__c=_c;"
    "var _s=(self.__seq=(self.__seq||0)+1);"
    "(self.__ops||(self.__ops=[])).push(_s*16777216+((e.r[0]&4095)<<8)+(_v&255))"
    "}catch(x){}return _v}"
)
PATTERN = r'function p\(e\)\{return t\[e\.r\[0\]\+\+\]>>5\}'

OPERAND_HOOK = (
    "function X(e){var _r=U(t,e.r,g,P);try{"
    "var _s=(self.__seq=(self.__seq||0)+1);"
    "(self.__x||(self.__x=[])).push(_s*16777216+(e.r[0]&0xFFFF))"
    "}catch(x){}return _r}"
)
OPERAND_PATTERN = r'function X\(e\)\{return U\(t,e\.r,g,P\)\}'


LEDGER = """
(function(){
  try {
    try {
      var store = window.sessionStorage;
      store.setItem('__loads', String((Number(store.getItem('__loads')) || 0) + 1));
    } catch (e) {}
    var push = function (label, detail, result) {
      var ops = self.__ops;
      var ledger = self.__ledger || (self.__ledger = []);
      if (ledger.length > 4000) { return; }
      var state = 'value';
      try {
        if (result === undefined) { state = 'undefined'; }
        else if (result === null) { state = 'null'; }
      } catch (e) {}
      ledger.push([ops ? ops.length : -1, label, String(detail).slice(0, 24), state]);
    };
    var wrap = function (owner, method, label) {
      try {
        if (!owner || typeof owner[method] !== 'function') { return; }
        var original = owner[method];
        owner[method] = function () {
          var result = original.apply(this, arguments);
          try { push(label, arguments[0], result); } catch (e) {}
          return result;
        };
      } catch (e) {}
    };
    if (window.Permissions) { wrap(Permissions.prototype, 'query', 'permissions.query'); }
    if (window.CanvasRenderingContext2D) {
      wrap(CanvasRenderingContext2D.prototype, 'fillText', 'fillText');
      wrap(CanvasRenderingContext2D.prototype, 'measureText', 'measureText');
      wrap(CanvasRenderingContext2D.prototype, 'getImageData', 'getImageData');
    }
    if (window.HTMLCanvasElement) {
      wrap(HTMLCanvasElement.prototype, 'toDataURL', 'toDataURL');
      wrap(HTMLCanvasElement.prototype, 'getContext', 'getContext');
    }
    if (window.WebGLRenderingContext) {
      wrap(WebGLRenderingContext.prototype, 'getParameter', 'getParameter');
      wrap(WebGLRenderingContext.prototype, 'getExtension', 'getExtension');
    }
    if (window.crypto) { wrap(crypto, 'getRandomValues', 'getRandomValues'); }
    if (navigator) {
      wrap(navigator, 'getBattery', 'getBattery');
      if (navigator.mediaDevices) {
        wrap(navigator.mediaDevices, 'enumerateDevices', 'enumerateDevices');
      }
      if (navigator.storage) { wrap(navigator.storage, 'estimate', 'storage.estimate'); }
      if (navigator.gpu) { wrap(navigator.gpu, 'requestAdapter', 'gpu.requestAdapter'); }
    }
    // Property reads: reinstall the same getter on the prototype and record reads that
    // answer undefined.  The receiver keeps its identity, so the SDK cannot tell.
    var record = function (proto, name, label) {
      try {
        var descriptor = Object.getOwnPropertyDescriptor(proto, name);
        if (!descriptor || typeof descriptor.get !== 'function') { return; }
        var original = descriptor.get;
        Object.defineProperty(proto, name, {
          configurable: descriptor.configurable,
          enumerable: descriptor.enumerable,
          get: function () {
            var value = original.call(this);
            if (value === undefined) {
              try {
                var ops = self.__ops;
                var ledger = self.__ledger || (self.__ledger = []);
                if (ledger.length < 6000) {
                  ledger.push([ops ? ops.length : -1, label + '.' + name, '', 'undefined-read']);
                }
              } catch (e) {}
            }
            return value;
          }
        });
      } catch (e) {}
    };
    var watching = [
      [window.Screen && Screen.prototype, 'screen',
       ['width', 'height', 'availWidth', 'availHeight', 'availTop', 'availLeft',
        'colorDepth', 'pixelDepth']],
      [window.Navigator && Navigator.prototype, 'navigator',
       ['userAgent', 'appVersion', 'platform', 'vendor', 'language', 'languages',
        'hardwareConcurrency', 'deviceMemory', 'maxTouchPoints', 'doNotTrack',
        'webdriver', 'onLine', 'productSub', 'product']],
      [window.Performance && Performance.prototype, 'performance',
       ['timeOrigin', 'memory', 'navigation', 'timing']],
      [window.VisualViewport && VisualViewport.prototype, 'visualViewport',
       ['width', 'height', 'scale', 'offsetTop', 'offsetLeft', 'pageTop', 'pageLeft']]
    ];
    for (var group = 0; group < watching.length; group++) {
      var proto = watching[group][0];
      var prefix = watching[group][1];
      var names = watching[group][2];
      if (!proto) { continue; }
      for (var index = 0; index < names.length; index++) {
        record(proto, names[index], prefix);
      }
    }
  } catch (e) {}
})();
"""


def intercept(url, request):
    parts = urlsplit(str(url))
    kind = request.get("resource_type", "")
    path = parts.path
    is_otto = (parts.hostname or "").lower() == "www.otto.de"
    challenge = is_otto and any(path.endswith("/" + suffix)
                                for suffix in ("p.js", "rc.js", "mfc", "fp", "ips.js", "tl"))
    document = is_otto and path == "/" and kind == "document"
    allow = challenge or document or parts.scheme in ("about", "data", "blob", "javascript")
    return None if allow else False


IPS_LOADS = []


class Observed(CurlCffiTransport):
    def _call(self, request):
        response = super()._call(request)
        parts = urlsplit(request.url)
        if parts.path.endswith("/ips.js"):
            IPS_LOADS.append(request.url[:80])
            text, traced = re.subn(PATTERN, TRACER, response.text, count=1)
            text, dumped = re.subn(
                r'(var (\w+)=A\(F,"[^"]+",\d+\))',
                lambda m: m.group(1) + ";try{self.__vm=" + m.group(2) + "}catch(e){}",
                text, count=1)
            text, operands = re.subn(OPERAND_PATTERN, OPERAND_HOOK, text, count=1)
            print("tracer patched:", traced, "program dumped:", dumped,
                  "operand patched:", operands, flush=True)
            response.body = (LEDGER + text).encode("utf8")
            return response
        if (parts.hostname or "").lower() == "www.otto.de" and parts.path.endswith("/tl"):
            try:
                body = response.json()
            except (ValueError, TypeError):
                body = None
            print("tl response:", response.status, body, flush=True)
        return response


register_transport("otto_vm_ledger", lambda *, options=None: Observed(options=options),
                   option_keys=Observed.accepted_option_keys, proxy=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--proxy", default="http://127.0.0.1:7890")
    args = parser.parse_args()

    logger.remove()
    browser = Browser(proxy=args.proxy, cache=False, threaded_loaders=True,
                      resource_interceptor=intercept, http_engine="otto_vm_ledger",
                      http_options={"impersonate": "chrome150", "timeout": 25},
                      ws_engine=None, stub_logging="off")
    try:
        page = browser.new_profile().new_page()
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=45)
        except BaseException as error:
            print("navigation:", type(error).__name__, str(error)[:100], flush=True)
        best = "none"
        for _round in range(args.timeout):
            time.sleep(1.0)
            current = page.evaluate("""(() => {
                let best = 'none';
                let size = 0;
                for (const frame of Array.from(document.querySelectorAll('iframe'))) {
                    const view = frame.contentWindow;
                    if (view && view.__ops && view.__ops.length > size) {
                        size = view.__ops.length;
                        try {
                            best = JSON.stringify({
                                ledger: view.__ledger || [],
                                ops: view.__ops.slice(-4000),
                                ends: (view.__x || []).slice(-8000),
                                program: view.__vm ? view.__vm.slice(0, 80000) : []
                            });
                        } catch (error) {}
                    }
                }
                return best;
            })()""")
            if current and current != "none" and len(current) > len(best):
                best = current
            try:
                loads = page.evaluate("window.sessionStorage.getItem('__loads')")
                print("frame loads:", loads, flush=True)
            except Exception:
                pass
            if best and len(best) > 60000:
                break
        if best and best != "none":
            Path(__import__("tempfile").gettempdir(), "otto_vm_ledger.json").write_text(
                best, encoding="utf8")
            print("ledger captured:", len(best), flush=True)
    finally:
        print("ips.js fetches:", len(IPS_LOADS), flush=True)
        browser.close()


main()
