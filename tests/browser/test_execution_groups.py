import threading

from pyv8c import Browser


def test_frames_share_owner_and_agent_but_independent_pages_do_not():
    with_browser = Browser(real_network=False, stub_logging='off')
    try:
        profile = with_browser.new_profile()
        page, other = profile.new_page(), profile.new_page()
        page.set_content('<body><iframe></iframe></body>')
        child = page.document.fn_querySelector('iframe')._child_services._page_realm.page
        assert page.execution_group is child.execution_group
        assert page.realm.agent is child.realm.agent
        assert page.realm.agent is not other.realm.agent
        assert page.executor.call(threading.get_ident) == child.executor.call(threading.get_ident)
        assert page.executor.call(threading.get_ident) != other.executor.call(threading.get_ident)
        child.close()
        assert not page.executor.closed
        assert page.evaluate('1+1') == 2
    finally:
        with_browser.close()


def test_same_origin_frames_share_microtasks_in_enqueue_order():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.set_content('<body><iframe></iframe></body>')
        assert page.evaluate('''globalThis.log=[];
            frames[0].eval("Promise.resolve().then(()=>parent.log.push('child'))");
            Promise.resolve().then(()=>log.push('parent')); 'scheduled'
        ''') == 'scheduled'
        assert page.evaluate('log') == ['child', 'parent']
    finally:
        browser.close()


def test_origin_navigation_changes_agent_without_replacing_group():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        group = page.execution_group
        opaque = page.realm.agent
        page.set_content('<body></body>', 'https://one.test/')
        first = page.realm.agent
        assert first is not opaque
        page.set_content('<body></body>', 'https://two.test/')
        assert page.realm.agent is not first
        page.set_content('<body></body>', 'https://one.test/again')
        assert page.realm.agent is first
        assert page.execution_group is group
    finally:
        browser.close()


def test_external_close_joins_the_last_group_owner():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        thread = page.execution_group.executor._thread
        page.close()
        assert not thread.is_alive()
    finally:
        browser.close()
