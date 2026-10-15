# 任务调度与事件循环

本文描述当前源码已经实现的任务模型：一个 realm 的队列、车道、轮次配额，网络回调 / 定时器 / 事件 / JS 执行各自落在哪里，它们之间如何互相影响；末尾给出与 Chromium 调度模型的对照。行为以 `tests/` 为最终事实来源。

## 1. 模型总览

### 1.1 单位：一个 realm 一个队列

页面 realm 与 worker realm 各有一个 `TaskQueue`，各有自己的 owner 线程与 V8 context；队列同时记账"还在路上的工作"，它是 idle 判定的依据。

```63:79:pyv8c/foundation/tasks.py
class TaskQueue:
    """One realm's task sources, timers and pending-work counters."""

    def __init__(self, host=None, *, error_sink: Callable[[BaseException], None] | None = None):
        self._host = host if host is not None else _NullTaskHost()
        self._error_sink = error_sink
        self.timers: list[tuple[float, int, Callable[..., object], float | None]] = []
        self.counter = itertools.count(1)
        self.cancelled: set[int] = set()
        # Single-threaded pop with append-only producers; ``_signal`` fences a sleep.
        self.posted: deque[Callable[..., object]] = deque()
        self.errors: list[BaseException] = []
        # Guards the errors and the in-flight counters, which other threads touch.
        self._lock = threading.Lock()
        self.network_pending = 0
        self.worker_pending = 0
        self.loader_pending = 0
```

### 1.2 车道：两条任务源 + 一个死线堆 + V8 微任务

队列里没有"网络源 / 定时源 / 事件源"的区分，只有 parser 与 posted 两个 task source；timer 是死线堆而不是任务源，微任务归 V8 context 所有。

```30:39:pyv8c/foundation/tasks.py
class TaskSource(enum.Enum):
    """Task queues drained by one realm event-loop turn, in priority order.

    Only sources callers post into are listed: timers live in this queue's own
    deadline-ordered heap and microtasks belong to V8.  Embedder commands travel on
    the executor's own queue, drained before any turn, so page work cannot starve them.
    """

    PARSER = "parser"         # parser resumption, gated by the parser-blocking state
    POSTED = "posted"         # network / worker / iframe completion callbacks
```

### 1.3 一个轮次的配额

这是整个调度公平性的根：每个轮次跑一个 parser 任务（未被 parser-blocking 挡住时）、一个 posted 任务、**至多一个**到期 timer。

```268:285:pyv8c/foundation/tasks.py
    def run_turn(self, *, allow_parser: bool = True) -> bool:
        """Perform one event-loop turn; return ``True`` when work happened.

        Must be called from the realm owner thread; the executor interleaves these
        turns with its own commands so a long page never starves ``evaluate``/``close``.
        """
        if self.closed:
            return False
        worked = False
        # Only ask about parser-blocking when there is parser work to gate: it is the
        # most expensive check in a turn.
        if allow_parser and self._parser and not self._host.parser_blocked():
            self.run_callback(self._parser.popleft())
            worked = True
        if self.posted:
            self.run_callback(self.posted.popleft())
            worked = True
        return self._run_due_timers() or worked
```

同一批注释还说明了重复 timer 只允许一个事件循环轮次跑一次：`One timer per turn: a repeating zero-delay timer must not monopolise the realm.`（`pyv8c/foundation/tasks.py:251`）

### 1.4 驱动者：owner 线程上的 executor

`RealmExecutor` 独占一个线程；每个轮次之前先清空宿主命令，所以宿主命令永远排在页面任务之前。没有工作可做时按下一个定时器死线睡眠，而不是轮询。

```336:346:pyv8c/foundation/executor.py
    def _pump(self):
        """Drain the commands, then run one turn, then sleep until something arrives."""
        self._drain_commands()
        if self._closed.is_set():
            return
        if self._run_turn():
            # A turn ends its task, which is where the specification puts the
            # rejection events for anything that task rejected.
            self._settle_rejections()
            return
        self._sleep(self._next_deadline())
```

