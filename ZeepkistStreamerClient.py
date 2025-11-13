import websocket
import json
import time
import traceback

WS_URL = "ws://localhost:8080"

def on_message(ws, message):
    try:
        data = json.loads(message)

        pos  = data.get("position", {})
        rot  = data.get("rotation", {})
        lv   = data.get("localVelocity", {})
        lav  = data.get("localAngularVelocity", {})

        print("\n==== Received Data ====")
        print(f"Position:            {pos}")
        print(f"Rotation:            {rot}")
        print(f"Loc. Vel.:          {lv}")
        print(f"Loc. Ang. Vel.:     {lav}")

    except Exception as e:
        print("Error processing the message:")
        traceback.print_exc()

def on_error(ws, error):
    print(f"[ERROR] {error}")

def on_close(ws, close_status_code, close_msg):
    print(f"[CLOSED] Code: {close_status_code} Message: {close_msg}")

def on_open(ws):
    print("[OK] Connected to the WebSocket Server!")

def connect_with_retries():
    """Try to reconnect"""
    while True:
        try:
            print(f"Trying to reconnect to {WS_URL}...")
            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )

            ws.run_forever()
        except Exception as e:
            print("Error in the connection:")
            traceback.print_exc()

        print("Retrying in 3 seconds...\n")
        time.sleep(3)

if __name__ == "__main__":
    connect_with_retries()
