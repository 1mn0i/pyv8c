import pyv8

class FlagConstructor:
    kDontAll = 0
    kNew = 1 << 0
    kCall = 1 << 1
    kAll = kNew | kCall

class A: 
    __v8_exposed__ = True

class Calculator(A):
    __v8_constructor__ = FlagConstructor.kAll
    __v8_method__ = (
        # (name, cb, length, CallbackType, FlagLocation, V8Attribute, FlagReceiverCheck, 
        # cross_origin_check, SideEffectType)
        ("add", "fn_add", 0, 0, 0, 0, 0, 0, 0),
        ("subtract", "fn_subtract", 0, 0, 0, 0, 0, 0, 0),
    )

    def __init__(self,win=None):
        pass
        print(win)

    def fn_add(self, a, b, **kw):
        return a + b
    
    def fn_subtract(self, a, b, **kw):
        return a - b

print("111111")
class Window: pass
context = pyv8.Context(Window())
# calc = Calculator()
print("33333")
context.expose(Calculator)
# context.calc = calc
print("44444")
add_result = context.exec_js("Calculator.__proto__")
print("555555")
print(add_result)