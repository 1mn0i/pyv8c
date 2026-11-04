"""Host callback execution; Fetcher queues the raw result on its owner."""
import threading


class CallbackRunner:
    def __init__(self, page):
        self.browser = page.profile.browser
        self.tasks = page.services.task_services

    def dispatch(self, call, tail, *, force_pool=False):
        """Run the host callback and raw-result tail inline or on the pool.

        A cancelled queued Future never enters the worker, so its done callback
        also releases the loader count. A running callback cannot be interrupted.
        """
        browser, tasks = self.browser, self.tasks
        def gather():
            try:
                return call(), None
            except BaseException as exc:
                return None, exc
        if not browser.threaded_loaders and not force_pool:
            value, error = gather()
            tail(value, error)
            return None
        def pool_side():
            try:
                value, error = gather()
                tail(value, error)
            finally:
                tasks.complete_loader()
        tasks.begin_loader()
        try:
            future = browser.loader_pool.submit(pool_side)
        except BaseException as exc:
            try:
                tail(None, exc)
            finally:
                tasks.complete_loader()
            return None
        future.add_done_callback(lambda done: tasks.complete_loader() if done.cancelled() else None)
        return future

    def call_sync(self, call, timeout=None):
        """Run *call* and wait on this thread for its result.

        The wait is a plain event rather than the realm's queue: this path is reached
        from inside a turn (a parser-blocking script, an iframe, a worker source),
        where nothing else can run the queue for it.
        """
        if not self.browser.threaded_loaders:
            return call()
        browser = self.browser
        answer = {}
        arrived = threading.Event()

        def run():
            try:
                value, error = call(), None
            except BaseException as exc:
                value, error = None, exc
            answer["value"], answer["error"] = value, error
            arrived.set()

        try:
            future = browser.loader_pool.submit(run)
        except BaseException as exc:
            answer["error"] = exc
            arrived.set()
        if not arrived.wait(timeout):
            future.cancel()
            raise TimeoutError(f"the page's loader did not answer within {timeout} seconds")
        if answer.get("error") is not None:
            raise answer["error"]
        return answer["value"]


