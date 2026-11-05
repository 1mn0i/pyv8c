"""Window synchronous XHR response/event ordering, compared with Chrome 156."""
import pytest

from pyv8c import Browser


@pytest.mark.parametrize('threaded', [False, True])
def test_sync_xhr_returns_response_before_next_task(threaded):
    calls=[]
    def loader(url, request):
        headers={key.lower():value for key,value in request['headers'].items()}
        calls.append((url,request['method'],request.get('body'),headers.get('x-example')))
        return {'status':200,'body':'{"ready":true}',
                'headers':{'content-type':'application/json','x-example':'response'}}
    browser=Browser(real_network=False,threaded_loaders=threaded,resource_loader=loader)
    try:
        page=browser.new_profile().new_page();page.set_content('<body>',url='https://xhr.test/')
        assert page.evaluate('''(() => {
            const order=[];const xhr=new XMLHttpRequest();
            setTimeout(()=>order.push('timer'),0);
            xhr.onreadystatechange=()=>order.push('state:'+xhr.readyState);
            for(const name of ['loadstart','progress','load','loadend','error'])
                xhr.addEventListener(name,()=>order.push(name));
            xhr.open('POST','/data',false);xhr.setRequestHeader('X-Example','yes');
            xhr.send('route=test');order.push('after');
            return [order,xhr.status,xhr.responseText,xhr.responseURL,xhr.getResponseHeader('x-example')];
        })()''') == [['state:1','state:4','load','loadend','after'],200,
                    '{"ready":true}','https://xhr.test/data','response']
        assert calls==[('https://xhr.test/data','POST','route=test','yes')]
        assert page.evaluate('document.readyState')=='complete'
    finally:
        browser.close()


def test_sync_xhr_window_disallows_timeout_and_response_type(page):
    page.set_content('<body>',url='https://xhr.test/')
    assert page.evaluate('''(() => {
        const results=[];function check(fn){try{fn();results.push('ok')}
            catch(error){results.push(error.name)}}
        let x=new XMLHttpRequest();x.timeout=1;check(()=>x.open('GET','/',false));
        x=new XMLHttpRequest();x.responseType='text';check(()=>x.open('GET','/',false));
        x=new XMLHttpRequest();x.open('GET','/',false);
        check(()=>x.timeout=0);check(()=>x.responseType='text');
        x.open('GET','/',true);check(()=>x.timeout=1);
        return results;
    })()''') == ['InvalidAccessError']*4+['ok']


@pytest.mark.parametrize('failure', ['network','cors'])
def test_sync_xhr_failure_throws_without_async_error_events(failure):
    def loader(url, request):
        if failure=='network':raise OSError('offline')
        return {'status':200,'body':'secret','headers':{'content-type':'text/plain'}}
    browser=Browser(real_network=False,resource_loader=loader)
    try:
        page=browser.new_profile().new_page();page.set_content('<body>',url='https://xhr.test/')
        assert page.evaluate('''(() => {
            const xhr=new XMLHttpRequest(),events=[];
            for(const name of ['load','loadend','error'])xhr.addEventListener(name,()=>events.push(name));
            xhr.open('GET','https://other.test/data',false);
            try{xhr.send();return ['unexpected']}
            catch(error){return [error.name,xhr.readyState,xhr.status,xhr.responseText,events]}
        })()''') == ['NetworkError',4,0,'',[]]
    finally:
        browser.close()


def test_sync_xhr_http_error_remains_a_readable_response():
    browser=Browser(real_network=False,resource_loader=lambda u,r:{'status':418,'body':'actual failure'})
    try:
        page=browser.new_profile().new_page();page.set_content('<body>',url='https://xhr.test/')
        assert page.evaluate('''(() => {const x=new XMLHttpRequest();x.open('GET','/data',false);
            x.send();return [x.status,x.responseText,x.readyState]})()''') == [418,'actual failure',4]
    finally:
        browser.close()


@pytest.mark.parametrize('asynchronous',[False,True])
@pytest.mark.parametrize('argument',['null','undefined',''])
def test_xhr_nullish_send_reaches_transport_as_no_body(asynchronous,argument):
    calls=[]
    def loader(url,request):
        calls.append(request)
        return {'body':'ok'}
    browser=Browser(real_network=False,resource_loader=loader,threaded_loaders=True)
    try:
        page=browser.new_profile().new_page();page.set_content('<body>',url='https://xhr.test/')
        page.evaluate('''globalThis.done=false;globalThis.answer=null;
            const xhr=new XMLHttpRequest();xhr.open('POST','/data','''+
            str(asynchronous).lower()+''');
            xhr.onloadend=()=>{answer=[xhr.status,xhr.responseText];done=true};
            xhr.send('''+argument+''');''')
        page.wait_for('done',timeout=2)
        assert page.evaluate('answer')==[200,'ok']
        assert len(calls)==1 and calls[0]['body'] is None
        assert not any(name.lower()=='content-type' for name in calls[0]['headers'])
    finally:
        browser.close()
