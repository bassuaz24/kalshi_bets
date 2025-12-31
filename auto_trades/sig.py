import os
import time
import base64

try:
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding
except ImportError:
    print("Error: 'cryptography' library not found.")
    print("Please install it using: pip install cryptography")
    exit()

def generate_websocket_auth_headers(api_key_id: str, private_key_path: str) -> dict:
    """
    Generates the PSS signature headers for authenticating a Kalshi WebSocket connection.

    The message to sign is constructed as: timestamp + "GET" + "/trade-api/ws/v2"

    Args:
        api_key_id: Your Kalshi API Key ID.
        private_key_path: The file path to your PEM-encoded private key.

    Returns:
        A dictionary containing the required authentication headers.
    """
    if not api_key_id or not private_key_path:
        raise ValueError("API key ID and private key path must be provided.")

    if not os.path.exists(os.path.expanduser(private_key_path)):
        raise FileNotFoundError(f"Private key file not found at: {private_key_path}")

    # 1. Load the private key.
    with open(os.path.expanduser(private_key_path), "rb") as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(),
            password=None
        )

    # 2. Generate the current timestamp in milliseconds.
    timestamp = str(int(time.time() * 1000))

    # 3. Construct the specific message for WebSocket authentication.
    method = "GET"
    path = "/trade-api/ws/v2"
    message_to_sign = (timestamp + method + path).encode('utf-8')

    # 4. Sign the message using PSS with SHA256.
    signature = private_key.sign(
        message_to_sign,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH
        ),
        hashes.SHA256()
    )

    # Base64 encode the signature.
    encoded_signature = base64.b64encode(signature).decode('utf-8')

    # 5. Assemble and return the headers.
    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-SIGNATURE": encoded_signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp
    }

if __name__ == '__main__':
    # --- Example Usage ---
    # It's best practice to load these from environment variables or a secure config system.
    my_api_key_id = os.environ.get("KALSHI_API_KEY", "your_api_key_id")
    my_private_key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "~/path/to/your/private_key.pem")

    print("Generating WebSocket authentication headers...")

    if my_api_key_id == "your_api_key_id":
        print("\nWARNING: Using placeholder values.")
        print("Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH environment variables to use your real credentials.")
    
    try:
        auth_headers = generate_websocket_auth_headers(my_api_key_id, my_private_key_path)
        print("\nGenerated Headers:")
        for key, value in auth_headers.items():
            # Don't print the full signature for security.
            if key == 'KALSHI-ACCESS-SIGNATURE':
                print(f"  {key}: {value[:10]}...")
            else:
                print(f"  {key}: {value}")

    except (ValueError, FileNotFoundError) as e:
        print(f"\nError: {e}")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
