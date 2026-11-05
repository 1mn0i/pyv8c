"""Execute OTTO's live Kasada flow with pyv8c and report the /tl response.

The remote scripts generate the current fingerprint and payload. No HAR, captured
payload, browser automation or fixed challenge identifiers are used at runtime.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

import pyv8
from pyv8.trace import format_trace
from loguru import logger
from pyv8c import Browser, register_transport
from pyv8c.web.dom.document import Document
from pyv8c.network.curl_cffi import CurlCffiTransport

URL = 'https://www.otto.de/'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proxy', default='http://127.0.0.1:7890')
    parser.add_argument('--timeout', type=float, default=75)
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--trace-ips-errors', action='store_true',
                        help='summarize failed binding operations in ips.js')
    parser.add_argument('--allow-site-scripts', action='store_true',
                        help='also load OTTO scripts while diagnosing dependencies')
    parser.add_argument('--allow-site-resources', action='store_true',
                        help='also load OTTO resources while diagnosing dependencies')
    args = parser.parse_args()
    if not args.verbose:
        logger.remove()
    completed = threading.Event()
    results = []
    counts = {'allowed': 0, 'blocked': 0}
    errors = {}
    error_examples = {}

    def trace(event, receiver, name, arguments, value, error, is_construct, context):
        if event == 'method' and name == 'createTextNode' and arguments:
            raw = str(arguments[0])
            if raw.startswith('__OTTO_CAPTURE__'):
                payload = raw[len('__OTTO_CAPTURE__'):]
                json.loads(payload)
                Path(__import__('tempfile').gettempdir(), 'otto_pyv8c_closure_sample.json').write_text(payload, encoding='utf8')
                print('closure payload captured:',len(payload),flush=True)

        if error is not pyv8.Undefined and error is not None:
            key = (str(context), str(event), type(receiver).__name__, str(name), type(error).__name__)
            errors[key] = errors.get(key, 0) + 1
            if key not in error_examples:
                error_examples[key] = str(format_trace(event, receiver, name, arguments, value, error,
                                                       is_construct, context))[:500]

    def intercept(url, request):
        parts = urlsplit(str(url))
        kind = request.get('resource_type', '')
        path = parts.path
        is_otto = (parts.hostname or '').lower() == 'www.otto.de'
        challenge = is_otto and any(path.endswith('/' + suffix)
                                    for suffix in ('p.js', 'rc.js', 'mfc', 'fp', 'ips.js', 'tl'))
        document = is_otto and path == '/' and kind == 'document'
        hostname = (parts.hostname or '').lower()
        other_script = (args.allow_site_scripts and kind == 'script'
                        and (hostname == 'otto.de' or hostname.endswith('.otto.de')))
        site_resource = (args.allow_site_resources
                         and (hostname == 'otto.de' or hostname.endswith('.otto.de'))
                         and kind not in ('image', 'media'))
        allow = challenge or document or other_script or site_resource or parts.scheme in ('about', 'data', 'blob', 'javascript')
        counts['allowed' if allow else 'blocked'] += 1
        if challenge:
            print('request:', request.get('method', 'GET'), path.rsplit('/', 1)[-1], flush=True)
        return None if allow else False

    class ObservedTransport(CurlCffiTransport):
        def _call(self, request):
            response = super()._call(request)
            if urlsplit(request.url).path.endswith('/ips.js'):
                source=response.text
                observer = 'function __observe(fn,receiver,args){try{let v=args&&args[0];if(typeof fn===\'function\'&&fn.name===\'encode\'&&typeof v===\'string\'&&v.length>10000&&v.startsWith(\'[\\"2:\'))document.createTextNode(\'__OTTO_CAPTURE__\'+v);try{document.createTextNode(\'__OTTO_HITS__\'+JSON.stringify((self.__hits||[]).slice(0,80)))}catch(e){}}catch(e){}}function __hitHook(){try{var pairs=[["XMLHttpRequest", "setAttributionReporting"], ["WebGLRenderingContext", "texElementImage2D"], ["WebGLRenderingContext", "texElementSubImage2D"], ["WebGL2RenderingContext", "texElementImage2D"], ["WebGL2RenderingContext", "texElementSubImage2D"], ["VTTCue", "lineAlign"], ["VTTCue", "positionAlign"], ["VTTCue", "region"], ["URLPattern", "generate"], ["TextMetrics", "emHeightAscent"], ["TextMetrics", "emHeightDescent"], ["TextMetrics", "getActualBoundingBox"], ["TextMetrics", "getIndexFromOffset"], ["TextMetrics", "getSelectionRects"], ["TextMetrics", "getTextClusters"], ["Text", "convertPointFromNode"], ["Text", "convertQuadFromNode"], ["Text", "convertRectFromNode"], ["Text", "getBoxQuads"], ["SourceBuffer", "appendEncodedChunks"], ["SourceBuffer", "audioTracks"], ["SourceBuffer", "trackDefaults"], ["SourceBuffer", "videoTracks"], ["SecurityPolicyViolationEvent", "evalHash"], ["SecurityPolicyViolationEvent", "urlHash"], ["Scheduler", "asyncData"], ["SVGTextPathElement", "side"], ["SVGPathElement", "getPathData"], ["SVGPathElement", "setPathData"], ["SVGElement", "onbeforefilter"], ["SVGElement", "oncomplete"], ["SVGElement", "onoverscrollcancel"], ["SVGElement", "onoverscrollchanging"], ["SVGElement", "onoverscrollend"], ["SVGElement", "onoverscrollstart"], ["SVGElement", "ontouchcancel"], ["SVGElement", "ontouchend"], ["SVGElement", "ontouchmove"], ["SVGElement", "ontouchstart"], ["Request", "getRetryOptions"], ["RTCEncodedVideoFrame", "setMetadata"], ["RTCEncodedAudioFrame", "setMetadata"], ["Permissions", "request"], ["Permissions", "requestAll"], ["Permissions", "revoke"], ["PerformanceResourceTiming", "initiatorUrl"], ["PerformanceEventTiming", "targetSelector"], ["Performance", "bind"], ["Performance", "getSpeculations"], ["Performance", "markConditional"], ["Performance", "measureConditional"], ["Performance", "measureUserAgentSpecificMemory"], ["OffscreenCanvasRenderingContext2D", "beginLayer"], ["OffscreenCanvasRenderingContext2D", "createMesh2DIndexBuffer"], ["OffscreenCanvasRenderingContext2D", "createMesh2DUVBuffer"], ["OffscreenCanvasRenderingContext2D", "createMesh2DVertexBuffer"], ["OffscreenCanvasRenderingContext2D", "drawElementImage"], ["OffscreenCanvasRenderingContext2D", "drawMesh"], ["OffscreenCanvasRenderingContext2D", "endLayer"], ["OffscreenCanvasRenderingContext2D", "fillTextCluster"], ["OffscreenCanvasRenderingContext2D", "globalHDRHeadroom"], ["OffscreenCanvasRenderingContext2D", "strokeTextCluster"], ["OffscreenCanvas", "clearElementGeometry"], ["OffscreenCanvas", "getElementTransform"], ["OffscreenCanvas", "updateElementGeometry"], ["NetworkInformation", "downlinkMax"], ["NetworkInformation", "ontypechange"], ["NetworkInformation", "type"], ["Navigator", "adAuctionComponents"], ["Navigator", "canLoadAdAuctionFencedFrame"], ["Navigator", "clearOriginJoinedAdInterestGroups"], ["Navigator", "contacts"], ["Navigator", "createAdRequest"], ["Navigator", "createAuctionNonce"], ["Navigator", "createHandwritingRecognizer"], ["Navigator", "deprecatedReplaceInURN"], ["Navigator", "deprecatedRunAdAuctionEnforcesKAnonymity"], ["Navigator", "deprecatedURNToURL"], ["Navigator", "finalizeAd"], ["Navigator", "getInterestGroupAdAuctionData"], ["Navigator", "globalPrivacyControl"], ["Navigator", "install"], ["Navigator", "joinAdInterestGroup"], ["Navigator", "leaveAdInterestGroup"], ["Navigator", "lockedMode"], ["Navigator", "ml"], ["Navigator", "playHaptics"], ["Navigator", "preferences"], ["Navigator", "protectedAudience"], ["Navigator", "queryHandwritingRecognizer"], ["Navigator", "rtc"], ["Navigator", "runAdAuction"], ["Navigator", "smartCard"], ["Navigator", "updateAdInterestGroups"], ["NavigateEvent", "deferPageSwap"], ["MessagePort", "onclose"], ["MediaStreamTrack", "onconfigurationchange"], ["MediaSource", "handle"], ["MathMLElement", "onbeforefilter"], ["MathMLElement", "oncomplete"], ["MathMLElement", "onoverscrollcancel"], ["MathMLElement", "onoverscrollchanging"], ["MathMLElement", "onoverscrollend"], ["MathMLElement", "onoverscrollstart"], ["MathMLElement", "ontouchcancel"], ["MathMLElement", "ontouchend"], ["MathMLElement", "ontouchmove"], ["MathMLElement", "ontouchstart"], ["KeyframeEffect", "iterationComposite"], ["HTMLTemplateElement", "buffer"], ["HTMLTemplateElement", "crossOrigin"], ["HTMLTemplateElement", "integrity"], ["HTMLTemplateElement", "referrerPolicy"], ["HTMLTemplateElement", "sanitize"], ["HTMLTemplateElement", "shadowRootAdoptedStyleSheets"], ["HTMLTemplateElement", "src"], ["HTMLSelectElement", "selectedContentElement"], ["HTMLScriptElement", "attributionSrc"], ["HTMLScriptElement", "cacheHint"], ["HTMLScriptElement", "signature"], ["HTMLMediaElement", "audioTracks"], ["HTMLMediaElement", "latencyHint"], ["HTMLMediaElement", "videoTracks"], ["HTMLInputElement", "alpha"], ["HTMLInputElement", "capture"], ["HTMLInputElement", "colorSpace"], ["HTMLInputElement", "switch"], ["HTMLImageElement", "attributionSrc"], ["HTMLImageElement", "browsingTopics"], ["HTMLImageElement", "onuareplaceend"], ["HTMLImageElement", "onuareplacestart"], ["HTMLImageElement", "replacedByUserAgent"], ["HTMLIFrameElement", "browsingTopics"], ["HTMLIFrameElement", "connectionAllowlist"], ["HTMLIFrameElement", "permissionsPolicy"], ["HTMLIFrameElement", "policy"], ["HTMLFrameSetElement", "ongamepadrawinputchanged"], ["HTMLFrameSetElement", "onmove"], ["HTMLFrameSetElement", "onorientationchange"], ["HTMLFrameSetElement", "ontimezonechange"], ["HTMLFieldSetElement", "checkable"], ["HTMLElement", "accessKeyLabel"], ["HTMLElement", "hideUnboundedElement"], ["HTMLElement", "onbeforefilter"], ["HTMLElement", "oncomplete"], ["HTMLElement", "onoverscrollcancel"], ["HTMLElement", "onoverscrollchanging"], ["HTMLElement", "onoverscrollend"], ["HTMLElement", "onoverscrollstart"], ["HTMLElement", "ontouchcancel"], ["HTMLElement", "ontouchend"], ["HTMLElement", "ontouchmove"], ["HTMLElement", "ontouchstart"], ["HTMLElement", "overscrollarea"], ["HTMLElement", "overscrollcontainer"], ["HTMLElement", "scrollParent"], ["HTMLElement", "showUnboundedElement"], ["HTMLElement", "unbounded"], ["HTMLCanvasElement", "captureElementImage"], ["HTMLCanvasElement", "clearElementGeometry"], ["HTMLCanvasElement", "configureHighDynamicRange"], ["HTMLCanvasElement", "getElementTransform"], ["HTMLCanvasElement", "layoutSubtree"], ["HTMLCanvasElement", "onpaint"], ["HTMLCanvasElement", "requestPaint"], ["HTMLCanvasElement", "updateElementGeometry"], ["HTMLBodyElement", "ongamepadrawinputchanged"], ["HTMLBodyElement", "onmove"], ["HTMLBodyElement", "onorientationchange"], ["HTMLBodyElement", "ontimezonechange"], ["HTMLAreaElement", "attributionSrc"], ["HTMLAreaElement", "hreflang"], ["HTMLAreaElement", "type"], ["HTMLAnchorElement", "attributionSrc"], ["GamepadButton", "type"], ["Gamepad", "touchEvents"], ["ElementInternals", "ariaOwnsElements"], ["ElementInternals", "behaviors"], ["ElementInternals", "setToolParamSchema"], ["Element", "ariaOwnsElements"], ["Element", "computedName"], ["Element", "computedRole"], ["Element", "containerTiming"], ["Element", "containerTimingIgnore"], ["Element", "convertPointFromNode"], ["Element", "convertQuadFromNode"], ["Element", "convertRectFromNode"], ["Element", "getBoxQuads"], ["Element", "getCanvasTransform"], ["Element", "headingOffset"], ["Element", "headingReset"], ["Element", "matchContainer"], ["Element", "setCanvasTransform"], ["Document", "browsingTopics"], ["Document", "convertPointFromNode"], ["Document", "convertQuadFromNode"], ["Document", "convertRectFromNode"], ["Document", "getBoxQuads"], ["Document", "onautofill"], ["Document", "onbeforefilter"], ["Document", "oncomplete"], ["Document", "onoverscrollcancel"], ["Document", "onoverscrollchanging"], ["Document", "onoverscrollend"], ["Document", "onoverscrollstart"], ["Document", "ontouchcancel"], ["Document", "ontouchend"], ["Document", "ontouchmove"], ["Document", "ontouchstart"], ["Document", "permissionsPolicy"], ["Document", "requestStorageAccessFor"], ["Document", "setSequentialFocusStartingPoint"], ["Document", "spellCheckCustomDictionary"], ["CanvasRenderingContext2D", "beginLayer"], ["CanvasRenderingContext2D", "createMesh2DIndexBuffer"], ["CanvasRenderingContext2D", "createMesh2DUVBuffer"], ["CanvasRenderingContext2D", "createMesh2DVertexBuffer"], ["CanvasRenderingContext2D", "drawElementImage"], ["CanvasRenderingContext2D", "drawMesh"], ["CanvasRenderingContext2D", "endLayer"], ["CanvasRenderingContext2D", "fillTextCluster"], ["CanvasRenderingContext2D", "globalHDRHeadroom"], ["CanvasRenderingContext2D", "strokeTextCluster"], ["CanvasGradient", "colorInterpolationMethod"], ["CanvasGradient", "hueInterpolationMethod"], ["CanvasGradient", "premultipliedAlpha"], ["CSPViolationReportBody", "evalHash"], ["CSPViolationReportBody", "urlHash"], ["AnimationTimeline", "getCurrentTime"], ["AbstractRange", "endContainer"], ["AbstractRange", "startContainer"], ["AudioWorklet", "port"], ["GPUAdapterInfo", "backend"], ["GPUAdapterInfo", "d3dShaderModel"], ["GPUAdapterInfo", "driver"], ["GPUAdapterInfo", "memoryHeaps"], ["GPUAdapterInfo", "powerPreference"], ["GPUAdapterInfo", "subgroupMatrixConfigs"], ["GPUAdapterInfo", "type"], ["GPUAdapterInfo", "vkDriverVersion"], ["GPUBuffer", "mapSync"], ["GPUCommandEncoder", "writeTimestamp"], ["GPUComputePassEncoder", "setResourceTable"], ["GPUDevice", "createResourceTable"], ["GPUExternalTexture", "isZeroCopy"], ["GPUQueue", "copyElementImageToTexture"], ["GPUQueue", "drawElementImageToTexture"], ["GPURenderPassEncoder", "multiDrawIndexedIndirect"], ["GPURenderPassEncoder", "multiDrawIndirect"], ["MediaDevices", "getAllScreensMedia"], ["MediaDevices", "selectAudioOutput"], ["MediaDevices", "setPreferredSinkId"], ["NavigatorManagedData", "getAnnotatedAssetId"], ["NavigatorManagedData", "getAnnotatedLocation"], ["NavigatorManagedData", "getDirectoryId"], ["NavigatorManagedData", "getHostname"], ["NavigatorManagedData", "getSerialNumber"], ["ScreenDetailed", "bluePrimaryX"], ["ScreenDetailed", "bluePrimaryY"], ["ScreenDetailed", "greenPrimaryX"], ["ScreenDetailed", "greenPrimaryY"], ["ScreenDetailed", "hdrHeadroom"], ["ScreenDetailed", "highDynamicRangeHeadroom"], ["ScreenDetailed", "onhdrheadroomchange"], ["ScreenDetailed", "redPrimaryX"], ["ScreenDetailed", "redPrimaryY"], ["ScreenDetailed", "whitePointX"], ["ScreenDetailed", "whitePointY"], ["ServiceWorkerRegistration", "index"], ["SubtleCrypto", "decapsulateBits"], ["SubtleCrypto", "decapsulateKey"], ["SubtleCrypto", "encapsulateBits"], ["SubtleCrypto", "encapsulateKey"], ["SubtleCrypto", "getPublicKey"], ["VideoEncoder", "getAllFrameBuffers"], ["WebTransport", "anticipatedConcurrentIncomingBidirectionalStreams"], ["WebTransport", "anticipatedConcurrentIncomingUnidirectionalStreams"], ["WebTransport", "congestionControl"], ["WebTransport", "createSendGroup"], ["WebTransport", "draining"], ["WebTransport", "getStats"], ["WebTransport", "responseHeaders"], ["Bluetooth", "getDevices"], ["Bluetooth", "onadvertisementreceived"], ["Bluetooth", "requestLEScan"], ["BluetoothDevice", "forget"], ["BluetoothDevice", "onadvertisementreceived"], ["BluetoothDevice", "watchAdvertisements"], ["BluetoothDevice", "watchingAdvertisements"], ["CaptureController", "oncapturedmousechange"], ["FileSystemFileHandle", "createSyncAccessHandle"], ["FileSystemHandle", "getCloudIdentifiers"], ["FileSystemHandle", "getUniqueId"], ["FileSystemHandle", "move"], ["FileSystemObserver", "unobserve"], ["FragmentDirective", "createSelectorDirective"], ["FragmentDirective", "items"], ["LanguageModel", "inputQuota"], ["LanguageModel", "inputUsage"], ["LanguageModel", "measureInputUsage"], ["LanguageModel", "onquotaoverflow"], ["LanguageModel", "samplingMode"], ["LanguageModel", "temperature"], ["LanguageModel", "topK"], ["StorageBucket", "durability"], ["StorageBucket", "locks"], ["XRFrame", "detectedMeshes"], ["XRFrame", "getImageTrackingResults"], ["XRPose", "angularVelocity"], ["XRPose", "linearVelocity"], ["XRSession", "frameRate"], ["XRSession", "getTrackedImageScores"], ["XRSession", "isSystemKeyboardSupported"], ["XRSession", "onframeratechange"], ["XRSession", "supportedFrameRates"], ["XRSession", "updateTargetFrameRate"], ["CSSPseudoElement", "convertPointFromNode"], ["CSSPseudoElement", "convertQuadFromNode"], ["CSSPseudoElement", "convertRectFromNode"], ["CSSPseudoElement", "getBoxQuads"], ["FencedFrameConfig", "setSharedStorageContext"], ["HTMLUserMediaElement", "initialPermissionStatus"], ["HTMLUserMediaElement", "invalidReason"], ["HTMLUserMediaElement", "isValid"], ["HTMLUserMediaElement", "onpromptaction"], ["HTMLUserMediaElement", "onpromptdismiss"], ["HTMLUserMediaElement", "onvalidationstatuschange"], ["HTMLUserMediaElement", "permissionStatus"], ["HTMLUserMediaElement", "type"], ["Notification", "scenario"], ["Notification", "showTrigger"], ["PerformanceLongAnimationFrameTiming", "layoutDuration"], ["PerformanceLongAnimationFrameTiming", "scriptCount"], ["PerformanceLongAnimationFrameTiming", "styleDuration"], ["PerformanceLongAnimationFrameTiming", "userTimingEntries"], ["PerformanceScriptTiming", "forcedLayoutDuration"], ["PerformanceScriptTiming", "forcedStyleDuration"], ["PerformanceScriptTiming", "sourceColumn"], ["PerformanceScriptTiming", "sourceLine"], ["RTCDataChannel", "priority"], ["URLPattern", "compareComponent"], ["TrustedScriptURL", "fromLiteral"], ["TrustedScript", "fromLiteral"], ["TrustedHTML", "fromLiteral"], ["CSS", "animationWorklet"], ["CSS", "layoutWorklet"], ["SubtleCrypto", "supports"], ["IdentityProvider", "register"], ["IdentityProvider", "unregister"], ["LanguageModel", "params"], ["HTMLUserMediaElement", "isTypeSupported"]];var hit=[];for(var i=0;i<pairs.length;i++){var iface=pairs[i][0];var member=pairs[i][1];try{var ctor=self[iface];if(!ctor||!ctor.prototype)continue;var proto=ctor.prototype;if(member in proto)continue;Object.defineProperty(proto,member,{configurable:true,get:function(){hit.push(member);return function(){hit.push(member);return undefined}},set:function(){}})}catch(e){}}self.__hits=hit}catch(e){}}__hitHook();function __patchCalls(src){let out=src.replace(/(?<![\\w$.])(\\w+)\\.apply\\((\\w+),(\\w+)\\)/g,\'(__observe($1,$2,$3),$1.apply($2,$3))\');try{new Function(\'__observe\',out);return out}catch(e){return src}}'
                source, count = re.subn(r'(\(function\(\)\{var\s+\w+;)', lambda m:m.group(1)+observer,source,count=1)
                pattern = r'new Function\((\w+)\+(\w+)\((\w+)\.slice\(0,-1\),(\w+)\)\+"\}"\)\(\)'
                source, n = re.subn(pattern,lambda m:"new Function('__observe',__patchCalls("+m[1]+'+'+m[2]+'('+m[3]+'.slice(0,-1),'+m[4]+')+"}"))(__observe)',source)
                print('closure instrumented:',count,n,flush=True)
                response.body=source.encode('utf8')
            parts = urlsplit(request.url)
            if ((parts.hostname or '').lower() == 'www.otto.de'
                    and parts.path.rsplit('/', 1)[-1] in ('p.js', 'rc.js', 'mfc', 'fp', 'ips.js', 'tl')):
                headers = {str(k).lower(): v for k, v in (response.headers or {}).items()}
                print('response:', parts.path.rsplit('/', 1)[-1], response.status,
                      'bytes:', len(response.content), 'x-kpsdk-r:', headers.get('x-kpsdk-r'), flush=True)
            if (parts.hostname or '').lower() == 'www.otto.de' and parts.path.endswith('/tl') and request.method.upper() == 'POST':
                headers = {str(k).lower(): v for k, v in (request.headers or {}).items()}
                print('tl request:', 'body_bytes:', len(request.body or b''),
                      'headers:', {k: len(str(v)) for k, v in headers.items() if k.startswith('x-kpsdk-')},
                      'cookie_names:', [item.strip().split('=', 1)[0]
                                       for item in str(self.cookies.header_for(request.url) or '').split(';') if item.strip()],
                      'referer:', urlsplit(str(headers.get('referer', ''))).path, flush=True)
                try:
                    body = response.json()
                except (ValueError, TypeError):
                    body = None
                results.append((response.status, body))
                print('tl response:', response.status, json.dumps(body, ensure_ascii=False), flush=True)
                if body is None:
                    print('tl response body:', repr(response.text[:200]), flush=True)
                completed.set()
            return response

    register_transport('otto_observed', lambda *, options=None: ObservedTransport(options=options),
                       option_keys=ObservedTransport.accepted_option_keys, proxy=True)
    def observe_text(document, value):
        raw = str(value)
        if raw.startswith('__OTTO_CAPTURE__'):
            payload = raw[len('__OTTO_CAPTURE__'):]
            json.loads(payload)
            Path(__import__('tempfile').gettempdir(), 'otto_pyv8c_closure_sample.json').write_text(payload, encoding='utf8')
            print('closure payload captured:', len(payload), flush=True)
        if raw.startswith('__OTTO_HITS__'):
            hits = json.loads(raw[len('__OTTO_HITS__'):])
            Path(__import__('tempfile').gettempdir(), 'otto_pyv8c_hits.json').write_text(
                json.dumps(hits, ensure_ascii=False), encoding='utf8')
            print('member hits:', len(hits), flush=True)
        return Document.fn_createTextNode(document, value)

    override = os.environ.get('PYV8C_ENV_JSON')
    environment = (json.loads(Path(override).read_text(encoding='utf-8'))
                   if override else None)
    browser = Browser(environment=environment, interface_overrides={'Document': {'createTextNode': observe_text}}, proxy=args.proxy, cache=False, threaded_loaders=True,
                      resource_interceptor=intercept, http_engine='otto_observed',
                      http_options={'impersonate': 'chrome150', 'timeout': 25},
                      ws_engine=None, stub_logging='off', trace=False,
                      trace_scripts=None,
                      trace_callback=None)
    try:
        page = browser.new_profile().new_page()
        started = time.monotonic()
        try:
            page.goto(URL, wait_until='domcontentloaded', timeout=min(args.timeout, 45))
        except BaseException as error:
            print('navigation:', type(error).__name__, str(error)[:200], flush=True)
        remaining = max(0.0, args.timeout - (time.monotonic() - started))
        completed.wait(remaining)
        try:
            print('analysis globals:', page.evaluate("Array.from(document.querySelectorAll('iframe'), f => Object.getOwnPropertyNames(f.contentWindow).filter(x => x.startsWith('__vm') || x.startsWith('__patch')))"), flush=True)
        except Exception as e: print('global check error:', str(e)[:100], flush=True)
        print('requests:', counts, flush=True)
        if errors:
            print('ips.js failed operations:', sorted(errors.items(), key=lambda item: -item[1])[:30], flush=True)
            for line in error_examples.values():
                print('ips.js error sample:', line, flush=True)
        if any(status == 200 and isinstance(body, dict) and body.get('reload') is True
               for status, body in results):
            print('PASS: /tl returned {"reload":true}', flush=True)
            return 0
        print('FAIL: no successful /tl response observed', flush=True)
        return 1
    finally:
        browser.close()


if __name__ == '__main__':
    raise SystemExit(main())