```348:353:pyv8c/foundation/executor.py
    def _drain_commands(self):
        # Commands first: evaluate/goto/close must never queue behind page work.
        while not self._closed.is_set():
            command = self._next_command()
            if command is None:
                return
```

### 1.5 投递：任何线程都能投，只有 owner 线程执行

投递只往 `deque` 上 append，然后叫醒 owner 线程；唤醒走 event（空闲停放）或 asyncio loop（朝死线等待时）。

```191:204:pyv8c/foundation/tasks.py
    def post(self, callback, source: TaskSource = TaskSource.POSTED):
        """Queue *callback* on the given task source.

        Parser work is held separately so a parser-blocking script can gate resumption
        without freezing network delivery or timers.
        """
        if self.closed:
            return False
        if source is TaskSource.PARSER:
            self._parser.append(callback)
        else:
            self.posted.append(callback)
        self._signal()
        return True
```

```142:154:pyv8c/foundation/executor.py
    def wakeup(self):
        """Rouse a sleeping owner thread: the event for an idle park, the loop for a deadline."""
        self._wake.set()
        if not self._timed:
            # The single-attribute read may be stale, and then the park's own re-check
            # sees the event instead: the flag only avoids the loop hop.
            return
        loop = self._loop
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            # The loop is closed: the thread is gone or on its way out.
            pass
```

### 1.6 "空闲"的定义：队列空 **且** 没有在途工作

```332:337:pyv8c/foundation/tasks.py
            deadline_hint = self.next_deadline()
            if deadline_hint is None and not (
                self.network_pending or self.worker_pending or self.loader_pending
            ):
                return
```

因此 worker 存活期间 `worker_pending > 0`，即使队列里没有任何任务，`run_until_idle()` 也不会认为空闲。

### 1.7 宿主命令是唯一会阻塞调用者的入口

```156:189:pyv8c/foundation/executor.py
    def call(self, callback, *args, timeout=_UNSET, **kwargs):
        """Run *callback* on the owner thread and return or raise its result.

        Raises :class:`RealmBusyError` when the owner thread does not pick the
        command up within *timeout*: a wedged realm surfaces as an error.
        """
        if self.is_owner_thread():
            return callback(*args, **kwargs)
        if timeout is _UNSET:
            timeout = self.default_timeout
        future: Future[object] = Future()
        with self._submit_lock:
            if self.closed:
                raise RuntimeError("realm executor is closed")
            self._commands.put((future, callback, args, kwargs))
        self.wakeup()
        name = getattr(callback, "__name__", callback)
        submitted = time.perf_counter()
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            if future.done():
                return future.result()
            # A queued command cancels safely; a running one was already preempted
            # by the realm's deadline, and what it ends with is logged, not awaited.
            cancelled = future.cancel()
            if not cancelled:
                future.add_done_callback(
                    lambda done: _report_late(done, name, submitted, timeout))
            raise RealmBusyError(
                f"realm executor did not complete {name!r} within {timeout}s"
                + (" (command cancelled)" if cancelled else
                   " (already running; its outcome is logged when it ends)")
            ) from None
```

命令执行期间还会把 60 s 预算挂到原生 V8Context 上，用来抢占卡死的脚本（`pyv8c/foundation/executor.py:206-238`）。

## 2. 各类任务的落点

