import json
import websockets
from websockets.server import serve


class ESP32Server:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.connected_clients = set()
        self.server = None

    async def broadcast_command(self, command):
        if not self.connected_clients:
            return
        message = json.dumps(command)
        disconnected = set()
        for client in self.connected_clients:
            try:
                await client.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(client)
        self.connected_clients.difference_update(disconnected)

    async def handle_client(self, websocket):
        print(f"ESP32 connected: {websocket.remote_address}")
        self.connected_clients.add(websocket)

        try:
            async for message in websocket:
                print(f"Received from ESP32: {message}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            print(f"ESP32 disconnected: {websocket.remote_address}")
            self.connected_clients.discard(websocket)

    async def start(self):
        self.server = await serve(self.handle_client, self.host, self.port)
        print(f"Websocket server running on ws://{self.host}:{self.port}/ws")

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
