"""Canvas operations used by browser fingerprint and ordinary page drawing."""

from pyv8c import Browser


def _evaluate(script):
    browser = Browser(real_network=False)
    try:
        page = browser.new_profile().new_page()
        page.set_content('<body></body>', url='https://canvas.test/')
        return page.evaluate(script)
    finally:
        browser.close()


def test_affine_transform_and_half_open_rectangle_pixels_match_chrome():
    result = _evaluate('''(() => {
      const canvas=document.createElement('canvas'); canvas.width=canvas.height=10;
      const ctx=canvas.getContext('2d');
      const matrix=()=>{const m=ctx.getTransform();return [m.a,m.b,m.c,m.d,m.e,m.f]};
      const pixel=(x,y)=>Array.from(ctx.getImageData(x,y,1,1).data);
      const initial=matrix();
      ctx.translate(2,3); const translated=matrix();
      ctx.save(); ctx.rotate(Math.PI/2); ctx.transform(1,0,0,1,4,5);
      const combined=matrix(); ctx.resetTransform(); const reset=matrix();
      ctx.restore(); const restored=matrix();
      ctx.fillStyle='red'; ctx.fillRect(0,0,1,1);
      const translatedPixels=[pixel(2,3),pixel(3,3),pixel(2,4)];
      ctx.clearRect(0,0,1,1); const cleared=pixel(2,3);
      ctx.resetTransform(); ctx.translate(4,4); ctx.rotate(Math.PI/2);
      ctx.fillRect(0,0,1,1);
      const rotatedPixels=[pixel(3,4),pixel(4,4),pixel(3,5),pixel(4,5)];
      return {initial,translated,combined,reset,restored,translatedPixels,
              cleared,rotatedPixels};
    })()''')
    transparent = [0, 0, 0, 0]
    red = [255, 0, 0, 255]
    assert result == {
        'initial': [1, 0, 0, 1, 0, 0],
        'translated': [1, 0, 0, 1, 2, 3],
        'combined': [6.123233995736766e-17, 1, -1, 6.123233995736766e-17, -3, 7],
        'reset': [1, 0, 0, 1, 0, 0],
        'restored': [1, 0, 0, 1, 2, 3],
        'translatedPixels': [red, transparent, transparent],
        'cleared': transparent,
        'rotatedPixels': [red, transparent, transparent, transparent],
    }


def test_image_data_dirty_write_ignores_transform_and_uses_live_data():
    result = _evaluate('''(() => {
      const canvas=document.createElement('canvas'); canvas.width=canvas.height=10;
      const ctx=canvas.getContext('2d');
      const pixel=(x,y)=>Array.from(ctx.getImageData(x,y,1,1).data);
      const data=ctx.createImageData(2,2);
      data.data.set([255,0,0,255, 0,255,0,255,
                     0,0,255,255, 255,255,0,255]);
      ctx.translate(2,3);
      ctx.putImageData(data,6,6,1,0,1,2);
      const dirty=[pixel(6,6),pixel(7,6),pixel(6,7),pixel(7,7)];
      const blank=ctx.createImageData(data);
      const errors=[];
      for (const operation of [()=>ctx.createImageData(0,1),
                               ()=>ctx.createImageData(1),
                               ()=>ctx.putImageData({},0,0)]) {
        try {operation();errors.push('accepted')} catch(e) {errors.push(e.name)}
      }
      return {dirty,blank:[blank.width,blank.height,Array.from(blank.data.slice(0,4))],errors};
    })()''')
    assert result == {
        'dirty': [[0, 0, 0, 0], [0, 255, 0, 255],
                  [0, 0, 0, 0], [255, 255, 0, 255]],
        'blank': [2, 2, [0, 0, 0, 0]],
        'errors': ['IndexSizeError', 'TypeError', 'TypeError'],
    }
