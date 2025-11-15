import websocket
import json
import time
import threading
import traceback
import signal
import sys
import random

WS_URL = "ws://localhost:8080"
ws_global = None

latest_state = None
waiting_state = False

def on_message(ws, message):
    try:
        global latest_state, waiting_state
        latest_state = json.loads(message)
        waiting_state = False   # mark the state as received

        # pos  = data.get("position", {})
        # rot  = data.get("rotation", {})
        # lv   = data.get("localVelocity", {})
        # lav  = data.get("localAngularVelocity", {})

        # print("\n==== Received Data ====")
        # print(f"Position:            {pos}")
        # print(f"Rotation:            {rot}")
        # print(f"Loc. Vel.:          {lv}")
        # print(f"Loc. Ang. Vel.:     {lav}")

    except Exception as e:
        print("Error processing the message:")
        traceback.print_exc()

def on_error(ws, error):
    print(f"[ERROR] {error}")

def on_close(ws, close_status_code, close_msg):
    print(f"[CLOSED] Code: {close_status_code} Message: {close_msg}")

def on_open(ws):
    print("[OK] Connected to the WebSocket Server!")
    def sequence_thread():
        time.sleep(1)   # let connection stabilize
        
        dt = 1  # 20 updates/sec (Currently only working for low frequency)

        global waiting_state

        while True:
            # Request the latest state
            waiting_state = True
            request_state(ws)

            # Wait for the state to be received
            start = time.time()
            while waiting_state:
                time.sleep(0.001)
            state = latest_state

            # Decide on action based on the latest state
            action = ml_policy(state)

            # Send the action
            send_input(ws, action)

            elapsed = time.time() - start
            remaining = dt - elapsed
            if remaining > 0:
                time.sleep(remaining)

    threading.Thread(target=sequence_thread, daemon=True).start()

def connect_with_retries():
    """Try to reconnect"""
    global ws_global
    while True:
        try:
            print(f"Trying to reconnect to {WS_URL}...")
            ws_global = websocket.WebSocketApp(
                WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )

            ws_global.run_forever()

        except Exception as e:
            print("Error in the connection:")
            traceback.print_exc()

        print("Retrying in 3 seconds...\n")
        time.sleep(3)

def request_state(ws):
    ws.send(json.dumps({ "cmd": "STATE_REQUEST" }))

def send_input(ws, action):
    ws.send(json.dumps(action))

def ml_policy(state):
    return {
        "cmd": "ACTION",
        "steer": random.uniform(-1, 1),   # random steering
        "brake": random.random(),         # 0–1 brake
        "armsUp": random.choice([0, 1]),  # random toggle
        # "reset": random.choice([0, 1]),   # occasional reset
    }



def cleanup(sig=None, frame=None):
    global ws_global
    print("\n[EXIT] Closing WebSocket cleanly...")
    try:
        if ws_global:
            ws_global.close()
    except:
        pass
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

if __name__ == "__main__":
    connect_with_retries()
