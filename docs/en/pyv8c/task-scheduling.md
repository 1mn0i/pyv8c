# Task Scheduling

[简体中文](../../pyv8c/task-scheduling.md) | **English**

[Documentation Index](../README.md)

## Execution Ownership

A standalone `new_page()` creates a WindowExecutionGroup with its own dedicated owner thread and Isolate. An iframe uses the same execution group; Realms with the same effective origin share an Agent, while other Agents queue independently but still run on that owner thread. Opaque origins are distinguished by identity token, and the origins of initial about:blank, srcdoc, and Blob inherit according to their creation relationship.

Each Agent holds task source queues and an explicit V8 microtask queue; a Dedicated Worker uses its own thread, Isolate, Agent, and event loop. Synchronous iframe access requires no cross-thread waiting; passing live JS objects directly between unrelated execution groups is not allowed.

## Task Sources and Entry Points

A task record contains its source, owning Realm/document, generation, order, and cancellation information. Task sources include parser, navigation, networking, timer, posted-message, user-interaction, DOM manipulation, media-element, and embedder.

The OfflineAudioContext completion notification uses the media-element source so that completed rendering is not backlogged by unrelated DOM tasks. That task is still subject to the owning document's generation and shutdown state; the complete event precedes the rendering Promise's fulfillment callback.

Ordinary host commands enter the embedder source and are not drained with unbounded priority. Scheduling fairly selects runnable sources and preserves the FIFO required within a source; parser blocking affects only the corresponding document. Shutdown, termination, and Inspector wake-ups use a restricted control entry point, which cannot be used to execute arbitrary page JS.

There is no single Chrome total order across sources that should be pinned down; tests constrain necessary causal ordering and non-starvation. Full loading prioritization, background budgets, and dynamic input priorities are not currently implemented.

## Microtasks and Rejection Notifications

Promise, queueMicrotask, and MutationObserver use the owning Agent's explicit microtask queue. Ordinary property reads, object conversions, and nested cross-Realm calls should not unexpectedly start a checkpoint; script and callback cleanup check when JS stack conditions allow, with a fallback at task end, and reentrancy is prevented.

Checkpoints drain to stability and do not use an artificial microtask quota to change ordering. Infinite microtasks are handled by the operation budget and termination mechanism. Promise rejections collect candidates at the checkpoint and then notify `unhandledrejection` and `rejectionhandled` through a task, rather than dispatching directly at the end of an arbitrary property call.

Navigation generation invalidation cancels only the operations of the corresponding document; it must not clear the entire Agent queue and inadvertently affect sibling Realms.

## Network, Navigation and Waiting

External network completion is delivered to the owning networking task; after returning to the owner, response conversion, redirects, and internal continuations may proceed within that task. Asynchronous final results from the cache, local URLs, and inline loaders are likewise delivered through the queue, and the final user callback is not invoked synchronously just because execution is currently on the owner.

Navigation does not block on the owner waiting for the main-document network. The synchronous host `goto()` waits on the operation Future while the owner continues processing runnable work; inside the owner, code must not synchronously wait on unfinished operations, nor nest a drive of the event loop to complete a wait.

| Wait entry point | Condition |
| --- | --- |
| `goto(..., wait_until="commit")` | The current navigation commits |
| `wait_for_load_state("domcontentloaded")` | The DOMContentLoaded state of the target document |
| `wait_for_load_state("load")` | The loading-completion flow ends normally |
| `wait_for(expression)` | A read-only JS condition holds |
| `run_until_idle(timeout)` | The target Agent's runnable tasks are drained and the checkpoint completes |

Distant timers, in-flight network requests, persistent connections, and live Workers do not directly prevent the runnable queue from going idle. An idle call with `timeout=0` only queries the current state; network or application readiness must use the corresponding wait entry point.

## Timers and Rendering Opportunities

The timer heap manages deadlines; once a timer expires it enters the timer source. Cancelled heap-top entries must be skipped and must not block later-expiring work. Timers have nesting levels and corresponding minimum-delay rules; interval is not a separate model with a fixed 1 ms floor.

rAF belongs to a separate rendering opportunity; the page tree shares a frame clock, virtual 60 Hz by default, configurable with the Browser's `frame_rate`. A single frame uses a unified time base; rAFs added or cancelled within callbacks are handled by the current batch rules, and hidden state affects frame opportunities. Workers have separate rendering scheduling facilities and do not share a thread with the page.

The virtual frame clock does not represent real screen refresh, painting, or the full Chromium rendering pipeline.

## Budgets, Cancellation and Shutdown

Host operations use an absolute deadline computed at submission; queueing, execution, and checkpoints share the budget. `evaluate(timeout=30)` has a finite budget by default; the default budget for ordinary calls on the executor is controlled by `PYV8C_CALL_TIMEOUT`, 60 seconds when unset, and a non-positive configuration means that default deadline is not set.

Native operations bind the watchdog with an identity ticket. Queued tasks can be cancelled; work already started cannot promise rollback of side effects. Isolate termination applies to the entire active call stack and does not guarantee stopping only one Context in a cross-Realm stack. Pure Python blocking cannot be interrupted by the V8 watchdog and should be moved off the owner.

When execution termination unwinds outward, event state and resource registration still need to be reset, but page JS, error events, or microtask checkpoints must not run again. Only a shutdown request natively accepted and matching the current ticket can be classified as an expected cancellation; other terminations still report failure.

The start and completion of load are recorded separately. After a load event is interrupted, subsequent pageshow, pagereveal, or success callbacks are not sent, and load waiters receive a failure; document replacement resets the old state. Shutdown, document generation, and identity are checked after every call into page code.

An Inspector pause does not consume the JS execution budget, and host wait deadlines still apply. Shutdown first wakes the pause/start gate, then completes release on the owner. Window closure, Worker termination, and resource expiry are handled separately and are not conflated with clearing a shared queue.
