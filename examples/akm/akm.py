import time

from loguru import logger
from pyv8c import Browser

logger.add("akm.log", level="DEBUG", mode='w')
# logger.remove()

FRAME = 1 / 60


def human_move(page, x, y, *, frames=8, seed=None):
    """Approach ``(x, y)`` along a bowed path, one position a frame.

    Each frame is one hit test, which on a page this size can cost up to a second;
    raising *frames* buys a longer approach and pays for it here.
    """
    page.mouse.move(x, y, curve="human", steps=frames, duration=frames * FRAME, seed=seed)


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
        # trace=True, traceable=True,
        resource_interceptor=intercept,
        # idl_path=r'D:\src\other-build\154.0.8030.0.zip',   # 换一份 IDL 文档；默认读包内 idl/idl.zip
        # proxy="http://127.0.0.1:7890",    
        http_engine='curl_cffi', http_options={"impersonate": "chrome150"},
        # ws_options={"proxy": PROXY},
        # ws_engine="websockets",
        threaded_loaders=True,
    )
    context = browser.new_context()
    page = context.new_page()
    # url = "https://www.koreanair.com"
    # url = "https://www.bestbuy.com"
    # url = "https://www.trinityairways.com/app/main"
    # url = "https://www.dhl.com/cn-zh/home/tracking/tracking-ecommerce.html?submit=1&tracking-id=123123"
    url = "https://www.radissonhotels.com/zh-cn/"
    page.goto(url, wait_until="domcontentloaded")
    # page.run_until_idle(timeout=2)

    # # 传感器看的是光标：走一段路径，落到终点后点一下。
    # for x, y, steps in ((360, 220, 9), (700, 380, 12), (940, 470, 10)):
    #     page.mouse.move(x, y, steps=steps)
    #     page.run_until_idle(timeout=0)
    # page.mouse.click()
    # page.run_until_idle(timeout=0)
    # print(context.cookies.header_for(url))

    # 传感器看的是光标：逐帧靠近，落到终点后按住一会儿再松开（人手一次点击几十毫秒）。
    for x, y, seed in ((360, 220, 1), (700, 380, 2), (940, 470, 3)):
        human_move(page, x, y, seed=seed)
        page.run_until_idle(timeout=0)
    page.mouse.click(delay=0.07)
    page.run_until_idle(timeout=0)

    logger.info("time.sleep start")
    time.sleep(1)
    logger.info("time.sleep end")
    print(context.cookies.header_for(url))

    browser.close()
    print(time.time() - st)
