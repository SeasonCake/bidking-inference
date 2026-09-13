import threading
import unittest
from research.lifecycle.ownership import Coordinator, Lease
from research.lifecycle.worker import WorkerOwner


class LifecycleTests(unittest.TestCase):
    def test_thread_owner_keeps_partial_handle_until_join_proves_exit(self):
        release = threading.Event()
        entered = threading.Event()

        def work(stop):
            entered.set()
            release.wait(2)

        owner = WorkerOwner(work)
        try:
            self.assertTrue(entered.wait(1))
            handle = owner.thread
            owner.request_stop()
            receipt = owner.reap(0)
            self.assertTrue(receipt["stop_requested"])
            self.assertFalse(receipt["reaped"])
            self.assertIs(owner.thread, handle)
            release.set()
            self.assertTrue(owner.reap(1)["reaped"])
        finally:
            release.set()
            owner.reap(2)

    def test_worker_failure_can_be_reaped_without_being_success(self):
        def work(stop):
            raise ValueError("synthetic failure")

        owner = WorkerOwner(work)
        result = owner.reap(2)
        self.assertTrue(result["reaped"])
        self.assertEqual(result["error_type"], "ValueError")

    def test_stop_is_not_completion_until_real_owner_returns(self):
        c = Coordinator()
        lease = c.acquire("owner")
        c.shutdown()
        self.assertEqual(c.state, "draining")
        self.assertFalse(c.wait_closed(0))
        self.assertFalse(c.complete(lease, "other"))
        self.assertFalse(c.complete(Lease(lease.token, lease.owner), "owner"))
        self.assertEqual(c.state, "draining")
        self.assertTrue(c.complete(lease, "owner"))
        self.assertTrue(c.wait_closed(0))
        self.assertFalse(c.complete(lease, "owner"))

    def test_shutdown_rejects_late_submission_and_closes_once(self):
        calls = []
        c = Coordinator(lambda: calls.append("closed"))
        c.shutdown()
        c.shutdown()
        with self.assertRaises(RuntimeError):
            c.acquire("late")
        self.assertEqual(calls, ["closed"])

    def test_callback_is_reentrant_and_outside_lock(self):
        calls = []

        def closed():
            calls.append(c.wait_closed(0))
            c.shutdown()
            with self.assertRaises(RuntimeError):
                c.acquire("callback")

        c = Coordinator(closed)
        lease = c.acquire("owner")
        c.shutdown()
        self.assertTrue(c.complete(lease, "owner"))
        self.assertEqual(calls, [True])

    def test_worker_barrier_preserves_handle_after_partial_wait(self):
        release = threading.Event()
        started = threading.Event()
        c = Coordinator()
        lease = c.acquire("worker")

        def worker():
            started.set()
            release.wait(2)
            c.complete(lease, "worker")

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            c.shutdown()
            self.assertFalse(c.wait_closed(0))
            self.assertTrue(thread.is_alive())
            release.set()
            self.assertTrue(c.wait_closed(1))
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_close_state_survives_callback_failure(self):
        def callback():
            raise ValueError("synthetic callback failure")

        c = Coordinator(callback)
        with self.assertRaises(ValueError):
            c.shutdown()
        self.assertTrue(c.wait_closed(0))
        c.shutdown()
