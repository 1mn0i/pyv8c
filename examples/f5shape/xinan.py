"""Southwest 单程航班查询 demo。

直接运行本文件，无启动参数；修改底部的日期、起点、终点即可。
search_flights("2026-11-14", "ISP", "BUR") 打印 Shape 头和航班信息，
返回航班列表，不保存文件。代理、超时和固定业务参数均写在本文件。
"""
from pathlib import Path
import json
import re
import sys
from urllib.parse import urlencode, urljoin, urlsplit
import uuid

sys.dont_write_bytecode = True
WORKSPACE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(WORKSPACE / "pyv8"), str(WORKSPACE / "pyv8c")]

from pyv8c import pyv8
from loguru import logger
from pyv8c import Browser, register_transport
from pyv8c.network.curl_cffi import CurlCffiTransport

PROXY = "http://127.0.0.1:7890"
BASE_URL = "https://www.southwest.com"
API_URL = BASE_URL + "/api/air-booking/v1/air-booking/page/air/booking/shopping"
SDK_HEADERS = tuple(f"Ee30zvqlwf-{suffix}" for suffix in "ABCDFZ")


def _print_flights(flights):
    print(f"\n查询成功，共 {len(flights)} 个航班方案（时间为各机场当地时间）：")
    for index, flight in enumerate(flights, 1):
        segments = flight.get("segments", [])
        route = [flight["originationAirportCode"]] + [
            segment["destinationAirportCode"] for segment in segments
        ]
        numbers = " / ".join(
            f'{segment["marketingCarrierCode"]}{segment["flightNumber"]}'
            for segment in segments
        )
        minutes = flight["totalDuration"]
        stops = max(0, len(segments) - 1) + sum(
            segment.get("numberOfStops", 0) for segment in segments
        )
        print(f"\n{index}. {numbers} | {' → '.join(route)}")
        print(f'   出发：{flight["departureDateTime"]}')
        print(f'   到达：{flight["arrivalDateTime"]}')
        print(f"   总时长：{minutes // 60}小时{minutes % 60}分 | 经停：{stops}次")
        fares = []
        for name, product in flight.get("fareProducts", {}).get("ADULT", {}).items():
            price = product.get("fare", {}).get("totalFare", {})
            fares.append(
                f'{name}: {price.get("currencyCode", "")} {price.get("value", "--")}'
                f' [{product.get("availabilityStatus", "--")}]'
            )
        print("   票价：" + " | ".join(fares))


def search_flights(date, origin, destination):
    """查询日期 YYYY-MM-DD、起点 IATA、终点 IATA；打印并返回航班列表。"""
    origin, destination = origin.strip().upper(), destination.strip().upper()
    body = {
        "lapInfantPassengersCount": "0", "olderChildCount": "0", "teensCount": "0",
        "youngerChildCount": "0", "adultsCount": "1", "adultPassengersCount": "1",
        "destinationAirportCode": destination, "departureDate": date,
        "departureTimeOfDay": "ALL_DAY", "fareType": "USD", "int": "HOMEQBOMAIR",
        "originationAirportCode": origin, "passengerType": "ADULT", "promoCode": "",
        "returnDate": "", "returnTimeOfDay": "ALL_DAY", "tripType": "oneway",
    }
    page_url = BASE_URL + "/air/booking/select-depart?" + urlencode(
        {**body, "from": origin, "to": destination}
    )
    config = {}

    def intercept(url, request):
        target = urlsplit(url)
        if target.scheme not in ("http", "https"):
            return None
        keep = target.hostname == "www.southwest.com" and (
            url in (page_url, API_URL, config.get("bootstrap"))
            or target.path.startswith("/resources/")
            or target.path == "/assets/app/scripts/swa-common.js"
        )
        return None if keep else False

    class Transport(CurlCffiTransport):
        def _call(self, request):
            request.timeout = 25
            if request.url == API_URL:
                headers = {name.lower(): value for name, value in request.headers.items()}
                print("\n本次请求使用的 Shape 参数：")
                for name in SDK_HEADERS:
                    if not headers.get(name.lower()):
                        raise RuntimeError(f"缺少 Shape 请求头：{name}")
                    print(f"{name}: {headers[name.lower()]}")
            response = super()._call(request)
            if request.url == page_url:
                if response.status != 200:
                    raise RuntimeError(f"查询页面 HTTP {response.status}")
                bootstrap = re.search(
                    r'''src=["']([^"']*/swa-ui/bootstrap/[^"']+/data\.js)["']''',
                    response.text,
                )
                version = re.search(r"/v2/air/booking/static/([^/]+)/js/", response.text)
                if not bootstrap or not version:
                    raise RuntimeError("页面缺少 bootstrap 或应用版本")
                config["bootstrap"] = urljoin(BASE_URL, bootstrap[1])
                config["version"] = version[1]
            elif request.url == config.get("bootstrap"):
                match = re.search(
                    r'''["'][^"']+/api-keys["']:\[function\(require,module,exports\)\{\s*module.exports\s*=\s*(\{.*?\});''',
                    response.text,
                )
                if not match:
                    raise RuntimeError("bootstrap 缺少 API key 配置")
                config["api_key"] = json.loads(match[1])["prod"]
            return response

    register_transport("southwest-demo", Transport, proxy=True)
    browser = Browser(
        proxy=PROXY, http_engine="southwest-demo",
        http_options={"impersonate": "chrome150", "timeout": 25}, cache=False,
        resource_interceptor=intercept, threaded_loaders=True, stub_logging="off",
    )
    try:
        page = browser.new_profile().new_page()
        page.goto(page_url, wait_until="domcontentloaded", timeout=45)
        if not config.get("api_key"):
            raise RuntimeError("bootstrap 未加载成功，无法读取 API key")
        headers = {
            "Accept": "application/json, text/plain, */*", "content-type": "application/json",
            "x-api-key": config["api_key"], "x-channel-id": "southwest",
            "x-app-id": "air-booking-v2", "x-app-version": config["version"],
            "x-diagnostic": json.dumps({"spa": config["version"]}, separators=(",", ":")),
            "x-user-experience-id": str(uuid.uuid4()),
        }
        # 页面 fetch 经过原始 SDK，直接 page.net.get() 不会生成这些头。
        response = page.net.fetch(
            API_URL, method="POST", headers=headers,
            body=json.dumps(body, separators=(",", ":")), timeout=60,
        )
        if response.status != 200:
            raise RuntimeError(f"Shopping HTTP {response.status}: {response.text[:1000]}")
        products = response.json().get("data", {}).get("searchResults", {}).get("airProducts")
        if not isinstance(products, list):
            raise RuntimeError(f"接口未返回航班数据：{response.text[:1000]}")
        flights = [flight for product in products for flight in product.get("details", [])]
        _print_flights(flights)
        return flights
    finally:
        browser.close()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    try:
        search_flights("2026-11-14", "ISP", "BUR")
    except (Exception, pyv8.JSException) as error:
        print(f"查询失败：{error}", file=sys.stderr)
        raise SystemExit(1)
