import websockets
import asyncio
import os
from sig import generate_websocket_auth_headers
import json

# --- Parameters ---
TARGET_TICKERS = ["KXNBAGAME-26JAN05GSWLAC-GSW", "KXNBAGAME-26JAN05GSWLAC-LAC"]
CHANNELS = ["ticker", "orderbook_delta"]

# WebSocket URL
ws_url = 'wss://api.elections.kalshi.com/trade-api/ws/v2' #production environment

# Get Credentials from environment variables
api_key_id = os.environ.get("KALSHI_API_KEY", "your_api_key_id")
private_key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "~/path/to/your/private_key.pem")

# Generate authentication headers (see API Keys documentation)
auth_headers = generate_websocket_auth_headers(api_key_id, private_key_path)

websocket = (ws_url, auth_headers)

message_id = 1  # Incremental message ID for subscriptions
async def subscribe_to_markets(ws, channels, market_tickers):
    global message_id
    """Subscribe to specific channels and markets"""
    subscription_message = {
        "id": message_id,
        "cmd": "subscribe",
        "params": {
            "channels": channels,
            "market_tickers": market_tickers
        }
    }
    await ws.send(json.dumps(subscription_message))
    message_id += 1
    #print(f"Subscribed to channels: {channels} for markets: {market_tickers}")

orderbooks = {}  # Store orderbook states
async def process_message(message):
    """Process incoming WebSocket messages"""

    msg = json.loads(message)
    t = msg.get("type")
    m = msg.get("msg") or msg.get("data") or {}

    if t == "ticker":
        print("TICKER", m.get("market_ticker"), "yes_bid", m.get("yes_bid"), "yes_ask", m.get("yes_ask"), "vol", m.get("volume"))

    elif t == "orderbook_snapshot":
        mt = m["market_ticker"]
        yes = {p: q for p, q in m.get("yes", [])}
        no  = {p: q for p, q in m.get("no", [])}
        orderbooks[mt] = {"yes": yes, "no": no}
        print("SNAP", mt, "levels_yes", len(yes), "levels_no", len(no))

    elif t == "orderbook_delta":
        mt = m["market_ticker"]
        side = m["side"]           # "yes" or "no"
        price = m["price"]         # integer cents
        delta = m["delta"]         # +/- quantity change

        ob = orderbooks.setdefault(mt, {"yes": {}, "no": {}})
        new_qty = ob[side].get(price, 0) + delta
        if new_qty <= 0:
            ob[side].pop(price, None)
        else:
            ob[side][price] = new_qty

    elif t == "trade":
        print("TRADE", m.get("market_ticker"), "yes", m.get("yes_price"), "no", m.get("no_price"), "count", m.get("count"), "taker", m.get("taker_side"))

    elif t == "fill":
        print("FILL", m.get("market_ticker"), m.get("action"), m.get("side"), "count", m.get("count"), "price", m.get("yes_price"))
    # data = json.loads(message)
    # msg_type = data.get("type")

    # if msg_type == "subscribed":
    #     # Handle ticker update
    #     print(f"Subscribed to {data}")

    # elif msg_type == "ticker":
    #     # Handle ticker update
    #     market = data.get("data", {}).get("market_ticker")
    #     bid = data.get("data", {}).get("bid")
    #     ask = data.get("data", {}).get("ask")
    #     print(f"Ticker update for {market}: Bid={bid}, Ask={ask}")

    # elif msg_type == "orderbook_snapshot":
    #     # Handle full orderbook state
    #     print(f"Orderbook snapshot for {data}")

    # elif msg_type == "orderbook_delta":
    #     # Handle orderbook changes
    #     print(f"Orderbook delta for {data}")
    #     # Note: client_order_id field is optional - present only when you caused this change
    #     if 'client_order_id' in data.get('data', {}):
    #         print(f"  Your order {data['data']['client_order_id']} caused this change")

    elif t == "error":
        error_code = msg.get("msg", {}).get("code")
        error_msg = msg.get("msg", {}).get("msg")
        print(f"Error {error_code}: {error_msg}")

async def main():
    async with websockets.connect(ws_url, additional_headers=auth_headers) as websocket:
        print("Connected to Kalshi WebSocket")
        await subscribe_to_markets(websocket, CHANNELS, TARGET_TICKERS)
        
        # Listen for messages
        async for message in websocket:
            await process_message(message)

# Run the connection
if __name__ == "__main__":
    if not api_key_id or not private_key_path:
        print("Error: Please set KALSHI_API_KEY and KALSHI_PRIVATE_KEY_PATH environment variables.")
    else:
        asyncio.run(main())

