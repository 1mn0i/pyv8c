from pyv8 import IdlSpec, AttributeSpec, MethodSpec
import unittest

import pyv8


class TestHookContracts(unittest.TestCase):
    """测试浏览器补环境 hook 收到的参数、结果、错误和上下文类型。"""

    def setUp(self):
        class Window:
            __v8_idl_spec__ = IdlSpec(
                global_this=True,
            )

        self.context = pyv8.Context(Window(), hook=True, ctx_type="Top")

    def tearDown(self):
        del self.context

    def test_get_set_method_and_construct_hooks(self):
        """四类 hook 应报告稳定且可用于逆向日志的结构化信息。"""

        events = []

        class HookTarget:
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=pyv8.FlagConstructor.kNew,
                has_constructor=1,
                attributes=(
                    AttributeSpec(
                        name="value",
                        getter="get_value",
                        setter="set_value",
                        callback_type=pyv8.CallbackType.kPy,
                        location=pyv8.FlagLocation.kPrototype,
                        property_flags=pyv8.V8Attribute.kNone,
                        receiver_check=pyv8.FlagReceiverCheck.kCheck,
                        cross_origin_get_check=pyv8.FlagCrossOriginCheck.kCheck,
                        cross_origin_set_check=pyv8.FlagCrossOriginCheck.kCheck,
                        side_effect=pyv8.SideEffectType.kHasSideEffect,
                    ),
                ),
                methods=(
                    MethodSpec(
                        name="add",
                        callback="fn_add",
                        length=2,
                        callback_type=pyv8.CallbackType.kPy,
                        location=pyv8.FlagLocation.kPrototype,
                        property_flags=pyv8.V8Attribute.kNone,
                        receiver_check=pyv8.FlagReceiverCheck.kCheck,
                        cross_origin_check=pyv8.FlagCrossOriginCheck.kCheck,
                        side_effect=pyv8.SideEffectType.kHasSideEffect,
                    ),
                ),
            )

            def __init__(self, win=None):
                self._value = 5

            def get_value(self):
                return self._value

            def set_value(self, value):
                self._value = value

            def fn_add(self, left, right):
                return left + right

            def __v8_get_hook__(self, name, value, error, ctx_type):
                events.append(("get", name, value, error, ctx_type))

            def __v8_set_hook__(self, name, value, error, ctx_type):
                events.append(("set", name, value, error, ctx_type))

            def __v8_method_hook__(
                self, name, arguments, value, has_error, ctx_type
            ):
                events.append(
                    ("method", name, list(arguments), value, has_error, ctx_type)
                )

            @staticmethod
            def __v8_construct_hook__(
                name, arguments, value, has_error, is_construct_call, ctx_type
            ):
                events.append(
                    (
                        "construct",
                        name,
                        list(arguments),
                        has_error,
                        is_construct_call,
                        ctx_type,
                    )
                )

        target = HookTarget()
        self.context.expose(HookTarget, target=target)
        self.context.exec_js("faker.hook = true")
        events.clear()

        result = self.context.exec_js(
            "[target.value, (target.value = 7), target.add(2, 3)]"
        )
        self.assertEqual(result, [5, 7, 5])
        self.context.exec_js("new HookTarget()")

        get_event = next(event for event in events if event[0] == "get")
        set_event = next(event for event in events if event[0] == "set")
        method_event = next(event for event in events if event[0] == "method")
        construct_event = next(
            event for event in events if event[0] == "construct"
        )

        self.assertEqual(get_event[:3], ("get", "value", 5))
        self.assertIs(get_event[3], pyv8.Undefined)
        self.assertEqual(get_event[4], "Top")

        self.assertEqual(set_event[:3], ("set", "value", 7))
        self.assertIs(set_event[3], pyv8.Undefined)
        self.assertEqual(set_event[4], "Top")

        self.assertEqual(method_event, ("method", "add", [2, 3], 5, False, "Top"))
        self.assertEqual(
            construct_event,
            ("construct", "HookTarget", [], False, True, "Top"),
        )

    def test_method_hook_observes_python_exception(self):
        """方法失败时 hook 应先观察错误，再把原 Python 异常传回调用方。"""

        events = []

        class HookFailure(RuntimeError):
            __v8_error__ = True

        class ThrowingTarget:
            __v8_idl_spec__ = IdlSpec(
                methods=(
                    MethodSpec(
                        name="fail",
                        callback="fn_fail",
                        length=0,
                        callback_type=0,
                        location=1,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_check=0,
                        side_effect=0,
                    ),
                ),
            )

            def fn_fail(self):
                raise HookFailure("hook method boom")

            def __v8_method_hook__(
                self, name, arguments, value, has_error, ctx_type
            ):
                events.append((name, list(arguments), value, has_error, ctx_type))

        self.context.expose(target=ThrowingTarget())
        self.context.exec_js("faker.hook = true")
        events.clear()

        with self.assertRaisesRegex(HookFailure, "hook method boom"):
            self.context.exec_js("target.fail()")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0:2], ("fail", []))
        self.assertIsInstance(events[0][2], HookFailure)
        self.assertTrue(events[0][3])
        self.assertEqual(events[0][4], "Top")
        self.assertEqual(self.context.exec_js("20 + 22"), 42)

    def test_method_hook_uses_each_javascript_alias(self):
        """同一 Python callable 暴露为多个名称时应分别报告对应 JS 名称。"""

        names = []

        class AliasTarget:
            __v8_idl_spec__ = IdlSpec(
                methods=(
                    MethodSpec(
                        name="firstAlias",
                        callback="shared",
                        length=0,
                        callback_type=0,
                        location=1,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_check=0,
                        side_effect=0,
                    ),
                    MethodSpec(
                        name="secondAlias",
                        callback="shared",
                        length=0,
                        callback_type=0,
                        location=1,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_check=0,
                        side_effect=0,
                    ),
                ),
            )

            def shared(self):
                return 42

            def __v8_method_hook__(
                self, name, arguments, value, has_error, ctx_type
            ):
                names.append(name)

        self.context.expose(target=AliasTarget())
        self.context.exec_js("faker.hook = true")
        names.clear()

        self.assertEqual(
            self.context.exec_js("[target.firstAlias(), target.secondAlias()]"),
            [42, 42],
        )
        self.assertEqual(names, ["firstAlias", "secondAlias"])

    def test_hook_exceptions_propagate_and_context_recovers(self):
        """四类 hook 自身抛错时均应传播异常并清理运行时状态。"""

        class HookCallbackFailure(RuntimeError):
            __v8_error__ = True

        class BrokenHookTarget:
            active_hook = None
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=pyv8.FlagConstructor.kNew,
                has_constructor=1,
                attributes=(
                    AttributeSpec(
                        name="value",
                        getter="get_value",
                        setter="set_value",
                        callback_type=0,
                        location=1,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_get_check=0,
                        cross_origin_set_check=0,
                        side_effect=0,
                    ),
                ),
                methods=(
                    MethodSpec(
                        name="ping",
                        callback="fn_ping",
                        length=0,
                        callback_type=0,
                        location=1,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_check=0,
                        side_effect=0,
                    ),
                ),
            )

            def __init__(self, win=None):
                self._value = 1

            def get_value(self):
                return self._value

            def set_value(self, value):
                self._value = value

            def fn_ping(self):
                return "pong"

            def _raise_for(self, hook_name):
                if self.active_hook == hook_name:
                    raise HookCallbackFailure(f"{hook_name} hook boom")

            def __v8_get_hook__(self, name, value, error, ctx_type):
                self._raise_for("get")

            def __v8_set_hook__(self, name, value, error, ctx_type):
                self._raise_for("set")

            def __v8_method_hook__(
                self, name, arguments, value, has_error, ctx_type
            ):
                self._raise_for("method")

            @staticmethod
            def __v8_construct_hook__(
                name, arguments, value, has_error, is_construct_call, ctx_type
            ):
                if BrokenHookTarget.active_hook == "construct":
                    raise HookCallbackFailure("construct hook boom")

        target = BrokenHookTarget()
        self.context.expose(BrokenHookTarget, target=target)
        self.context.exec_js("faker.hook = true")

        operations = {
            "get": "target.value",
            "set": "target.value = 2",
            "method": "target.ping()",
            "construct": "new BrokenHookTarget()",
        }
        for hook_name, source in operations.items():
            with self.subTest(hook=hook_name):
                BrokenHookTarget.active_hook = hook_name
                with self.assertRaisesRegex(
                    HookCallbackFailure, f"{hook_name} hook boom"
                ):
                    self.context.exec_js(source)
                BrokenHookTarget.active_hook = None
                self.assertEqual(self.context.exec_js("20 + 22"), 42)


if __name__ == "__main__":
    unittest.main()
