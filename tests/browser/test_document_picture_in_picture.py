"""``documentPictureInPicture`` exists in a secure page and answers Chrome's shape."""

from pyv8c import Browser

SURFACE = """(() => {
  const pip = documentPictureInPicture;
  return {
    type: typeof pip,
    tag: Object.prototype.toString.call(pip),
    eventTarget: pip instanceof EventTarget,
    window: pip.window === null ? 'null' : typeof pip.window,
    onenter: pip.onenter === null ? 'null' : typeof pip.onenter,
    same: documentPictureInPicture === pip,
    keys: Object.getOwnPropertyNames(Object.getPrototypeOf(pip)),
    requestLength: pip.requestWindow.length,
  };
})()"""


def test_the_object_matches_chromes_shape():
    browser = Browser(cache=False, real_network=False, stub_logging="off")
    try:
        page = browser.new_profile().new_page()
        page.set_content("<body></body>", url="https://example.test/")
        assert page.evaluate(SURFACE) == {
            "type": "object",
            "tag": "[object DocumentPictureInPicture]",
            "eventTarget": True,
            "window": "null",
            "onenter": "null",
            "same": True,
            "keys": ["window", "onenter", "requestWindow", "constructor"],
            "requestLength": 0,
        }
    finally:
        browser.close()


def test_an_insecure_page_has_none():
    browser = Browser(cache=False, real_network=False, stub_logging="off")
    try:
        page = browser.new_profile().new_page()
        page.set_content("<body></body>", url="http://plain.test/")
        assert page.evaluate("typeof documentPictureInPicture") == "undefined"
    finally:
        browser.close()
