"""Single, non-preemptive GPU slot with cancellable priority/FIFO waiters."""
import asyncio
import heapq
import itertools
from contextlib import asynccontextmanager


class PrioritySlot:
    def __init__(self):
        self._busy=False
        self._waiters=[]
        self._sequence=itertools.count()

    def locked(self):
        return self._busy

    def waiting_count(self):
        return sum(not future.done() for _, _, future in self._waiters)

    async def acquire(self,priority=1):
        if not self._busy:
            self._busy=True
            return True
        future=asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters,(priority,next(self._sequence),future))
        try:
            await future
            return True
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():self.release()
            else:future.cancel()
            raise

    def release(self):
        if not self._busy:raise RuntimeError('Priority slot released without owner')
        while self._waiters:
            _,_,future=heapq.heappop(self._waiters)
            if not future.done():
                future.set_result(True)
                return
        self._busy=False

    @asynccontextmanager
    async def slot(self,priority=1):
        await self.acquire(priority)
        try:yield
        finally:self.release()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self,*args):
        self.release()
