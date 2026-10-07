import time
from loguru import logger
from pyv8c import Browser

logger.add("akm.log", level="DEBUG")
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
        # stub_logging="verbose",
        # http_engine="cronet",
        # trace=True, traceable=True,
        resource_interceptor=intercept,
        # idl_path=r'D:\src\other-build\154.0.8030.0.zip',   # 换一份 IDL 文档；默认读包内 idl/idl.zip
        proxy="http://127.0.0.1:7890",
        threaded_loaders=True,
    )
    context = browser.new_context()
    page = context.new_page()
    # url = "https://www.koreanair.com"
    url = "https://www.bestbuy.com"
    # url = "https://www.trinityairways.com/app/main"
    # url = "https://www.dhl.com/cn-zh/home/tracking/tracking-ecommerce.html?submit=1&tracking-id=123123"
    page.goto(url, wait_until="domcontentloaded")
    # page.run_until_idle(timeout=2)

    # 传感器看的是光标：走一段路径，落到终点后点一下。
    for x, y, steps in ((360, 220, 9), (700, 380, 12), (940, 470, 10)):
        page.mouse.move(x, y, steps=steps)
        page.run_until_idle(timeout=0)
    page.mouse.click()
    page.run_until_idle(timeout=0)
    print(context.cookies.header_for(url))

    browser.close()
    print(time.time() - st)
