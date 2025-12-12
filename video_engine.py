import cv2
import numpy as np

class AdaptiveVideoEngine:
    # MODIFIED: Accepts a camera_index (0 for default, 1, 2, etc.)
    def __init__(self, camera_index=0): 
        self.cap = cv2.VideoCapture(camera_index)
        
        # Initialize defaults FIRST so the object is safe to use
        self.current_quality = 85
        self.current_resolution = (640, 480) 

        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open camera index {camera_index}")

        # Try to read one frame to ensure connection
        ret, frame = self.cap.read()
        if not ret:
            # Don't return None; Raise error so client.py knows to stop
            raise RuntimeError("Camera opened but failed to read frame.")

    def adjust_quality(self, amount):
        """Dynamically adjusts compression based on network feedback."""
        self.current_quality += amount
        
        if self.current_quality > 95: self.current_quality = 95
        if self.current_quality < 10: self.current_quality = 10
        
        # ADAPTIVE RESOLUTION LOGIC
        if self.current_quality < 30:
            self.current_resolution = (320, 240)
        elif self.current_quality < 60:
            self.current_resolution = (480, 360)
        else:
            self.current_resolution = (640, 480)

    def set_manual_mode(self, quality_level):
        """Allows manual overrides via 'b' and 'g' keys"""
        self.current_quality = quality_level
        self.adjust_quality(0) # Trigger resolution check

    def get_processed_frame(self):
        """Captures, Resizes, and Compresses the frame."""
        ret, frame = self.cap.read()
        if not ret: return None

        # STEP 1: ADAPTIVE RESOLUTION
        frame = cv2.resize(frame, self.current_resolution)

        # STEP 2: GRANULAR COMPRESSION
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), self.current_quality]
        result, encoded_img = cv2.imencode('.jpg', frame, encode_param)
        
        if result:
            return encoded_img.tobytes()
        else:
            return None

    def show_local_preview(self, frame_bytes, title="Local Preview"):
        """Helper to see what you are sending"""
        nparr = np.frombuffer(frame_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        color = (0, 255, 0) 
        if self.current_quality < 50: color = (0, 165, 255) 
        if self.current_quality < 30: color = (0, 0, 255)   

        cv2.putText(img, f"Quality: {self.current_quality}%", (10, 30), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        cv2.putText(img, f"Res: {self.current_resolution}", (10, 60), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        
        cv2.imshow(title, img)

    def cleanup(self):
        self.cap.release()
        cv2.destroyAllWindows()