import cv2
import numpy as np
import uvicorn
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
import threading
import queue
import socket
import qrcode # pip install "qrcode[pil]"

# --- CONFIG ---
# The server GUI Queue to show status/QR
gui_queue = queue.Queue()
connected_count = 0

# --- HELPER FUNCTIONS ---
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def generate_qr_overlay(ip, port):
    url = f"https://{ip}:{port}"
    print(f"[System] Server URL: {url}")
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img_np = np.array(img.convert('RGB'))
    return cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

# --- CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.pairings = {}
        self.waiting_user = None

    async def connect(self, websocket: WebSocket):
        global connected_count
        await websocket.accept()
        connected_count += 1
        gui_queue.put("UPDATE")
        
        if self.waiting_user is None:
            self.waiting_user = websocket
            await websocket.send_text("STATUS:Waiting for partner...")
        else:
            partner = self.waiting_user
            self.pairings[websocket] = partner
            self.pairings[partner] = websocket
            self.waiting_user = None
            await websocket.send_text("STATUS:Connected!")
            await partner.send_text("STATUS:Connected!")

    def get_partner(self, websocket: WebSocket):
        return self.pairings.get(websocket)

    def disconnect(self, websocket: WebSocket):
        global connected_count
        partner = self.pairings.get(websocket)
        if partner:
            del self.pairings[partner]
            asyncio.create_task(partner.send_text("STATUS:Partner Disconnected"))
        if websocket in self.pairings:
            del self.pairings[websocket]
        if self.waiting_user == websocket:
            self.waiting_user = None
        
        connected_count -= 1
        gui_queue.put("UPDATE")

manager = ConnectionManager()
app = FastAPI()

@app.get("/")
async def get(request: Request):
    with open("templates/index.html", "r") as f:
        return HTMLResponse(f.read())

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # 1. Receive Encrypted Data
            data = await websocket.receive_bytes()
            
            # 2. Relay to Partner
            partner = manager.get_partner(websocket)
            if partner:
                await partner.send_bytes(data)
                
                # 3. Adaptive Logic (Server Brain)
                # If packet > 40KB, tell sender to compress more
                if len(data) > 40000: 
                    await websocket.send_text("ADAPT:LOW")
                elif len(data) < 10000:
                    await websocket.send_text("ADAPT:HIGH")

    except WebSocketDisconnect:
        manager.disconnect(websocket)

def run_gui_loop():
    window_name = "Secure Relay Server"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 640, 480)
    
    # Generate QR Code
    local_ip = get_local_ip()
    try:
        qr_img = generate_qr_overlay(local_ip, 8000)
        qr_h, qr_w, _ = qr_img.shape
    except:
        qr_img = None

    print(f"[GUI] Scan QR to join: https://{local_ip}:8000")

    while True:
        try:
            while not gui_queue.empty(): _ = gui_queue.get_nowait()
        except queue.Empty: pass

        # DRAW GUI
        if connected_count >= 2:
            # Show "Traffic Flow" Mode
            display_img = np.zeros((480, 640, 3), np.uint8)
            cv2.putText(display_img, "SECURE RELAY ACTIVE", (140, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            cv2.putText(display_img, f"Users Connected: {connected_count}", (200, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
            cv2.putText(display_img, "(Server is Blind to Video)", (180, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 1)
        else:
            # Show QR Code
            display_img = np.full((480, 640, 3), 255, dtype=np.uint8)
            cv2.putText(display_img, "Scan to Join E2EE Call:", (150, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,0), 2)
            if connected_count == 1:
                 cv2.putText(display_img, "1 User Waiting...", (200, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,255), 2)
            
            if qr_img is not None:
                y_o, x_o = (480 - qr_h) // 2, (640 - qr_w) // 2
                if y_o >= 0 and x_o >= 0:
                     display_img[y_o:y_o+qr_h, x_o:x_o+qr_w] = qr_img

        cv2.imshow(window_name, display_img)
        if cv2.waitKey(100) & 0xFF == ord('q'): break

    cv2.destroyAllWindows()

if __name__ == "__main__":
    # Start Server in Background
    server_thread = threading.Thread(
        target=uvicorn.run, 
        args=(app,), 
        kwargs={
            "host": "0.0.0.0", "port": 8000, 
            "ssl_keyfile": "key.pem", "ssl_certfile": "cert.pem",
            "log_level": "error"
        }, 
        daemon=True
    )
    server_thread.start()
    
    # Run GUI
    run_gui_loop()