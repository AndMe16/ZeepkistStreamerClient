import websocket
import json
import time
import threading
import traceback
import signal
import sys

WS_URL = "ws://localhost:8080"
ws_global = None

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
    def sequence_thread():
        run_timeline(ws, TIMELINE)

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

def send_input(ws, steer=0, brake=0, armsUp=0, reset=0):
    msg = json.dumps({
        "steer": steer,
        "brake": brake,
        "armsUp": armsUp,
        "reset": reset
    })
    ws.send(msg)

def run_timeline(ws, timeline):
    """
    timeline = list of (duration_seconds, input_dict)
    """
    for duration, inputs in timeline:
        start = time.time()
        while time.time() - start < duration:
            send_input(ws, **inputs)
            time.sleep(0.05)  # 20 updates/sec

TIMELINE = [
    (5, {}),
    (1, {"armsUp": 1}),         # 1 sec ArmsUp
    (1, {"armsUp": 0}),         # 1 sec Nothing
    (2, {"steer": -1}),         # 2 sec SteerLeft
    (1, {"steer": 1}),          # 1 sec SteerRight
    (2, {}),                    # 2 sec Nothing
    (3, {"brake": 1}),          # 3 sec Brake
    (5, {}),                    # 2 sec Nothing
    (0.2, {"reset": 1}),
    (1, {})
]

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
