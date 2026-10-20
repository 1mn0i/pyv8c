"""Local slow-worker source, early messages and termination against Chrome."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from pyv8c import Browser
from harness import chrome_reference


@pytest.mark.parametrize('action', ['release','terminate','document-open'])
def test_slow_source_and_early_messages_match_chrome(action):
    terminate = action == 'terminate'
    entered, release, served = threading.Event(), threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            worker = self.path.startswith('/worker.js')
            if worker:
                entered.set()
                release.wait(5)
            body = (b"postMessage('boot');onmessage=e=>{postMessage(e.data);"
                    b"queueMicrotask(()=>postMessage('micro'))}" if worker else b'<body></body>')
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript' if worker else 'text/html')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if worker:
                    served.set()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/'
    script = ("globalThis.messages=[];globalThis.pulse=false;"
              "globalThis.worker=new Worker('/worker.js');"
              "worker.onmessage=e=>messages.push(e.data);worker.postMessage('early');"
              "setTimeout(()=>pulse=true,0)")
    results = []
    try:
        with chrome_reference() as reference:
            for kind in ('chrome', 'pyv8c'):
                entered.clear(); release.clear(); served.clear()
                browser = Browser(stub_logging='off') if kind == 'pyv8c' else None
                page = browser.new_profile().new_page() if browser else reference.browser.new_page()
                runtimes = ()
                try:
                    page.goto(url)
                    page.evaluate(script)
                    assert entered.wait(3)
                    assert page.evaluate('21*2') == 42
                    if browser:
                        page.wait_for('pulse', timeout=2)
                        runtimes = page.executor.call(lambda: tuple(w._runtime for w in page._owned.workers))
                    else:
                        page.wait_for_function('pulse', timeout=2000)
                    if terminate:
                        page.evaluate('worker.terminate()')
                        if browser:
                            for runtime in runtimes:
                                runtime.executor._thread.join(1)
                                assert not runtime.executor._thread.is_alive()
                    elif action == 'document-open':
                        page.evaluate("document.open();document.write('<body>new</body>');document.close()")
                    release.set()
                    assert served.wait(3)
                    if terminate:
                        # Give a mistakenly delivered source a turn to execute.
                        page.evaluate('globalThis.settled=false;setTimeout(()=>settled=true,100)')
                        if browser:
                            page.wait_for('settled', timeout=2)
                        else:
                            page.wait_for_function('settled', timeout=2000)
                    elif browser:
                        page.wait_for('messages.length===3', timeout=3)
                    else:
                        page.wait_for_function('messages.length===3', timeout=3000)
                    results.append(page.evaluate('messages'))
                finally:
                    release.set()
                    browser.close() if browser else page.close()
                    for runtime in runtimes:
                        runtime.executor._thread.join(2)
                        assert not runtime.executor._thread.is_alive()
        assert results == ([[], []] if terminate else [['boot', 'early', 'micro']] * 2)
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()
