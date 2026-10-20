import pytest
pytest.importorskip('pyv8')
from harness import chrome_reference, ReferenceUnavailable
from pyv8c import Browser


@pytest.fixture(scope='module')
def reference():
    try:
        with chrome_reference() as reference:
            yield reference
    except ReferenceUnavailable as error:
        pytest.skip(str(error))


def test_same_agent_messages_across_frames_preserve_source_order(reference):
    browser = Browser(real_network=False, stub_logging='off')
    chrome = reference.browser.new_page()
    script = """globalThis.log=[];
      addEventListener('message',event=>log.push(event.data));
      frames[0].addEventListener('message',event=>log.push(event.data));
      postMessage('parent-first','*');
      frames[0].postMessage('child','*');
      postMessage('parent-last','*');'queued' """
    try:
        page = browser.new_profile().new_page()
        page.set_content('<iframe></iframe>');page.wait_for_load_state()
        chrome.set_content('<iframe></iframe>')
        chrome.evaluate(script);chrome.wait_for_function('log.length===3')
        page.evaluate(script);page.wait_for('log.length===3')
        assert page.evaluate('log') == chrome.evaluate('log') == ['parent-first','child','parent-last']
    finally:
        chrome.close();browser.close()
