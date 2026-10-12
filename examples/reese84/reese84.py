"""reese84：本地跑挑战脚本拿到 Cookie，再查一次业务接口。"""
import json
import time
from html import escape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pyv8
from loguru import logger
from pyv8c import Browser
from pyv8c.web.bom.window import Window
from pyv8c.web.dom.html_iframe import HTMLIFrameElement
from pyv8c.web.page_errors import PageSyntaxError, PageTypeError, js_error_value

logger.remove()  # 想让日志同时打到控制台，把这一行去掉
logger.add("reese84.log", level="DEBUG", mode='w')

ORIGIN = 'https://orion.nv.gov'
URL = ORIGIN + '/portal/public/#/public/nvsos/en/CaseXscreen?screen=external-GenericFilingsSearch&tabRoute=agent'
SDK_PATH = '/sworthe-which-welcolmembling-those-That-th-edgem'
API_URL = ORIGIN + '/core/api/_q/v1/nv-sos/public/CRAEntities'
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'
              ' Chrome/150.0.0.0 Safari/537.36')
KEYWORD = 'NORTHWEST'
TIMEOUT = 45.0


def intercept(url, request):
    """只放行挑战流程需要的请求；文档换成只带挑战脚本的那一版。"""
    parsed, method = urlsplit(url), request.get('method', 'GET').upper()
    allowed = parsed.hostname == 'orion.nv.gov' and (
        (parsed.path == '/portal/public/' and method == 'GET') or
        (parsed.path == SDK_PATH and method in ('GET', 'POST')) or
        (parsed.path == '/_Incapsula_Resource' and method == 'GET'
         and ('SWJIYLWA' in parse_qs(parsed.query) or 'SWKMTFSR' in parse_qs(parsed.query))))
    if not allowed:
        print('×', method, parsed.path)
        return False
    print('✓', method, parsed.path)


def wait_for_cookie(context, url, seconds=TIMEOUT):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        header = context.cookies.header_for(url)
        for part in header.split('; '):
            if part.startswith('reese84=') and part[8:]:
                return header
        time.sleep(0.2)
    raise RuntimeError('没有拿到新的 reese84 Cookie，看看 reese84.log 里的 SDK 报错')


def search_body(keyword, page_number=1, page_size=25):
    params = {'agentName': keyword, 'registeredAgentType': '', 'craId': '', 'nvBusinessId': '',
              'craEntityNumber': '', 'agentStatus': '', 'filingNo': '', 'submit1': False,
              'searchDumy': True, 'restrictedAndProhibitedWords': '', 'totalRecordCount': '',
              'searchType': 'STARTS_WITH', 'searchTerm': ''}
    return {'data': {}, 'origin': '', 'origin_key': 'CaseX',
            'gridInput': {'params': {'parameters': params.copy()}, 'persistPrevParams': False},
            'parameters': params, 'pageNo': page_number, 'pageSize': page_size, 'sortBy': {}}


if "__main__" == __name__:
    started = time.time()

    browser = Browser(
        resource_interceptor=intercept,
        # interface_overrides={'HTMLIFrameElement': {'contentWindow': content_window},
        #                     #  'Response': {'json': response_json}
        #                      },
        cache=False,
        http_engine='curl_cffi',
        http_options={'impersonate': 'chrome150', 'timeout': 20},
        proxy="http://127.0.0.1:7890",
        environment={'user_agent': USER_AGENT, 'languages': ['en-US', 'en'],
                     'client_hints.full_version_list': [
                         {'brand': 'Chromium', 'version': '150.0.0.0'},
                         {'brand': 'Google Chrome', 'version': '150.0.0.0'},
                         {'brand': 'Not A(Brand', 'version': '8.0.0.0'}]},
    )
    context = browser.new_context()
    page = context.new_page()

    page.goto(URL)
    cookies = wait_for_cookie(context, API_URL)

    response = page.net.post(API_URL, json=search_body(KEYWORD), timeout=20)
    print(response.text)
    data = response.json()
    browser.close()

    print('cookies: ', cookies)
    print('api: ', response.status, 'code', data.get('code'), 'total', data.get('totalItemCount'))
    print('agents: ', [item.get('agentname') for item in data.get('items', [])][:5])
    print(time.time() - started)
