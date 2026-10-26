"""多开身份：主实例锁独占（拿不到只是副实例，不是错误）。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import uuid

from sketchpad.services.single_instance import InstanceLock


def test_instance_lock_is_exclusive_and_releasable():
    name = f"PageFlowLockTest-{os.getpid()}-{uuid.uuid4().hex}"
    first = InstanceLock(name)
    assert first.acquire() is True
    try:
        second = InstanceLock(name)
        assert second.acquire() is False     # 第二个进程：副实例
        second.release()
    finally:
        first.release()
    third = InstanceLock(name)
    assert third.acquire() is True           # 第一个退出后，新启动的重新成为主实例
    third.release()
