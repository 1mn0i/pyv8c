import time
from loguru import logger
from pyv8c import Browser


def run(page, url):
    page.goto(url, wait_until="domcontentloaded")
    # page.goto(url)
    # page.run_until_idle(timeout=3)
    # res = page.net.get(url)
    res = page.fetch(url)
    # print(res.text)
    logger.info(f"res: {res.status}, {len(res.text)}")


if "__main__" == __name__:
    st = time.time()

    def intercept(url, request):
        # print(request.get("resource_type") , url)
        if request.get("resource_type") in {"image", "style"}:
            return False
        if request.get("resource_type") == "script":
            if 'N7xexRXkVdNQ' in url:
                return None
            if 'fpqQrgG7L6po' in url:
                return None
            if '4QbVtADbnLVIc' in url:
                return None
            # https://lib.cqvip.com/N7xexRXkVdNQ/Y5oPTJBkyHck.cca831b.js
            # https://www.nmpa.gov.cn/fpqQrgG7L6po/eaKJbLE9bqof.e17ed02.js
            # https://www.cde.org.cn/4QbVtADbnLVIc/c.FxJzG50F.6152bb9.js?D9PVtGL=6152bb
            return False
        print(request.get("resource_type") , url)
        return None
        
    browser = Browser(
        # stub_logging="verbose",
        # trace=True, traceable=True,
        resource_interceptor=intercept
    )
    context = browser.new_context()
    page = context.new_page()
    run(page, "https://www.nmpa.gov.cn/datasearch/home-index.html")
    # run(page,"https://www.cde.org.cn/main/xxgk/listpage/9f9c74c73e0f8f56a8bfbc646055026d")
    # run(page,"https://lib.cqvip.com/Qikan/Search/Index?from=index")

    browser.close()
    print(time.time() - st)
