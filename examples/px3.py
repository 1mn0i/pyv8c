import time
from loguru import logger
from pyv8c import Browser

logger.add("px3.log", level="DEBUG")
# logger.remove()

if "__main__" == __name__:
    st = time.time()
    def intercept(url, request):
        if request.get("resource_type") in {"image", "style"}:
            return False
        if '/akam/' in url:
            return False
        if ".js" in url:
            return False
        print(request.get("resource_type") , url)

    browser = Browser(
        stub_logging="verbose",
        # http_engine="cronet",
        trace=True, traceable=True,
        # resource_interceptor=intercept,
        # interface_baseline_path=r'D:\src\pyv8env\pyv8c\baselines\145.0.7632.162.zip',
        # proxy="http://127.0.0.1:7890",
        threaded_loaders=True,
    )
    context = browser.new_context()
    page = context.new_page()
    # url = "http://www.skyscanner.com.au/"
    url = "https://www.skyscanner.com.au/sttc/px/captcha-v2/index.html?url=Lz8=&uuid=f8f53f71-bda4-11f1-9467-efae51e2afd2&vid="
    # page.goto(url, wait_until="domcontentloaded")
    page.goto(url)
    page.run_until_idle(timeout=20)

    browser.close()
    print(time.time() - st)
