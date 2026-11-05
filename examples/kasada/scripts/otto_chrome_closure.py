import asyncio, ast, json, re, tempfile, subprocess
from pathlib import Path
from playwright.async_api import async_playwright
root=Path(tempfile.gettempdir())
source=Path(root,'otto_vm_calls_full.py').read_text(encoding='utf-8-sig')
module=ast.parse(source)
prefix=next(ast.literal_eval(n.value) for n in ast.walk(module) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='prefix' for t in n.targets))
pattern=r'new Function\((\w+)\+(\w+)\((\w+)\.slice\(0,-1\),(\w+)\)\+"\}"\)\(\)'
async def main():
 async with async_playwright() as pw:
  chrome=subprocess.Popen([r'C:\Program Files\Google\Chrome\Application\chrome.exe','--remote-debugging-port=19381','--user-data-dir='+tempfile.mkdtemp(prefix='otto_chrome_manual_'),'--proxy-server=http://127.0.0.1:7890','--no-first-run','--start-minimized','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  browser=None
  for _ in range(60):
   try: browser=await pw.chromium.connect_over_cdp('http://127.0.0.1:19381'); break
   except Exception: await asyncio.sleep(0.25)
  if browser is None: chrome.terminate(); return
  context=browser.contexts[0]
  page=await context.new_page()
  print('webdriver:',await page.evaluate('navigator.webdriver'),flush=True)
  async def instrument(route):
   response=await route.fetch();s=await response.text()
   observer = r"""function __observe(fn,receiver,args){try{let v=args&&args[0];if(typeof fn==='function'&&fn.name==='encode'&&typeof v==='string'&&v.length>10000&&v.startsWith('[\"2:'))console.debug('__OTTO_CAPTURE__'+v)}catch(e){}}function __patchCalls(src){let out=src.replace(/(?<![\w$.])(\w+)\.apply\((\w+),(\w+)\)/g,'(__observe($1,$2,$3),$1.apply($2,$3))');try{new Function('__observe',out);return out}catch(e){return src}}"""
   s,closed=re.subn(r'(\(function\(\)\{var\s+\w+;)',lambda m:m.group(1)+observer,s,count=1)
   s,n=re.subn(pattern,lambda m:"new Function('__observe',__patchCalls("+m[1]+'+'+m[2]+'('+m[3]+'.slice(0,-1),'+m[4]+')+"}"))(__observe)',s)
   print('instrumented closure:',closed,'factory:',n,flush=True)
   await route.fulfill(response=response,body=s.encode('utf8'))
  await page.route('**/ips.js*',instrument)
  tasks=[];done=asyncio.Event();captures=[]
  def console(msg):
   marker='__OTTO_CAPTURE__'
   if msg.text.startswith(marker):
    captures.append(msg.text[len(marker):]);print('captured array chars:',len(captures[-1]),flush=True)
  page.on('console',console)
  async def collect(req):
   try:
    props=await req.frame.evaluate("() => Object.getOwnPropertyNames(window).filter(x=>x.startsWith('__vm')||x.startsWith('__patch'))")
    print('analysis global properties:',props,flush=True)
    resp=await req.response();body=await resp.text() if resp else '';print('tl status',resp.status if resp else None,'body',repr(body),flush=True)
    if captures:
     Path(root,'otto_chrome_closure_sample.json').write_text(captures[-1],encoding='utf8')
     print('sample captured',flush=True)
   except Exception as e: print('capture error',type(e).__name__,str(e)[:200],flush=True)
   finally: done.set()
  page.on('request',lambda req: tasks.append(asyncio.create_task(collect(req))) if req.method=='POST' and req.url.split('?')[0].endswith('/tl') else None)
  try:
   await page.goto('https://www.otto.de/',wait_until='domcontentloaded',timeout=45000)
   await asyncio.wait_for(done.wait(),45)
  except Exception as e: print('navigation/wait',type(e).__name__,str(e)[:200],flush=True)
  if tasks: await asyncio.gather(*tasks,return_exceptions=True)
  await browser.close()
  chrome.terminate()
asyncio.run(main())
