from pyv8 import IdlSpec, AttributeSpec, MethodSpec
import unittest
import pyv8

class TestMultipleContext(unittest.TestCase):
    """测试不同上下文的创建和交互"""
    
    def test_iframe(self):
        """测试iframe"""
        class Document:

            __v8_idl_spec__ = IdlSpec(
                methods=(
                    MethodSpec(
                        name="createElement",
                        callback="fn_createElement",
                        length=1,
                        location=1,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_check=0,
                        side_effect=0,
                    ),
                ),
            )
            
            def fn_createElement(self, *args, **kw):
                name = str(args[0])
                if name.lower() == "iframe":
                    return HTMLIFrameElement()

        class Window: 
            __v8_idl_spec__ = IdlSpec(
                cross_origin_check="__v8_cross_origin_check__",
                global_this=True,
                attributes=(
                    AttributeSpec(
                        name="window",
                        getter="get_window",
                        setter=None,
                        location=0,
                        property_flags=4,
                        receiver_check=0,
                        cross_origin_get_check=1,
                        cross_origin_set_check=0,
                        side_effect=1,
                    ),
                    AttributeSpec(
                        name="document",
                        getter="get_document",
                        setter=None,
                        location=0,
                        property_flags=4,
                        receiver_check=0,
                        cross_origin_get_check=0,
                        cross_origin_set_check=0,
                        side_effect=1,
                    ),
                ),
            )

            # 控制是否允许跨域访问 accessor为访问对象 self为被访问对象
            # accessor（window）想要访问 self（window） 中的属性时，会调用这个方法
            # 返回True为允许访问 反之拒绝
            def __v8_cross_origin_check__(self, accessor):
                print("accessor id: ", id(accessor))
                print("self id: ", id(self))
                return True

            def __init__(self, win=None):
                self.document = Document()

            def get_window(self):
                return self

            def get_document(self):
                return self.document

        class HTMLIFrameElement:
            def __init__(self, win=None):
                self._contentWindow = None

            __v8_idl_spec__ = IdlSpec(
                attributes=(
                    AttributeSpec(
                        name="contentWindow",
                        getter="get_contentWindow",
                        setter=None,
                        location=1,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_get_check=0,
                        cross_origin_set_check=0,
                        side_effect=1,
                    ),
                ),
            )
            
            def get_contentWindow(self):
                if self._contentWindow is None:
                    self._contentWindow = Window()
                    print("Ifrome win id: ", id(self._contentWindow))
                    iframe_ctx = pyv8.Context(self._contentWindow, ctx_type='Ifrome')

                    # 绑定到window上 防止iframe_ctx被垃圾回收
                    self._contentWindow._ctx = iframe_ctx
                return self._contentWindow
        
        win = Window()
        win_ctx = pyv8.Context(win, ctx_type='Top')
        print("Top win id: ", id(win))
        win_ctx.exec_js("var iframe = document.createElement('iframe'); iframe.contentWindow.aaa=111")
        self.assertEqual(win_ctx.exec_js("iframe.contentWindow == window"), False)
        self.assertEqual(win_ctx.exec_js("window.aaa"), pyv8.Undefined)
        self.assertEqual(win_ctx.exec_js("iframe.contentWindow.aaa"), 111)

if __name__ == "__main__":
    unittest.main()