| 类别 | 入口 | 落点 / 车道 | 执行线程 |
| --- | --- | --- | --- |
| `setTimeout` / `setInterval` | `_SchedulingMixin._schedule` → `task_services.schedule` | `timers` 死线堆（heapq，`perf_counter` 计时） | owner |
| `requestAnimationFrame` | 同一个 mixin，一帧只排一个 timer | `timers` 死线堆，60 Hz 网格 | owner |
| 事件 `dispatchEvent` | `fn_dispatchEvent` 在当前轮次内**同步**派发 | 不产生新任务；宿主 `page.dispatch_event` 走 `executor.call` | owner |
| 重复定时器 | `_run_due_timers` 跑完后重新入堆 | 同一死线堆，间隔下限被钳到 1 ms | owner |
| 网络完成（引擎） | 引擎线程池 → `TaskDelivery` → `post_task` | `posted` | 引擎池 → owner |
| 网络完成（回到 realm） | `_HopReply.enqueue` → `post(receive)` → `landed` → `deliver` → `post(settle)` | `posted`，**连投三跳** | owner |
| `resource_interceptor`（宿主回调） | `CallbackRunner.dispatch` | loader 池线程执行，回来 `post_task`；`loader_pending` 计数 | loader 池 → owner |
| `postMessage` / worker 完成 | 目标 realm 的 `post` | 目标 realm 的 `posted` | 各自 owner |
| `MutationObserver` | `enqueue_microtask` | V8 微任务队列 | owner |
| promise 反应 / `queueMicrotask` | `context.enqueue_microtask` | V8 微任务队列 | owner |

关键引文：

```496:497:pyv8c/platform/resources/fetcher.py
                transport = self.source.request_async(self.fetcher.page.services.task_services,
                                                      request, reply.enqueue)
```

```40:43:pyv8c/network/backend.py
            tasks.post_task(
                lambda: (tasks.complete_network(), callback(result, error))
                if not handle.cancelled else tasks.complete_network()
            )
```

```406:408:pyv8c/platform/resources/fetcher.py
    def deliver(self, response, error):
        from functools import partial
        self.tasks.post(partial(self.settle, response, error))
```

```529:531:pyv8c/platform/resources/fetcher.py
    def enqueue(self, value, error):
        from functools import partial
        self.owner.tasks.post(partial(self.receive, value, error))
```

## 3. 它们之间如何互相影响

1. **同类之间只有到达顺序。** 网络完成、`postMessage`、iframe 完成、frame 收尾回调全部落在同一条 `posted` FIFO 上，没有任何优先级：网络回调不可能插队到事件之前，反之亦然。

2. **跨类之间有不饿死的配额。** 每个轮次必跑一个到期 timer、必跑一个 posted 任务，所以：
   - 大量网络回调不会把 `setTimeout` 饿死；
   - timer 也不会淹没网络；
   - 代价是**吞吐被均分**：两类同时积压时，每类每轮次各走一个。

3. **宿主命令（JS 执行）优先级最高，且是唯一同步阻塞调用者的东西。** `page.evaluate` / `goto` / `close` 走 `executor.call`，`_drain_commands` 保证它们不排在页面任务之后。副作用：一次 `evaluate` 自身就是一个任务边界，命令是插在轮次之间执行的。

4. **真正会咬人的是跨线程锁，而不是排队。** 仓库里有四处注释在防同一族死锁：

   ```235:237:pyv8c/platform/services.py
           # Cross-window access is synchronous and can start inside a V8 callback
           # holding the shared isolate lock: posting to another executor deadlocks.
           return apply()
   ```

   ```18:21:pyv8c/platform/resources/frames.py
               # Removal can happen while the parent realm executes; closing the
               # child synchronously would wait on an executor that script can
               # reach, so the teardown is dispatched after the parent turn.
               page.tasks.post(page.close)
   ```

   另外两处是 `pyv8c/platform/realms.py:182-189`（运行中的脚本里等自己的线程会死锁）与 `pyv8c/foundation/executor.py:101-103`（`Browser` 与 realm 共享 GIL 时不能在构造里阻塞等待线程被调度）。外显形式就是 `RealmBusyError: ... (already running ...)`。

5. **一次网络完成被拆成三个轮次。** 交付之间可以插入页面任务；代价是两个并发请求的 settle 可以互相穿插，**交付顺序不等于完成顺序**。

6. **worker 与页面只通过两条路交互**：目标 realm 的 `posted` 队列，以及 `worker_pending` 计数。worker 活着时"空闲"永不成立。

7. **没有节流或预算。** 自续的 `setTimeout(…, 0)` 会让 loop 一直转（`run_turn` 返回 `True` 就不睡），`run_until_idle` 只会撞超时。

