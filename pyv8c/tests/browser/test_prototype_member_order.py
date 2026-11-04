"""Prototype insertion phases as observed in Chrome's bindings."""

from pyv8c import Browser


PERFORMANCE_NAMES = [
    'timeOrigin', 'onresourcetimingbufferfull', 'clearMarks', 'clearMeasures',
    'clearResourceTimings', 'getEntries', 'getEntriesByName', 'getEntriesByType',
    'mark', 'measure', 'setResourceTimingBufferSize', 'toJSON', 'now',
    'constructor', 'timing', 'navigation', 'memory', 'eventCounts', 'interactionCount',
]


def test_performance_order_when_instance_is_observed_first():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        assert page.evaluate('Object.getOwnPropertyNames(Object.getPrototypeOf(performance))') == PERFORMANCE_NAMES
        assert page.evaluate('Object.getOwnPropertyNames(Performance.prototype)') == PERFORMANCE_NAMES
        assert page.evaluate('''(() => {
            const names = [], seen = new Set();
            for (let p = Object.getPrototypeOf(performance); p; p = Object.getPrototypeOf(p)) {
                for (const name of Object.getOwnPropertyNames(p)) {
                    if (!seen.has(name)) { seen.add(name); names.push(name); }
                }
            }
            return names;
        })()''') == PERFORMANCE_NAMES + [
            'addEventListener', 'dispatchEvent', 'removeEventListener', 'when',
            '__defineGetter__', '__defineSetter__', 'hasOwnProperty', '__lookupGetter__',
            '__lookupSetter__', 'isPrototypeOf', 'propertyIsEnumerable', 'toString',
            'valueOf', '__proto__', 'toLocaleString',
        ]
        assert page.evaluate('''(() => {
            const d = Object.getOwnPropertyDescriptor(Performance.prototype, 'timing');
            return [typeof d.get, d.set === undefined, d.enumerable, d.configurable,
                    Performance.prototype.constructor === Performance];
        })()''') == ['function', True, True, True, True]
        # Materializing the interface again must not undo page changes to its order.
        assert page.evaluate('''(() => {
            const p = Performance.prototype, d = Object.getOwnPropertyDescriptor(p, 'timing');
            delete p.timing; Object.defineProperty(p, 'timing', d);
            const before = Object.getOwnPropertyNames(p);
            return before.join(',') === Object.getOwnPropertyNames(Performance.prototype).join(',');
        })()''')
    finally:
        browser.close()


def test_order_is_applied_to_each_same_origin_realm():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.set_content('<body></body>', url='https://order.test/')
        page.evaluate("const f = document.createElement('iframe'); f.srcdoc = '<body></body>'; document.body.appendChild(f)")
        page.run_until_idle(timeout=2)
        assert page.evaluate('Object.getOwnPropertyNames(Object.getPrototypeOf(document.querySelector("iframe").contentWindow.performance))') == PERFORMANCE_NAMES
        assert page.evaluate('Object.getOwnPropertyNames(Object.getPrototypeOf(performance))') == PERFORMANCE_NAMES
    finally:
        browser.close()


def test_context_independent_template_member_follows_initial_operations():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        assert page.evaluate('''(() => {
            const names = Object.getOwnPropertyNames(AudioContext.prototype);
            return names.indexOf('suspend') < names.indexOf('playbackStats') &&
                   names.indexOf('playbackStats') < names.indexOf('constructor');
        })()''')
    finally:
        browser.close()
