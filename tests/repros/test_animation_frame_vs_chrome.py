"""Frame callback mutation and cleanup on an uninstrumented blank document."""
import pytest

from harness import chrome_reference
from pyv8c import Browser


PROBES = {
    'cancel': """
        requestAnimationFrame(t=>{log.push('first');cancelAnimationFrame(second)});
        const second=requestAnimationFrame(t=>log.push('cancelled'));
        requestAnimationFrame(t=>{log.push('last');done=true});
    """,
    'microtask-cancel': """
        requestAnimationFrame(t=>{
            log.push('first');queueMicrotask(()=>{log.push('micro');cancelAnimationFrame(second)});
        });
        const second=requestAnimationFrame(t=>log.push('cancelled'));
        requestAnimationFrame(t=>{log.push('last');done=true});
    """,
    'append': """
        let stamp;
        requestAnimationFrame(t=>{
            stamp=t;log.push('first');
            requestAnimationFrame(u=>{log.push(u>stamp?'next':'same');done=true});
            queueMicrotask(()=>log.push('micro'));
        });
        requestAnimationFrame(t=>log.push(t===stamp?'peer':'wrong'));
    """,
    'throw': """
        addEventListener('error',e=>{e.preventDefault();log.push('error')});
        requestAnimationFrame(()=>{log.push('first');throw new Error('frame failure')});
        requestAnimationFrame(()=>{log.push('last');done=true});
    """,
}

EXPECTED = {
    'cancel': ['first', 'last'],
    'microtask-cancel': ['first', 'micro', 'last'],
    'append': ['first', 'micro', 'peer', 'next'],
    'throw': ['first', 'error', 'last'],
}


@pytest.fixture(scope='module')
def reference():
    with chrome_reference() as reference:
        yield reference


@pytest.mark.parametrize('case', PROBES)
def test_frame_callback_mutation(reference, case):
    script = 'globalThis.log=[];globalThis.done=false;' + PROBES[case] + "'scheduled'"
    chrome = reference.browser.new_page()
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        chrome.evaluate(script)
        chrome.wait_for_function('done', timeout=3000)
        assert chrome.evaluate('log') == EXPECTED[case]
        page.evaluate(script)
        page.wait_for('done', timeout=3)
        assert page.evaluate('log') == chrome.evaluate('log')
    finally:
        browser.close()
        chrome.close()
