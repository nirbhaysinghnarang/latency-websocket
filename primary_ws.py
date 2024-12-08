import asyncio
import websockets
import logging


from healthcheck_ws import HealthCheckWebsocket


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
        ping_timeout: int = 200,
        connection_timeout: int = 5,
        
        maintain_healthcheck=True
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
        self.connection_timeout = connection_timeout
        self.maintain_healthcheck = maintain_healthcheck

        self.retry_attempts = 0
        self.is_connected = False
        self.bandwidth_window = []
        
        self.healthcheck_socket = HealthCheckWebsocket(
            uri=self.uri,
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_timeout,
            on_good_latency=self.on_detect_good_latency
        )
        
    async def connect(self):
        try:
            self.connection = await asyncio.wait_for(websockets.connect(self.uri), timeout=self.connection_timeout)
            self.logger.info("Connected to WebSocket server")
            self.retry_attempts = 0
            self.is_connected = True
            
            self.ping_task = asyncio.create_task(self.ping_loop())
            self.listen_task = asyncio.create_task(self.listen())
            await self.listen_task

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
        if not self.is_connected:   return
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
            await self.cleanup_tasks()
            self.retry_attempts += 1
            await self.connect()
            return
        self.log("Not retrying.")
        
        
    async def cleanup_tasks(self):
        
        if hasattr(self, 'ping_task') and self.ping_task and not self.ping_task.done():
            self.ping_task.cancel()
            self.logger.info("Ping task cancelled")
            try:
                await self.ping_task
            except asyncio.CancelledError:
                self.logger.info("Ping task confirmed cancelled")

        if hasattr(self, 'listen_task') and self.listen_task and not self.listen_task.done():
            self.listen_task.cancel()
            self.logger.info("Listen task cancelled")
            try:
                await self.listen_task
            except asyncio.CancelledError:
                self.logger.info("Listen task confirmed cancelled")

    async def close(self):
        if self.is_connected:
            try:
                await asyncio.wait_for(self.connection.wait_closed(), timeout=2)
            except asyncio.TimeoutError:
                self.logger.info("Connection close timed out.")

            self.is_connected = False
            self.logger.info("Connection closed")
            self.connection = None

        await self.cleanup_tasks()
        if getattr(self, 'maintain_healthcheck', False) and getattr(self, 'healthcheck_socket', None):
            await self.healthcheck_socket.connect()

            

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
        self.retry_connection = False
        if self.is_connected:
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

    def refresh_state(self):
        self.retry_attempts = 0
        self.is_connected = False
        self.retry_connection = True
        self.bandwidth_window = []
        
        
    async def on_detect_good_latency(self):
        self.refresh_state()
        if self.healthcheck_socket:
            await self.healthcheck_socket.terminate()
            
        await self.connect()
        


if __name__ == "__main__":
    async def main():

        uri = "wss://echo.websocket.events/"
        ws_client = LatencyAwareWebsocket(uri)
        await ws_client.connect()
        # Test for a bit before closing
        # await asyncio.sleep(5)
        # await ws_client.close()

    asyncio.run(main())
