import time 
from threading import Lock 
 
class RateLimiter: 
    def __init__(self, per_minute: int): 
        self.per_minute = max(1, int(per_minute)) 
        self.min_interval = 60.0 / self.per_minute 
        self._last_time = 0.0 
        self._lock = Lock() 
 
    def wait(self): 
        with self._lock: 
            now = time.monotonic() 
            elapsed = now - self._last_time 
            if elapsed < self.min_interval: 
                time.sleep(self.min_interval - elapsed) 
            self._last_time = time.monotonic()