## 4. 与 Chromium 的对照

以下为 Chromium 侧的背景模型，用于说明 pyv8c 的选择与取舍。

### 4.1 Chromium 的做法

- **线程层**：渲染器主线程由 `base::SingleThreadTaskRunner` 驱动（一组 `TaskQueue` + `TaskQueueSelector`）；网络在 Network Service 进程 / IO 线程，合成在 compositor 线程，worker 各自线程。跨线程一律 `PostTask`，主线程不阻塞等待网络或 worker。
- **任务源到优先级**：HTML 规范的多个 task source（DOM manipulation、user interaction、networking、navigation、timer、posted message…）映射为 `TaskType` 优先级阶梯，`kControlPriority` / `kHighestPriority` 高于输入相关队列，默认是 `kNormalPriority`，下面是 `kLowPriority`、`kBestEffortPriority`。规范只要求同一 source 内保序，优先级用来决定不同 source 之间的先后。
- **调度器层**：`RendererSchedulerImpl` / `MainThreadSchedulerImpl` 负责同优先级队列间的轮转（饥饿避免）、页面隐藏时的 timer 节流、低优先级任务的预算，以及输入到来或加载期对相关队列的提级。
- **延迟任务**：timer 经 `TaskRunnerTimer` 投递，延迟任务由时间域与延迟任务堆管理，对齐与节流在调度器层。
- **帧**：`requestAnimationFrame` 由合成器 BeginFrame 驱动，与主线程任务队列分开。
- **微任务**：每个任务返回时执行一次 `MicrotaskQueue::PerformCheckpoint`；`queueMicrotask` 与 promise 反应共用一条 FIFO。
- **嵌套与阻塞**：允许嵌套循环但被严格审计，主线程卡住会触发 hang 检测。

### 4.2 差异对照

| 维度 | pyv8c | Chromium |
| --- | --- | --- |
| 队列粒度 | realm 一个队列，三条车道 | 每线程多队列 × 任务类型，按优先级选择 |
| 任务源 | 两个（parser / posted），timer 是堆不是源 | 十几种 task source 映射到优先级 |
| 调度策略 | 固定配额：每轮 1 posted + 1 timer（+1 parser） | 优先级 + 同优先级轮转 + 饥饿避免 |
| 网络交付 | 跨三个轮次，多跳 | 通常一个任务内交付 |
| 宿主命令 | `executor.call`，插在轮次前，最高优先，阻塞调用者，超时 `RealmBusyError` | 无对应物（宿主即浏览器），主线程不等待 |
| 节流与预算 | 无 | timer 节流、唤醒预算、低优先级预算 |
| `requestAnimationFrame` | 与 timer 同一个死线堆 | 合成器 BeginFrame，独立于任务队列 |
| 微任务 | V8 context 自管，轮次/命令后结算 rejection | 每个任务后 `PerformCheckpoint` |
| 死锁面 | owner 线程 + isolate 锁 + 同步 `call` | 跨进程/线程 `PostTask` + hang 检测 |

## 5. 结论与最小改进方向

- 四类任务**确实互相影响，但影响是排队式的，不是抢占式的**：pyv8c 用"每轮每类一个"的固定配额代替了 Chromium 的优先级与节流，结果是不会饿死、也没有优先级，吞吐被均分。
- **JS 执行在 pyv8c 里是最高优先级且唯一同步阻塞的东西**，这是与 Chromium 差别最大的一处：每一个 `evaluate` / `goto` / 网络等待都是一个可能超时并抛 `RealmBusyError` 的同步等待。
- **最容易出问题的是跨线程锁序**，不是任务排队：owner 线程、isolate 锁、`executor.call` 三者叠加时才会真正卡死；第 3 节第 4 点的那四处注释就是在防它。
- 若要更贴近 Chromium，最小的一步是给 `TaskQueue` 引入真正的 task source 枚举（network / timer / posted-message / user-interaction）并按源轮转，而不是把四类都塞进一条 `posted`。这既不动线程模型，也不碰上述几处锁。
