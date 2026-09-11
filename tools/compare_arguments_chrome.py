"""Run the same isolated argument probes in pyv8c and a temporary Chrome profile."""

import json
import tempfile

from devtools_chrome_smoke import Chrome
from pyv8c import Browser


CASES = {
    "append_missing": "document.createElement('div').appendChild()",
    "append_type": "document.createElement('div').appendChild({})",
    "set_attribute_missing": "document.createElement('div').setAttribute('name')",
    "query_missing": "document.querySelector()",
    "token_missing": "document.createElement('div').classList.contains()",
    "storage_missing": "localStorage.setItem('name')",
    "url_missing": "new URL()",
    "event_missing": "new Event()",
    "message_missing": "postMessage()",
    "headers_missing": "new Headers().set('name')",
    "worker_missing": "new Worker()",
    "url_invalid": "new URL('not-an-url')",
    "dispatch_type": "document.createElement('div').dispatchEvent({})",
    "fetch_missing": "await fetch()",
    "fetch_options": "await fetch('http://example.test', 3)",
    "token_empty": "document.createElement('div').classList.add('')",
    "token_space": "document.createElement('div').classList.add('a b')",
    "blob_parts": "new Blob(1)",
    "headers_byte": "new Headers().set('name', '中')",
    "strings": "return [String(new URLSearchParams({key:null})),new Event(false).type]",
    "convert_order": "const seen=[];const node=document.createElement('div');node.setAttribute({toString(){seen.push('name');return 'id'}},{toString(){seen.push('value');return 'ok'}});return [seen,node.id]",
    "conversion_throw": "const error=new Error('coercion');try{new URL({toString(){throw error}})}catch(caught){return caught===error}",
    "dictionary_order": "const seen=[];new Event('x',{get composed(){seen.push('composed');return false},get bubbles(){seen.push('bubbles');return false},get cancelable(){seen.push('cancelable');return false}});return seen",
    "token_atomic": "const node=document.createElement('div');node.className='old';try{node.classList.add('new','bad token')}catch(error){}return node.className",
    "token_conversion_order": "const seen=[];try{document.createElement('div').classList.add('',{toString(){seen.push('second');return 'ok'}})}catch(error){}return seen",
    "ignored_extra": "return document.createElement('div').getAttribute('id',{toString(){throw new Error('ignored')}})",
    "token_contains_invalid": "return [document.createElement('div').classList.contains(''),document.createElement('div').classList.contains('a b')]",
    "headers_pair_order": "const seen=[];let index=0;new Headers({get [Symbol.iterator](){seen.push('iterator');return function(){return {next(){seen.push('next');return index++?{done:true}:{value:[{toString(){seen.push('key');return 'x'}},{toString(){seen.push('value');return 'y'}}],done:false}}}}}});return seen",
    "iterator_close": "const seen=[];let index=0;try{new Blob({[Symbol.iterator](){return {next(){seen.push('next');return {done:false,value:{toString(){seen.push('value');throw 42}}}},return(){seen.push('close');return {done:true}}}}})}catch(error){seen.push(error)}return seen",
    "fetch_throw_identity": "const error={value:42};try{await fetch({toString(){throw error}})}catch(caught){return caught===error}",
    "primitive_throw_identity": "try{new URL({toString(){throw 42}})}catch(caught){return caught===42}",
    "record_own_keys": "const source=Object.create({ignore:'value'});source.key=null;return String(new URLSearchParams(source))",
    "ignored_proxy": "const {proxy,revoke}=Proxy.revocable({},{});revoke();return document.createElement('div').getAttribute('id',proxy)===null",
    "symbol_string": "try{localStorage.setItem('x',Symbol())}catch(error){return error instanceof TypeError}",
    "numeric_bigint": "try{localStorage.key(1n)}catch(error){return error instanceof TypeError}",
    "event_listener_object": "const listener={count:0,handleEvent(){this.count++}};const target=new EventTarget();target.addEventListener('x',listener,{once:true});target.dispatchEvent(new Event('x'));target.dispatchEvent(new Event('x'));return listener.count",
    "event_handler_primitive": "document.body.onload=42;return document.body.onload===null",
    "response_json_reject": "try{await new Response('bad-json').json()}catch(error){return error instanceof SyntaxError}",
    "response_reuse": "const response=new Response('ok');await response.text();try{await response.text()}catch(error){return error instanceof TypeError}",
    "response_status": "try{new Response('',{status:199})}catch(error){return error instanceof RangeError}",
    "worker_options": "try{new Worker('invalid.js',42)}catch(error){return error instanceof TypeError}",
    "worker_option_order": "const seen=[];try{new Worker('unused.js',{get type(){seen.push('type');throw 42},get name(){seen.push('name');return 'x'},get credentials(){seen.push('credentials');return 'omit'}})}catch(error){}return seen",
    "xhr_invalid_no_mutation": "const request=new XMLHttpRequest();try{request.open('bad method','/')}catch(error){}return request.readyState",
    "replace_children_no_mutation": "const node=document.createElement('div');node.textContent='old';try{node.replaceChildren('new',{toString(){throw 42}})}catch(error){}return node.textContent",
    "dom_null_variadic": "const node=document.createElement('div');node.append(null,undefined,false);return node.textContent",
    "clear_timer_optional": "clearTimeout();clearInterval();return true",
}


def probe(source):
    return "(async()=>{try{" + source + ";return {ok:true}}catch(error){return {name:error.name,message:error.message}}})()"


def main():
    browser = Browser(stub_logging="off", resource_loader=lambda url, request: {"url": url, "body": ""})
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url="http://example.test/")
    chrome = None
    with tempfile.TemporaryDirectory(prefix="pyv8-arguments-chrome-") as profile:
        try:
            chrome = Chrome(r"C:\Program Files\Google\Chrome\Application\chrome.exe", profile)
            target, session = chrome.open_frontend(f"http://127.0.0.1:{chrome.port}/json/version")
            records = {}
            for name, source in CASES.items():
                expression = probe(source)
                expected = chrome.evaluate(session, expression)
                page.evaluate("globalThis.argumentResult=undefined;" + expression + ".then(value=>globalThis.argumentResult={value})")
                page.run_until_idle(timeout=0.05)
                observed = page.evaluate("argumentResult")
                actual = json.loads(page.evaluate("JSON.stringify(argumentResult.value)")) if isinstance(observed, dict) else repr(observed)
                records[name] = {"chrome": expected, "pyv8c": actual, "equal": expected == actual}
            print(json.dumps({"chrome": chrome.version, "cases": records}, ensure_ascii=True, indent=2))
            return 0 if all(record["equal"] for record in records.values()) else 1
        finally:
            if chrome is not None:
                chrome.close()
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
