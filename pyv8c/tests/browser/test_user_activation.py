from pyv8c import Browser


def test_trusted_click_sets_user_activation_but_script_click_does_not():
    browser = Browser(real_network=False)
    try:
        page = browser.new_profile().new_page()
        page.set_content('<button id="x">ok</button>', url='https://example.test/')
        flags = '[navigator.userActivation.isActive,navigator.userActivation.hasBeenActive]'
        assert page.evaluate(flags) == [False, False]
        page.evaluate("document.querySelector('#x').click()")
        assert page.evaluate(flags) == [False, False]
        page.click('#x')
        assert page.evaluate(flags) == [True, True]
    finally:
        browser.close()
