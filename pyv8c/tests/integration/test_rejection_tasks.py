import threading
import pytest
pytest.importorskip('pyv8c.pyv8')
from pyv8c import Browser


@pytest.fixture
def page():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        yield page
    finally:
        browser.close()


def test_sibling_frame_rejection_progresses_without_a_child_host_command(page):
    page.set_content('<iframe></iframe>')
    page.wait_for_load_state()
    page.evaluate("""window.seen=[];frames[0].onunhandledrejection=e=>{
      seen.push(e.reason);e.preventDefault()};frames[0].eval("Promise.reject('child')");'queued' """)
    page.wait_for('seen.length === 1', timeout=0.5)
    assert page.evaluate('seen') == ['child']


def test_late_catch_schedules_handled_without_host_polling(page):
    delivered = threading.Event()
    page.executor.call(page.v8_context.expose, handled=delivered.set)
    page.evaluate("""window.seen=[];const reason={label:'reason'};
      onrejectionhandled=e=>{seen.push([e.reason===reason,e.promise===p]);handled()};
      onunhandledrejection=e=>{e.preventDefault();setTimeout(()=>e.promise.catch(()=>{}),0)};
      const p=Promise.reject(reason);'queued' """)
    assert delivered.wait(0.5)
    assert page.evaluate('seen') == [[True, True]]


def test_navigation_drops_old_rejection_candidates_without_clearing_agent_jobs(page):
    page.set_content('<body>old</body>', url='https://same.test/old')
    previous = page.realm
    previous_tracker = previous.rejections
    def replace():
        page.scripts.evaluate("Promise.reject('old rejection')")
        page.navigation.set_content("<script>window.seen=[];onunhandledrejection=e=>seen.push(e.reason)</script>",
                                    url='https://same.test/new')
    page.executor.call(replace)
    page.run_until_idle(1)
    assert page.evaluate('seen') == []
    assert previous is not page.realm
    assert previous_tracker is not page.realm.rejections
    assert previous_tracker._state is None
    assert previous.agent is page.realm.agent
    assert page.evaluate('Promise.resolve(1);42') == 42


def test_notified_promises_are_weakly_held_even_with_a_cyclic_reason(page):
    page.evaluate("""window.refs=[];window.count=0;
      onunhandledrejection=e=>{count++;e.preventDefault()};
      for(let i=0;i<100;i++){
        const reason={index:i};const promise=Promise.reject(reason);reason.promise=promise;
        refs.push(new WeakRef(promise));
      }'queued' """)
    page.wait_for('count === 100')
    page.executor.call(page.isolate.gc)
    page.executor.call(page.isolate.gc)
    assert page.evaluate('refs.every(ref=>ref.deref()===undefined)') is True
    tracker = page.realm.rejections
    assert not tracker._pending and not tracker._queued and not tracker._handled


def test_rejection_state_uses_pristine_intrinsics_and_does_not_read_reason_getters(page):
    page.evaluate("""window.seen=[];window.reads=0;
      const reason={get value(){reads++;throw new Error('must not read')}};
      const NativeWeakMap=WeakMap;
      WeakMap.prototype.get=()=>{throw new Error('patched get')};
      WeakMap.prototype.set=()=>{throw new Error('patched set')};
      globalThis.WeakMap=function(){throw new Error('patched constructor')};
      onunhandledrejection=e=>{e.preventDefault();seen.push(e.reason===reason);setTimeout(()=>e.promise.catch(()=>{}),0)};
      onrejectionhandled=e=>seen.push(e.reason===reason);
      Promise.reject(reason);'queued' """)
    page.wait_for('seen.length === 2')
    assert page.evaluate('[seen,reads]') == [[True,True],0]


def test_rejection_flood_times_out_without_unraisable_hook_errors():
    import subprocess
    import sys
    program = r"""
import sys
from pyv8c import pyv8
from pyv8c import Browser
from pyv8c.foundation.host_errors import RealmBusyError
from loguru import logger
logger.disable('pyv8c')
errors=[]
sys.unraisablehook=lambda error:errors.append(str(error.exc_value))
browser=Browser(real_network=False,stub_logging='off')
try:
    page=browser.new_profile().new_page()
    try:page.evaluate("while(true){Promise.reject('flood')}",timeout=0.03)
    except (pyv8.JavaScriptTerminated, RealmBusyError):pass
    else:raise AssertionError('execution was not interrupted')
    assert page.evaluate('42',timeout=2)==42
    assert errors==[], errors
finally:browser.close()
"""
    result = subprocess.run([sys.executable,'-c',program],capture_output=True,text=True,timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_checkpoint_only_flushes_pending_realms_and_releases_candidates(page, monkeypatch):
    page.set_content('<iframe></iframe><iframe></iframe>')
    page.wait_for_load_state()
    calls = []
    def observe_idle():
        for lane in page.execution_group.members:
            if lane is not page.executor:
                monkeypatch.setattr(lane.rejections, 'flush', lambda: calls.append('idle'))
    page.executor.call(observe_idle)
    page.evaluate("window.seen=[];onunhandledrejection=e=>{seen.push(e.reason);e.preventDefault()};Promise.reject('parent');0")
    page.wait_for("seen.length===1")
    assert page.evaluate('seen') == ['parent']
    assert calls == []
    assert page.executor.call(lambda: not page.execution_group._pending_rejections)


def test_close_before_checkpoint_releases_candidate_subscription(page):
    def close_pending():
        group = page.execution_group
        assert group._pending_rejections
        page.realm.rejections.close()
        assert not group._pending_rejections
    page.executor.call(page.v8_context.expose, closePending=close_pending)
    page.evaluate("Promise.reject('closing');closePending();0")
