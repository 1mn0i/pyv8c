"""Pages shared by offline wire tests and Chrome comparison tests."""

GET_FORM = ('<!doctype html><html><body><form method=get action="/echo?old=1">'
            '<p><input name=sp value="hello world"></p>'
            '<p><button name=b value="x y">Go</button></p></form></body></html>')
EMPTY_GET = ('<!doctype html><html><body><form method=get action="/echo?old=1">'
             '<p><button>Go</button></p></form></body></html>')
KINDS = ('<!doctype html><html><body><form method=post action="/echo">'
         '<p><input name=t value="v"></p>'
         '<p><input type=checkbox name=on checked></p>'
         '<p><input type=checkbox name=off></p>'
         '<p><input name=dis value="x" disabled></p>'
         '<p><input value="noname"></p>'
         '<p><select name=s><option value=o1 selected>1</option>'
         '<option value=o2>2</option></select></p>'
         '<p><textarea name=ta>text</textarea></p>'
         '<p><button name=b value=bv>Go</button></p></form></body></html>')
PLAIN_ENC = ('<!doctype html><html><body>'
             '<form method=post action="/echo" enctype="text/plain">'
             '<p><input name=a value="1"></p><p><input name=b value="2"></p>'
             '<p><button>Go</button></p></form></body></html>')
MULTIPART_ENC = ('<!doctype html><html><body>'
                 '<form method=post action="/echo" enctype="multipart/form-data">'
                 '<p><input name=a value="1"></p><p><button>Go</button></p>'
                 '</form></body></html>')



PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
PAGE = """<!doctype html><html><head><meta charset=utf-8><title>probe</title>
<link rel=stylesheet href="/sub.css"></head><body>
<img src="/img.png"><script src="/sub.js"></script>
<video src="/clip.mp4" muted></video>
<iframe src="/frame.html"></iframe>
</body></html>"""

