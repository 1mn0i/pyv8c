import threading

from pyv8c import Browser


def test_rendering_opportunity_is_not_a_timer_task():
    browser = Browser(real_network=False)
    try:
        page = browser.new_profile().new_page()
        done = threading.Event()
        observed = []
        def frame(stamp):
            observed.append((page.realm.agent.tasks._current, len(page.tasks.timers)))
            done.set()
        page.executor.call(page.services.task_services.request_animation_frame, frame)
        assert done.wait(3)
        assert observed == [(None, 0)]
    finally:
        browser.close()


def test_hidden_tree_pauses_frames_but_keeps_other_tasks_running():
    browser = Browser(real_network=False)
    try:
        page = browser.new_profile().new_page()
        page.set_content('<body><iframe></iframe></body>')
        page.wait_for_load_state(timeout=3)
        page.set_visibility(True)
        page.evaluate("""globalThis.log=[];globalThis.timer=false;
            requestAnimationFrame(()=>log.push('parent'));
            frames[0].requestAnimationFrame(()=>log.push('child'));
            setTimeout(()=>timer=true,50);
        """)
        page.wait_for('timer', timeout=3)
        assert page.evaluate('log') == []
        assert page.executor.call(page.execution_group.rendering.next_deadline) is None
        assert page.run_until_idle(timeout=0)
        page.set_visibility(False)
        page.wait_for('log.length===2', timeout=3)
        assert page.evaluate('log') == ['parent', 'child']
        assert page.executor.call(page.execution_group.rendering.next_deadline) is None
    finally:
        browser.close()


def test_navigation_and_close_release_frame_callbacks():
    browser = Browser(real_network=False)
    try:
        page = browser.new_profile().new_page()
        page.set_visibility(True)
        page.evaluate('globalThis.oldRan=false;requestAnimationFrame(()=>oldRan=true)')
        old = page.executor.call(lambda: page.services.task_services._frames)
        page.set_content('<body>replacement</body>')
        assert old.closed and not old.callbacks
        page.wait_for_load_state(timeout=3)
        page.evaluate('globalThis.done=false;requestAnimationFrame(()=>done=true)')
        page.wait_for('done', timeout=3)
        group = page.execution_group
        page.close()
        assert group.rendering.closed and not group.rendering._lanes
    finally:
        browser.close()


def test_reinsert_does_not_resume_the_retired_child_frame():
    browser = Browser(real_network=False)
    try:
        page = browser.new_profile().new_page()
        page.set_content('<body><iframe></iframe></body>')
        page.wait_for_load_state(timeout=3)
        page.evaluate("""globalThis.log=[];globalThis.done=false;
            const frame=document.querySelector('iframe'),old=frame.contentWindow;
            old.requestAnimationFrame(()=>log.push('stale'));
            requestAnimationFrame(()=>{
                frame.remove();document.body.appendChild(frame);
                frame.contentWindow.requestAnimationFrame(()=>log.push('current'));
                requestAnimationFrame(()=>requestAnimationFrame(()=>done=true));
            });
        """)
        page.wait_for('done', timeout=3)
        assert page.evaluate('log') == ['current']
    finally:
        browser.close()
