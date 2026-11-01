"""Durable, observable library inventories, separate from the extraction worker."""
import json
import threading
import time
import uuid

from ...log import get_logger
from .preview_source import InventoryChanged


logger = get_logger("stream.preview_submission")


class Submission:
    def __init__(self, registry, data):
        self.registry = registry
        self.data = data
        self.stop = threading.Event()
        self.thread = None

    def snapshot(self):
        with self.registry.lock:
            return dict(self.data)

    def update(self, **changes):
        with self.registry.lock:
            # A blocked storage call may return after shutdown or replacement.
            # It must not restore a running/done state over the recorded stop.
            if self.stop.is_set() or self.registry.tasks.get(self.data["library_id"]) is not self:
                return False
            self.data.update(changes)
            now = time.monotonic()
            if changes.get("state") or now - self.registry.saved_at >= 1:
                try:
                    self.registry.save()
                except OSError:
                    logger.warning("save preview inventory progress failed")
            return True


class SubmissionRegistry:
    def __init__(self, root):
        self.root = root
        # Progress must not invalidate the sprite inventory's root signature.
        self.path = root.parent / f".{root.name}-submissions.json"
        self.lock = threading.RLock()
        self.tasks = {}
        self.saved_at = 0.0
        self.closed = False
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            saved = []
        if not isinstance(saved, list):
            raise ValueError("invalid preview submission history")
        for data in saved:
            if (not isinstance(data, dict) or type(data.get("library_id")) is not int
                    or data["library_id"] <= 0 or not isinstance(data.get("id"), str)
                    or data.get("state") not in ("running", "done", "error", "cancelled")):
                raise ValueError("invalid preview submission")
            if data["state"] == "running":
                data.update(state="error", error="服务已重启，全库盘点已中断；已保存的处理队列仍保留，可重新提交")
            self.tasks[data["library_id"]] = Submission(self, data)

    def save(self):
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps([task.data for task in self.tasks.values()], ensure_ascii=False),
                            encoding="utf-8")
            temp.replace(self.path)
            self.saved_at = time.monotonic()

    def latest(self, library_id):
        with self.lock:
            task = self.tasks.get(library_id)
            return dict(task.data) if task else None

    def submit(self, library_id, worker):
        with self.lock:
            if self.closed:
                raise OSError("preview inventory registry is stopping")
            previous = self.tasks.get(library_id)
            if previous and previous.data["state"] == "running":
                return previous.snapshot()
            data = dict(id=uuid.uuid4().hex, library_id=library_id, state="running", phase="listing",
                        total=None, checked=0, added=0, promoted=0, ready=0,
                        already_queued=0, unavailable=0, batch_id=None, error="")
            task = Submission(self, data)
            self.tasks[library_id] = task
            try:
                self.save()
            except OSError:
                if previous:
                    self.tasks[library_id] = previous
                else:
                    del self.tasks[library_id]
                raise
            try:
                task.thread = threading.Thread(target=self._run, args=(task, worker),
                                               name=f"preview-inventory-{library_id}", daemon=True)
                task.thread.start()
            except Exception as error:
                task.thread = None
                task.update(state="error", error="无法启动全库盘点，请稍后重新提交")
                raise OSError("could not start preview inventory worker") from error
            return task.snapshot()

    @staticmethod
    def _run(task, worker):
        started = time.monotonic()
        try:
            worker(task)
            if task.snapshot()["state"] == "running":
                task.update(state="error", error="全库盘点未完成，请稍后重新提交")
        except InventoryChanged as error:
            task.update(state="error", error=str(error))
        except Exception:
            logger.exception("preview inventory failed library=%s", task.data["library_id"])
            task.update(state="error", error="全库盘点或保存队列失败，请检查存储连接和服务日志后重试")
        finally:
            logger.info("preview inventory library=%s state=%s checked=%s total=%s elapsed=%.2fs",
                        task.data["library_id"], task.data["state"], task.data["checked"],
                        task.data["total"], time.monotonic() - started)

    def shutdown(self):
        with self.lock:
            self.closed = True
            tasks = list(self.tasks.values())
            for task in tasks:
                task.stop.set()
                if task.data["state"] == "running":
                    task.data.update(state="cancelled", error="服务正在停止，全库盘点已中断，可重新提交")
            try:
                self.save()
            except OSError:
                logger.warning("save stopped preview inventories failed")
        deadline = time.monotonic() + 5
        for task in tasks:
            if task.thread:
                task.thread.join(timeout=max(0, deadline - time.monotonic()))
