# IDL 声明、编译与迁移

[返回文档索引](README.md)。保留此文件名作为迁移入口；以下描述当前不可变 IdlSpec 架构，而不是旧字典装配接口。

## 两类声明不要混淆

| 声明 | 所属项目 | 用途 |
| --- | --- | --- |
| InterfaceSpec / __idl_spec__ | pyv8c.engine.registry | 接口名称、父接口、exposure 身份 |
| IdlSpec / __v8_idl_spec__ | pyv8 原生桥 | 构造、模板、成员、handler 等安装描述 |
| 实现层转换 | pyv8c.foundation.arguments 与各 Web API | 方法、构造器和 setter 按 Web API 语义自行转换与报错 |
| BindingSession.configs | 每个 realm | Python 类型到不可变 IdlSpec 的注册映射 |

下面是声明方式的示例，不是自动加入 Browser 的插件：

```python
from pyv8 import AttributeSpec, IdlSpec, MethodSpec
from pyv8c.engine.registry import interface

@interface("Example", None)
class Example:
    __v8_idl_spec__ = IdlSpec(
        has_constructor=1,
        constructor_behavior=1,
        attributes=(AttributeSpec(name="value", getter="get_value"),),
        methods=(MethodSpec(name="reset", callback="reset"),),
    )

    def __init__(self, **kwargs):
        self.value = 0

    def get_value(self):
        return self.value

    def reset(self):
        self.value = 0
```

要用于浏览器，还需登记实现、提供匹配 baseline 并完成 realm 装配。
interface 装饰器只写身份声明，不会向进程全局暴露表偷偷注册类。

## 原生描述约束

IdlSpec、AttributeSpec、MethodSpec、ConstantSpec、ConstantCallbackSpec 为显式描述对象。
成员集合使用 tuple；配置只读，修改时通过 replace 创建新描述，未改变的成员可共享。
Python 回调支持名称或 callable；JS 实现回调显式设置 callback_type，并提供已有 Faker 回调名称。
这类业务 JS 实现与已移除的“JS trace 输出支持”不是一回事。

named/indexed/cross-origin handler 通过 IdlSpec 字段显式选择，不因类上存在某个方法名就自动启用。
实现 mixin 可以声明自己的 IdlSpec，编译器收集声明；不应重新引入扫描 __v8_*__ 命名来猜测协议。
trace 不属于类声明；错误桥接协议也与 trace 独立。

## 编译与 realm 绑定

1. InterfaceCatalog 提供 baseline 继承、exposure 和成员信息。
2. InterfaceCompiler 结合手写声明、mixin、baseline 和 stub，首次生成结构描述。
3. Universe 缓存结构与创建期 Python 回调检查；普通新页面复用 realm 无关结果。不存在运行时参数计划。
4. 依赖 _realm 的静态回调及构造适配器按 session 绑定，不再生成 callback-owner 类。
5. BindingSession 将本批接口和祖先准备为 staged 配置，native 接受后再更新本地状态。

baseline 的成员描述符和 length 提供可观察约束；signature 仅供离线参考，不参与调用。
参数存在性、转换、重载、校验顺序与 Promise 拒绝由各 Web API 实现负责，详见 [开发规范](DEVELOPMENT.md)。
接口自身实现优先于实现 mixin。
JS 父接口成员通过原型链继承，不无条件复制到子接口；baseline 明确声明在子接口的成员仍按其描述安装。
显式原生模板持有回调快照，不追踪之后对 Python 类成员的替换。
strict_validation 在 Universe 内完整编译一次，只检查绑定结构，不检查参数语义；它不是完整兼容性审计或“无 stub 模式”。

## 注册和失败原子性

native Context 构造时复制 class_configs 注册表。后续 lazy materialization 使用 update_configs 提交，而不是修改一个期待被 native 自动监听的 dict。
同类型重复提交同一个 IdlSpec 是无操作；不同描述对象冲突会失败；模板已安装后不能首次补注册其描述。
要改变已安装配置，应创建新 Context。

Session 在 native 批提交失败时不发布本批 materialized/configs 状态，允许纠正后重试。
Page 和 Worker 都先准备全局接口配置，再创建 native Context，然后绑定 session 并声明 lazy 名称。
没有显式 IdlSpec 的普通 Python 桥接保留其独立反射路径，不应与浏览器显式安装机制混写。

## 从旧实现迁移

- 用描述对象代替 ClassConfig 字典/位置元组、ConstructorConfig 与旧 js_attributes/js_methods 包装。
- 用冻结 registry、每 Browser 的 Universe、每 realm 的 BindingSession 代替可变全局 exposure 表。
- 用 Context 级 trace_callback 和开关代替 cls 上的 __v8_*_hook__。
- 用 realm capability 绑定静态行为，不捕获 Page 到全局编译缓存。
- 不用修改已存在的 IdlSpec 或 Python 类来实现旧 Context 的配置热更新。

## 代码与回归

[registry](../pyv8c/engine/registry.py)、[compiler](../pyv8c/engine/assembly.py)、[wrappers](../pyv8c/engine/wrappers.py)、[sessions](../pyv8c/engine/sessions.py)。

[编译测试](../tests/unit/test_idl_compilation.py) 覆盖复用、realm 绑定和提交失败；
[native IdlSpec 测试](../../_pyv8/tests/test_idl_spec.py) 覆盖不可变性、replace、回调快照与原生限制。
baseline 输入格式见 [字段说明](interface-baseline-fields.md)。
