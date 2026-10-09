from collections import deque


class TerminalHistoryBuffer:
    """Bounded chunked character buffer with string-like relative slicing."""

    def __init__(self, limit, chunk_size=65536):
        self.limit = max(0, int(limit))
        self.chunk_size = max(1, int(chunk_size))
        self._chunks = deque()
        self._size = 0
        self.start = 0
        self.end = 0

    def __len__(self):
        return self._size

    def __bool__(self):
        return self._size > 0

    def append(self, text):
        value = str(text or "")
        if not value:
            return

        self.end += len(value)
        if self.limit <= 0:
            self._chunks.clear()
            self._size = 0
            self.start = self.end
            return

        if len(value) >= self.limit:
            value = value[-self.limit:]
            self._chunks.clear()
            self._size = 0

        while value:
            if self._chunks and len(self._chunks[-1]) < self.chunk_size:
                available = self.chunk_size - len(self._chunks[-1])
                part = value[:available]
                self._chunks[-1] += part
                self._size += len(part)
                value = value[len(part):]
                continue
            part = value[:self.chunk_size]
            self._chunks.append(part)
            self._size += len(part)
            value = value[len(part):]

        self._trim_left(max(0, self._size - self.limit))
        self.start = self.end - self._size

    def _trim_left(self, count):
        remaining = min(max(0, int(count)), self._size)
        while remaining and self._chunks:
            first = self._chunks[0]
            if remaining >= len(first):
                remaining -= len(first)
                self._size -= len(first)
                self._chunks.popleft()
                continue
            self._chunks[0] = first[remaining:]
            self._size -= remaining
            remaining = 0

    def __getitem__(self, key):
        if isinstance(key, int):
            index = key if key >= 0 else self._size + key
            if index < 0 or index >= self._size:
                raise IndexError("terminal history index out of range")
            return self[index:index + 1]
        if not isinstance(key, slice):
            raise TypeError("terminal history indices must be integers or slices")

        start, stop, step = key.indices(self._size)
        if step != 1:
            return self.to_string()[key]
        if stop <= start:
            return ""

        parts = []
        offset = 0
        for chunk in self._chunks:
            chunk_end = offset + len(chunk)
            if chunk_end <= start:
                offset = chunk_end
                continue
            if offset >= stop:
                break
            left = max(0, start - offset)
            right = min(len(chunk), stop - offset)
            if right > left:
                parts.append(chunk[left:right])
            offset = chunk_end
        return "".join(parts)

    def tail(self, limit):
        count = max(0, min(int(limit), self._size))
        return self[self._size - count:self._size]

    def to_string(self):
        return "".join(self._chunks)
