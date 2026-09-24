import time
from loguru import logger
from pyv8c import Browser

logger.add("info.log", level="DEBUG")

if "__main__" == __name__:
    st = time.time()
        
    browser = Browser(
        stub_logging="verbose",
        # http_engine="cronet",
        # trace=True, traceable=True,
        # resource_interceptor=intercept
        interface_baseline_path=r'D:\src\pyv8env\pyv8c\baselines\145.0.7632.162.zip',
        proxy="http://127.0.0.1:7890"
    )
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://www.koreanair.com", wait_until="load")
    page.run_until_idle(timeout=30)

    browser.close()
    print(time.time() - st)
