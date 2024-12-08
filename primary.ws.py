import asyncio
import websockets
import logging
logging.basicConfig(level=logging.INFO) 

class LatencyAwareWebsocket:
    def __init__(
        self, 
        uri,
        retry_connection: bool = True,
        max_retry_attempts: int = 3,
        good_latency_threshold: int = 200,
        window_sz: int = 10,
        min_bad_count: int = 7,
        ping_interval: int = 1,
        ping_timeout: int = 200
    ):
        self.uri = uri
        self.logger = logging.getLogger(__name__)
        self.retry_connection = retry_connection
        self.max_retry_attempts = max_retry_attempts
        self.good_latency_threshold = good_latency_threshold
        self.window_sz = window_sz
        self.min_bad_count = min_bad_count
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout

        self.retry_attempts = 0
        self.is_connected = False
        self.bandwidth_window = []

    async def connect(self):
        try:
            self.connection = await websockets.connect(self.uri)
            self.logger.info("Connected to WebSocket server")
            self.retry_attempts = 0
            self.is_connected = True
            
            asyncio.create_task(self.ping_loop())
            await self.listen()  

        except asyncio.TimeoutError:
            self.log("Connection Timed Out")
            await self.handle_retry()
        except websockets.exceptions.ConnectionClosed as e:
            self.log(f"Connection closed with code {e.code}: {e.reason}")
            await self.handle_retry()
        except asyncio.CancelledError:
            self.log("Connection cancelled")
            await self.handle_retry()
        except Exception as e:
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
        if self.is_connected:
            return
        if self.retry_connection and self.retry_attempts < self.max_retry_attempts:
            self.log("Attempting retry.")
            self.retry_attempts += 1
            await self.connect()
            return
        self.log("Not retrying.")

    async def close(self):
        if self.is_connected:
            await self.connection.close()
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
            await self.handle_new_latency(True) #This is bad
        except Exception as e:
            self.log(f"Ping failed: {e}")

    async def handle_bad_window(self):
        self.log("Handling bad window")
        # If the window is bad, stop retrying and close.
        self.retry_connection = False
        await self.close()

    async def handle_new_latency(self, res: bool):
        self.log(f"Received latency result {res} ")
        self.bandwidth_window.append(res)
        if len(self.bandwidth_window) > self.window_sz:
            self.slide_window()

        if self.is_window_bad():
            await self.handle_bad_window()

    def is_window_bad(self):
        if len(self.bandwidth_window) == self.window_sz:
            bad_count = sum(1 for measurement in self.bandwidth_window if measurement)
            return bad_count >= self.min_bad_count
        return False

    def slide_window(self):
        # Remove the oldest measurement
        if self.bandwidth_window:
            self.bandwidth_window.pop(0)

    def log(self, message: str):
        self.logger.info(message)

if __name__ == "__main__":
    async def main():

        uri = "wss://echo.websocket.events/"
        ws_client = LatencyAwareWebsocket(uri)
        await ws_client.connect()
        # Test for a bit before closing
        # await asyncio.sleep(5)
        # await ws_client.close()

    asyncio.run(main())
