"""Shared background-task queue for the current CCCLEANER desktop."""
import queue
import threading
from cplus_cleaner.localization.localized_tk import messagebox


def operation_status(page, message):
    """Keep automatic action results in the page instead of opening a raw dump."""
    page.app.status.set(message)
    for name in ("note", "scan_note"):
        variable = getattr(page, name, None)
        if variable is not None:
            variable.set(message)
            break


class TaskRunner:
    def start(self, label, work, done, failed=None):
        if self.busy:
            self.status.set("已有任务正在运行，请等待或取消")
            return
        self.busy = True
        self.cancel.clear()
        self.progress.start(12)
        self.status.set(label + "…")
        def run():
            try:
                self.events.put(("done", (done, work())))
            except Exception as exc:
                self.events.put(("error", (failed, str(exc))))
        threading.Thread(target=run, daemon=True).start()

    def report_progress(self, text):
        import time
        now = time.monotonic()
        if now - self.last_progress > .15:
            self.last_progress = now
            self.events.put(("progress", str(text)))

    def poll(self):
        try:
            # Yield regularly so a large scan cannot starve rendering or cancellation.
            for _ in range(20):
                event, payload = self.events.get_nowait()
                if event == "progress":
                    self.status.set(payload[-130:])
                elif event == "batch":
                    callback, items = payload
                    callback(items)
                else:
                    self.busy = False
                    self.progress.stop()
                    if event == "done":
                        done, result = payload
                        self.status.set("已取消；保留已完成结果" if self.cancel.is_set() else "")
                        try:
                            done(result)
                        except Exception as exc:
                            messagebox.showerror("显示结果失败", str(exc))
                    else:
                        self.status.set("任务未完成")
                        failed, error = payload
                        if failed:
                            failed(error)
                        messagebox.showerror("任务失败", error)
        except queue.Empty:
            pass
        self.after(100, self.poll)
