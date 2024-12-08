import asyncio
import websockets
import logging
logging.basicConfig(level=logging.INFO) 

class HealthCheckWebsocket:
    def __init__(
        self, 
        uri,
        good_latency_threshold: int = 200,
        window_sz: int = 10,
        min_good_count: int = 7,
        ping_interval: int = 1,
        ping_timeout: int = 200,
        connection_timeout: int = 5,
        
        on_good_latency=None
    ):
        self.uri = uri
        self.logger = logging.getLogger(__name__)
        self.good_latency_threshold = good_latency_threshold
        self.window_sz = window_sz
        self.min_good_count = min_good_count
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.connection_timeout = connection_timeout
        self.on_good_latency = on_good_latency

        self.retry_attempts = 0
        self.is_connected = False
        self.bandwidth_window = []
        self.attempt_retry = True
        

    async def connect(self):
        try:
            self.attempt_retry = True
            self.connection = await asyncio.wait_for(websockets.connect(self.uri), timeout=self.connection_timeout)
            self.logger.info("Connected to WebSocket server")
            self.retry_attempts = 0
            self.is_connected = True
            self.bandwidth_window = []
            self.ping_task = asyncio.create_task(self.ping_loop())
            self.listen_task = asyncio.create_task(self.listen())
            await self.listen_task
        except asyncio.TimeoutError:
            self.is_connected = False
            self.log("Connection Timed Out")
            await self.handle_retry()
        except websockets.exceptions.ConnectionClosed as e:
            self.is_connected = False
            self.log(f"Connection closed with code {e.code}: {e.reason}")
            await self.handle_retry()
        except asyncio.CancelledError:
            self.is_connected = False
            self.log("Connection cancelled")
            await self.handle_retry()
        except Exception as e:
            self.is_connected = False
            self.log(f"Caught Exception {e} in [self.connect]")
            await self.handle_retry()

    async def listen(self):
        try:
            async for message in self.connection:
                self.log(f"Received message: {message}")
        except websockets.exceptions.ConnectionClosed as e:
            self.log(f"Connection closed during listen with code {e.code}: {e.reason}")
            await self.handle_retry()
        except asyncio.CancelledError:
            self.log("Listening task cancelled")
            raise

    async def ping_loop(self):
        # Continuously send pings every ping_interval seconds as long as connected
        while self.is_connected:
            await self.ping_pong()
            await asyncio.sleep(self.ping_interval)

    async def handle_retry(self):
        if not self.attempt_retry:
            self.log(f"Not attempting retry")
            return

        await self.cleanup_tasks()
        self.retry_attempts += 1
        self.log(f"Attempting retry {self.retry_attempts}")

        await asyncio.sleep(self.connection_timeout)
        await self.connect()
        return

    async def close(self):
        if self.is_connected:
            try:
                await asyncio.wait_for(self.connection.wait_closed(), timeout=2)
            except asyncio.TimeoutError:
                self.logger.info("Connection close timed out.")
            self.is_connected = False
            self.logger.info("Connection closed")

    async def ping_pong(self):
        if not self.is_connected:
            return
        try:
            start_time = asyncio.get_event_loop().time()
            pong_waiter = await self.connection.ping()
            await asyncio.wait_for(pong_waiter, timeout=self.ping_timeout/1000)
            end_time = asyncio.get_event_loop().time()
            latency = (end_time - start_time) * 1000
            self.log(f"Ping successful, latency: {latency:.2f} ms")
            await self.handle_new_latency(latency >= self.good_latency_threshold)
        except asyncio.TimeoutError:
            await self.handle_new_latency(True) 
        except Exception as e:
            self.log(f"Ping failed: {e}")

    async def handle_good_window(self):
        self.log("Handling good window")
        if self.on_good_latency:
            await self.on_good_latency()

    async def handle_bad_window(self):
        self.log("Bad window detected. Closing connection and starting from scratch.")
        await self.cleanup_tasks()
        await self.close()
        await self.connect()

    async def handle_new_latency(self, res: bool):
        self.log(f"Received latency result {res} ")
        self.bandwidth_window.append(res)
        if len(self.bandwidth_window) > self.window_sz:
            self.slide_window()

        # Once we have a full window, determine if it's good or bad
        if len(self.bandwidth_window) == self.window_sz:
            if self.is_window_good():
                await self.handle_good_window()
            else:
                # We do not have a good window, treat this as "bad window"
                await self.handle_bad_window()

    def is_window_good(self):
        if len(self.bandwidth_window) == self.window_sz:
            good_count = sum(1 for measurement in self.bandwidth_window if not measurement)
            return good_count >= self.min_good_count
        return False

    def slide_window(self):
        # Remove the oldest measurement
        if self.bandwidth_window:
            self.bandwidth_window.pop(0)

    def log(self, message: str):
        self.logger.info(f"HealthCheck: {message}")

    async def cleanup_tasks(self):
        if hasattr(self, 'ping_task') and self.ping_task and not self.ping_task.done():
            self.ping_task.cancel()
            self.log("Ping task cancelled")
            try:
                if self.ping_task and not self.ping_task.done():
                    await self.ping_task
                    self.ping_task = None
            except asyncio.CancelledError:
                self.log("Ping task confirmed cancelled")

        if hasattr(self, 'listen_task') and self.listen_task and not self.listen_task.done():
            self.listen_task.cancel()
            self.log("Listen task cancelled")
            try:
                if self.listen_task and not self.listen_task.done():
                    await self.listen_task
                    self.listen_task = None
            except asyncio.CancelledError:
                self.log("Listen task confirmed cancelled")

    async def terminate(self):
        self.attempt_retry = False
        await self.cleanup_tasks()
