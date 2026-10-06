"""
Background work for the UI.

Scanning, planning and copying run on a thread pool so the window never freezes. Each
``Task`` wraps a plain function that accepts ``progress`` and ``cancelled`` keyword
arguments, and reports back through Qt signals on the UI thread.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL = 0.05  # seconds between progress updates sent to the UI

# Tasks must outlive their Python references until the pool is done with them.
_LIVE_TASKS: set[Task] = set()


class TaskSignals(QObject):
	progress = pyqtSignal(object)
	finished = pyqtSignal(object)
	failed = pyqtSignal(str)
	ended = pyqtSignal()


class Task(QRunnable):
	"""Run ``fn(*args, progress=..., cancelled=..., **kwargs)`` off the UI thread.

	``finished`` is emitted with the return value, unless the task was cancelled, in
	which case nothing is emitted for scans/plans; pass ``report_cancelled=True`` to
	still receive the (partial) result, e.g. for a copy run that stopped half-way.
	"""

	def __init__(self, fn: Callable, *args, report_cancelled: bool = False, **kwargs) -> None:
		super().__init__()
		self.setAutoDelete(False)
		self.signals = TaskSignals()
		self._fn = fn
		self._args = args
		self._kwargs = kwargs
		self._cancel = threading.Event()
		self._report_cancelled = report_cancelled
		self._last_progress = 0.0
		self.done = False
		self.signals.ended.connect(lambda: _LIVE_TASKS.discard(self))

	def cancel(self) -> None:
		self._cancel.set()

	@property
	def is_cancelled(self) -> bool:
		return self._cancel.is_set()

	def _progress(self, *values) -> None:
		now = time.monotonic()
		if now - self._last_progress < PROGRESS_INTERVAL:
			return
		self._last_progress = now
		self.signals.progress.emit(values if len(values) != 1 else values[0])

	def run(self) -> None:
		try:
			result = self._fn(
				*self._args, progress=self._progress, cancelled=self._cancel.is_set, **self._kwargs
			)
		except Exception as error:  # surfaced in the UI, details go to the log
			logger.exception("Background task failed")
			self.done = True
			self.signals.failed.emit(str(error) or type(error).__name__)
		else:
			self.done = True
			if not self._cancel.is_set() or self._report_cancelled:
				self.signals.finished.emit(result)
		finally:
			self.signals.ended.emit()

	def start(self, pool: QThreadPool | None = None) -> Task:
		_LIVE_TASKS.add(self)
		(pool or QThreadPool.globalInstance()).start(self)
		return self


class TaskSlot:
	"""Holds at most one running task of a kind, cancelling the old one on replace.

	Handlers are only called for the current task, so results from a scan that was
	superseded a moment ago can never overwrite newer state.
	"""

	def __init__(self) -> None:
		self.task: Task | None = None

	def replace(
		self,
		task: Task,
		*,
		finished: Callable[[object], None] | None = None,
		progress: Callable[[object], None] | None = None,
		failed: Callable[[str], None] | None = None,
	) -> Task:
		self.cancel()
		self.task = task
		for signal, handler in (
			(task.signals.finished, finished),
			(task.signals.progress, progress),
			(task.signals.failed, failed),
		):
			if handler is not None:
				signal.connect(self._guard(task, handler))
		return task.start()

	def _guard(self, task: Task, handler: Callable) -> Callable:
		def call(value) -> None:
			if self.task is task:
				handler(value)

		return call

	def cancel(self) -> None:
		if self.task is not None and not self.task.done:
			self.task.cancel()
		self.task = None

	@property
	def running(self) -> bool:
		return self.task is not None and not self.task.done
