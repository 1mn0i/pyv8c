import pytest

pytest.importorskip("pyv8")

from pyv8c import Browser, BrowserEnvironment, MimeTypeConfig, PluginConfig


def new_page(environment=None):
    browser = Browser(environment=environment, stub_logging="off")
    return browser, browser.new_context().new_page()


def test_default_plugins_match_current_chrome_pdf_snapshot():
    browser, page = new_page()
    try:
        assert page.evaluate("""
            (() => [
                navigator.plugins.length,
                navigator.mimeTypes.length,
                [...navigator.plugins].map(plugin => [
                    plugin.name, plugin.filename, plugin.description,
                    [...plugin].map(mime => mime.type),
                ]),
                [...navigator.mimeTypes].map(mime => [
                    mime.type, mime.suffixes, mime.description, mime.enabledPlugin.name,
                ]),
            ])()
        """) == [
            5,
            2,
            [
                ["PDF Viewer", "internal-pdf-viewer", "Portable Document Format", ["application/pdf", "text/pdf"]],
                ["Chrome PDF Viewer", "internal-pdf-viewer", "Portable Document Format", ["application/pdf", "text/pdf"]],
                ["Chromium PDF Viewer", "internal-pdf-viewer", "Portable Document Format", ["application/pdf", "text/pdf"]],
                ["Microsoft Edge PDF Viewer", "internal-pdf-viewer", "Portable Document Format", ["application/pdf", "text/pdf"]],
                ["WebKit built-in PDF", "internal-pdf-viewer", "Portable Document Format", ["application/pdf", "text/pdf"]],
            ],
            [
                ["application/pdf", "pdf", "Portable Document Format", "PDF Viewer"],
                ["text/pdf", "pdf", "Portable Document Format", "PDF Viewer"],
            ],
        ]
    finally:
        browser.close()


def test_plugin_and_mime_type_collections_follow_browser_legacy_collection_rules():
    browser, page = new_page()
    try:
        assert page.evaluate("""
            (() => {
                const plugins = navigator.plugins;
                const mimes = navigator.mimeTypes;
                return [
                    Object.prototype.toString.call(plugins),
                    Object.prototype.toString.call(plugins[0]),
                    Object.prototype.toString.call(mimes),
                    Object.prototype.toString.call(mimes[0]),
                    plugins.item(0) === plugins[0],
                    plugins.namedItem('PDF Viewer') === plugins['PDF Viewer'],
                    mimes.item(0) === mimes['application/pdf'],
                    plugins[0][0] === mimes[0],
                    mimes[0].enabledPlugin === plugins[0],
                    Object.keys(plugins),
                    Object.getOwnPropertyNames(plugins),
                    Object.getOwnPropertyDescriptor(plugins, 'PDF Viewer').enumerable,
                    Object.getOwnPropertyDescriptor(mimes, 'application/pdf').writable,
                    typeof plugins.refresh,
                    plugins.item(99) === null,
                    plugins.namedItem('missing') === null,
                ];
            })()
        """) == [
            "[object PluginArray]", "[object Plugin]", "[object MimeTypeArray]", "[object MimeType]",
            True, True, True, False, True,
            ["0", "1", "2", "3", "4"],
            ["0", "1", "2", "3", "4", "PDF Viewer", "Chrome PDF Viewer", "Chromium PDF Viewer", "Microsoft Edge PDF Viewer", "WebKit built-in PDF"],
            False, False, "function", True, True,
        ]
    finally:
        browser.close()


def test_environment_plugins_support_mapping_and_page_overrides():
    base = {
        "plugins": [{
            "name": "Browser Plugin",
            "filename": "browser-plugin",
            "description": "Browser description",
            "mime_types": [{"type": "application/browser", "suffixes": "browser", "description": "Browser MIME"}],
        }],
    }
    override = {
        "plugins": [{
            "name": "Page Plugin",
            "filename": "page-plugin",
            "description": "Page description",
            "mime_types": [
                {"type": "application/page", "suffixes": "pg", "description": "Page MIME"},
                {"type": "text/page", "suffixes": "page", "description": "Text Page MIME"},
            ],
        }],
    }
    browser = Browser(environment=base, stub_logging="off")
    try:
        context = browser.new_context()
        default_page = context.new_page()
        override_page = context.new_page(environment=override)
        assert default_page.evaluate("[navigator.plugins[0].name, navigator.mimeTypes[0].type]") == ["Browser Plugin", "application/browser"]
        assert override_page.evaluate("[navigator.plugins.length, navigator.plugins['Page Plugin'].filename, navigator.plugins[0].namedItem('text/page').suffixes, navigator.mimeTypes['application/page'].enabledPlugin.name]") == [
            1, "page-plugin", "page", "Page Plugin",
        ]
    finally:
        browser.close()


def test_plugin_configuration_is_immutable_and_validated():
    environment = BrowserEnvironment(
        plugins=(PluginConfig("Test", mime_types=(MimeTypeConfig("application/test"),)),),
    )
    assert environment.plugins[0].mime_types[0].type == "application/test"
    with pytest.raises(ValueError):
        BrowserEnvironment(plugins=[{"name": "duplicate"}, {"name": "duplicate"}])
    with pytest.raises(ValueError):
        BrowserEnvironment(plugins=[{"name": "invalid", "mime_types": [{"type": "application/test"}, {"type": "application/test"}]}])
    with pytest.raises(TypeError):
        BrowserEnvironment(plugins="not a plugin sequence")
