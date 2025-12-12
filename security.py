import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

class VideoEncryptor:
    def __init__(self, key):
        """
        Initialize with a symmetric key (must be 32 bytes for AES-256).
        Both the sender and receiver must use this EXACT same key.
        """
        if len(key) != 32:
            raise ValueError("Key must be 32 bytes long for AES-256")
        self.key = key

    def encrypt_frame(self, frame_bytes):
        """
        Takes raw image bytes -> Returns (nonce + encrypted_bytes)
        """
        # 1. Generate a random Nonce (Number used ONCE) for this specific frame
        # GCM mode requires a unique nonce for every packet to be secure.
        nonce = os.urandom(12) 

        # 2. Create the Cipher
        encryptor = Cipher(
            algorithms.AES(self.key),
            modes.GCM(nonce),
            backend=default_backend()
        ).encryptor()

        # 3. Encrypt the data
        ciphertext = encryptor.update(frame_bytes) + encryptor.finalize()

        # 4. Pack the Nonce + Auth Tag + Ciphertext together
        # We need the nonce and tag to decrypt it on the other side.
        return nonce + encryptor.tag + ciphertext

    def decrypt_frame(self, encrypted_packet):
        """
        Takes encrypted packet -> Returns raw image bytes
        """
        try:
            # 1. Unpack the components (Hardware logic knows the sizes)
            # GCM standard: Nonce is 12 bytes, Tag is 16 bytes
            nonce = encrypted_packet[:12]
            tag = encrypted_packet[12:28]
            ciphertext = encrypted_packet[28:]

            # 2. Create the Decipher
            decryptor = Cipher(
                algorithms.AES(self.key),
                modes.GCM(nonce, tag),
                backend=default_backend()
            ).decryptor()

            # 3. Decrypt
            return decryptor.update(ciphertext) + decryptor.finalize()
            
        except Exception as e:
            print(f"Security Alert: Decryption failed! Packet tampering? {e}")
            return None

class E2EEHandler:
    """Handles secure key negotiation using ECDH."""
    def __init__(self):
        # 1. Generate Private Key (long-term secret)
        self.private_key = ec.generate_private_key(
            ec.SECP384R1(), # Strong curve
            default_backend()
        )
        # 2. Get Public Key (to be sent over the wire)
        self.public_key_bytes = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.shared_key = None # AES key derived here

    def get_public_key(self):
        """Returns the public key to share with the other party."""
        return self.public_key_bytes
    
    def derive_shared_secret(self, peer_public_key_bytes):
        """
        Receives the peer's public key and computes the shared secret.
        """
        # 1. Load the peer's public key from bytes
        peer_public_key = serialization.load_pem_public_key(
            peer_public_key_bytes,
            backend=default_backend()
        )

        # 2. Perform ECDH Exchange to get the initial secret
        shared_secret = self.private_key.exchange(ec.ECDH(), peer_public_key)
        
        # 3. Derive a strong, 32-byte AES key using HKDF
        self.shared_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32, # We need 32 bytes for AES-256
            salt=None, # Use a fixed salt or None for simplicity
            info=b'video call key exchange',
            backend=default_backend()
        ).derive(shared_secret)

        print(f"E2EE Handshake Success! Shared AES Key Derived.")
        return self.shared_key


# Re-run the security test using the E2EE handler
if __name__ == "__main__":
    handler1 = E2EEHandler()
    handler2 = E2EEHandler()

    # 1. Exchange public keys (this can be intercepted but is useless)
    pk1 = handler1.get_public_key()
    pk2 = handler2.get_public_key()

    # 2. Derive the AES session key independently
    key_for_1 = handler1.derive_shared_secret(pk2)
    key_for_2 = handler2.derive_shared_secret(pk1)

    # 3. Verify the keys match
    if key_for_1 == key_for_2:
        print("\nSUCCESS: Keys match. Encrypting with session key...")
        secure_channel = VideoEncryptor(key_for_1)
        data = b"Hello, encrypted video frame!"
        encrypted = secure_channel.encrypt_frame(data)
        decrypted = secure_channel.decrypt_frame(encrypted)
        print(f"Decrypted: {decrypted}")
    else:
        print("ERROR: Keys do not match!")