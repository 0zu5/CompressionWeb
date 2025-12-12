import cv2
import numpy as np
import uvicorn
import asyncio
from fastapi import FastAPI, WebSocket, Request, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import threading
import queue
import math

# --- IMPORTS ---
from security import VideoEncryptor
from video_engine import AdaptiveVideoEngine

# --- GLOBAL RESOURCES ---
GLOBAL_CAMERA = None
CAMERA_LOCK = threading.Lock()
gui_queue = queue.Queue()

# Store the latest frame from EACH client: { client_id: frame_image }
client_frames = {} 

# --- SECURITY ---
PRE_SHARED_KEY_HEX = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
GLOBAL_AES_KEY = bytes.fromhex(PRE_SHARED_KEY_HEX)

# --- CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self.lock:
            self.active_connections.append(websocket)
            print(f"[System] New Client Connected. Total: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        async with self.lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
                print(f"[System] Client Disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: bytes):
        """Sends a message to ALL connected clients."""
        # Copy list to avoid modification errors during iteration
        async with self.lock:
            connections = self.active_connections[:]
            
        for connection in connections:
            try:
                await connection.send_bytes(message)
            except:
                pass # Dead connections are handled in the receive loop

manager = ConnectionManager()

# --- LIFESPAN (Startup/Shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Start Camera
    global GLOBAL_CAMERA
    print("\n[System] Opening Conference Camera...")
    try:
        GLOBAL_CAMERA = AdaptiveVideoEngine(camera_index=0)
    except:
        GLOBAL_CAMERA = AdaptiveVideoEngine(camera_index=1)
    
    # 2. Start Broadcaster Task (One task feeds ALL phones)
    encryptor = VideoEncryptor(GLOBAL_AES_KEY)
    broadcast_task = asyncio.create_task(broadcast_camera_loop(encryptor))

    yield 
    
    # 3. Cleanup
    broadcast_task.cancel()
    if GLOBAL_CAMERA: GLOBAL_CAMERA.cleanup()
    print("[System] Server Shutdown.")

app = FastAPI(lifespan=lifespan)

async def broadcast_camera_loop(encryptor):
    """Captures ONE frame -> Encrypts ONCE -> Sends to ALL clients."""
    print("[Broadcaster] Started.")
    while True:
        frame_bytes = None
        if GLOBAL_CAMERA:
            with CAMERA_LOCK:
                frame_bytes = GLOBAL_CAMERA.get_processed_frame()
        
        if frame_bytes:
            encrypted_packet = encryptor.encrypt_frame(frame_bytes)
            await manager.broadcast(encrypted_packet)
        
        await asyncio.sleep(0.033) # 30 FPS cap

@app.get("/")
async def get(request: Request):
    with open("templates/index.html", "r") as f:
        return HTMLResponse(f.read())

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    
    # Assign a temporary ID for this connection (memory address is easiest unique ID)
    client_id = id(websocket)
    
    decryptor = VideoEncryptor(GLOBAL_AES_KEY)
    
    try:
        while True:
            data = await websocket.receive_bytes()
            decrypted_frame = decryptor.decrypt_frame(data)
            
            if decrypted_frame:
                nparr = np.frombuffer(decrypted_frame, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                
                if img is not None:
                    # Store this client's latest frame in the global dictionary
                    client_frames[client_id] = img
                    # Notify GUI to redraw
                    if gui_queue.empty(): gui_queue.put("REDRAW")

    except WebSocketDisconnect:
        await manager.disconnect(websocket)
        # Remove their video from the screen
        if client_id in client_frames:
            del client_frames[client_id]

def create_grid(frames_dict, target_size=(320, 240)):
    """Stitches multiple images into a grid."""
    images = list(frames_dict.values())
    count = len(images)
    
    if count == 0:
        # Return a black "Waiting" screen
        blank = np.zeros((480, 640, 3), np.uint8)
        cv2.putText(blank, "Waiting for callers...", (180, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        return blank

    # Resize all to uniform size
    resized_imgs = [cv2.resize(img, target_size) for img in images]
    
    # Calculate grid dimensions (e.g., 2 items -> 2x1, 3 items -> 2x2)
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    
    # Create empty canvas
    grid_h = rows * target_size[1]
    grid_w = cols * target_size[0]
    canvas = np.zeros((grid_h, grid_w, 3), np.uint8)
    
    for i, img in enumerate(resized_imgs):
        r = i // cols
        c = i % cols
        y = r * target_size[1]
        x = c * target_size[0]
        canvas[y:y+target_size[1], x:x+target_size[0]] = img
        
    return canvas

def run_gui_loop():
    window_name = "Conference View (Laptop)"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    
    print("[GUI] Waiting for connections...")
    while True:
        try:
            # Wait for a trigger (don't need data, just a wake-up signal)
            _ = gui_queue.get(timeout=0.1)
            
            # Build the grid from whatever is in client_frames
            # We use .copy() to avoid thread errors if dictionary changes size during read
            current_frames = client_frames.copy()
            grid_img = create_grid(current_frames)
            
            cv2.imshow(window_name, grid_img)
            
            if cv2.waitKey(1) & 0xFF == ord('q'): break
        except queue.Empty:
            pass
            
    cv2.destroyAllWindows()

if __name__ == "__main__":
    server_thread = threading.Thread(
        target=uvicorn.run, 
        args=(app,), 
        kwargs={
            "host": "0.0.0.0", 
            "port": 8000, 
            "ssl_keyfile": "key.pem", 
            "ssl_certfile": "cert.pem",
            "log_level": "error"
        }, 
        daemon=True
    )
    server_thread.start()
    
    run_gui_loop()