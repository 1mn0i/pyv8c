# 不可变 IdlSpec 迁移说明

本轮为开发阶段的破坏性 API 调整，不提供旧装配接口兼容层。

## 声明

`interface(name, parent, exposed_in=...)` 仅登记身份、父接口和 exposure。构造器、
模板、成员及拦截器全部写入 `pyv8.IdlSpec`。删除 ConstructorConfig、进程级
exposed_constructs 和 js_attributes/js_methods 包装器。

```python
from pyv8 import IdlSpec, AttributeSpec, MethodSpec
from pyv8c.engine.registry import interface

@interface("Example", None)
class Example:
    __v8_idl_spec__ = IdlSpec(
        has_constructor=1, constructor_behavior=1,
        attributes=(AttributeSpec(name="value", getter="get_value"),),
        methods=(MethodSpec(name="reset", callback="reset"),),
    )
```

示例接口还需要匹配的 baseline 条目及实现回调。描述对象只读；修改配置使用
`spec.replace(methods=...)`，未改变的 tuple/描述对象共享，不进行字典合并。
常量分别使用 `ConstantSpec`、`ConstantCallbackSpec`。Python 回调可用名称或 callable；
Faker 回调显式声明 `callback_type=CallbackType.kJs`，并提供回调名称。

拦截器的最终字段为 callable 或 None，不再使用布尔开关。实现 mixin 自己声明
IdlSpec（例如只读 indexed-property mixin），编译器收集这些引用，不再通过扫描
`__v8_*__` 方法名推测是否启用。hook 与异常协议保持独立。

## 编译与绑定

- Universe 持有一个编译缓存；baseline 描述符与签名优先，接口自身成员优先于实现 mixin。
- JS 父接口成员通过原型链继承，不复制到子接口；baseline 明确声明的子接口成员除外。
- 每个接口首次编译时解析回调、分析签名并选择 handler；普通 realm 复用结果。
- 只有需要 `_realm` 的静态回调和构造器适配器按 realm 绑定，不再生成 callback-owner 类。
- 显式 IDL 模板持有回调快照，不修改实现函数，也不追踪后续的类成员替换。
- `strict_validation` 在 Universe 内执行一次完整编译审计；正常模式按需编译。

## 注册与失败处理

Context 复制 `class_configs` 注册表；`update_configs` 先检查整批输入再替换自己的注册表。
同一类型重复提交同一个 IdlSpec 对象是无操作；不同对象报错。模板已经安装后不得首次
注册其配置。需要改配置时创建新的 Context。

BindingSession 在一个批次内准备目标接口和祖先，native 提交成功后才更新 session
的配置和 materialized 状态；失败可重试。Page 与 Worker 均先准备全局接口配置再创建
native Context。未声明 IdlSpec 的普通 Python 桥接保持独立反射路径。

## 验证

原生字段范围、不可变性、replace 共享、命名回调快照、GC 循环、Isolate 隔离和关闭
引用释放见 `_pyv8/tests/test_idl_spec.py`。编译复用、realm 绑定和失败原子性见
`tests/unit/test_idl_compilation.py`。性能口径与实测结果见 `IDL_PERFORMANCE.md`。

