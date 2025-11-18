import websocket
import msgpack
import threading
import time
import traceback
import signal
import sys
import random

WS_URL = "ws://localhost:8080"

# Thread-shared state
latest_state = None           # dict populated by on_message when StreamData is received
waiting_state = False         # set True by sequence thread when it requested a state
waiting_state_lock = threading.Lock()

# Tuning
UPDATE_HZ = 60
UPDATE_INTERVAL = 1.0 / UPDATE_HZ

# ---------------- WebSocket callbacks ----------------

def on_message(ws, message):
    """
    Receive StreamData (binary MessagePack). Save into latest_state and mark waiting_state False.
    """
    global latest_state, waiting_state
    try:
        if isinstance(message, bytes):
            latest_state = msgpack.unpackb(message, raw=False)
            # Expecting StreamData shaped like {"state": {...}, "timestamp": ...}
            # Save it
            # state = latest_state.get("state", {})
            # pos  = state.get("position", [0,0,0])
            # rot  = state.get("rotation", [0,0,0])
            # lv   = state.get("localVelocity", [0,0,0])
            # lav  = state.get("localAngularVelocity", [0,0,0])

            # print("\n==== Received StreamData ====")
            # print(f"Position           {pos}")
            # print(f"Rotation (Euler)   {rot}")
            # print(f"Local Velocity     {lv}")
            # print(f"Local Ang Vel      {lav}")
            # print(f"Timestamp          {latest_state.get('timestamp')}")

            # Mark state as received for sequence thread
            with waiting_state_lock:
                waiting_state = False
        else:
            # text messages (ignore)
            print("[WS TEXT] ", message)
    except Exception:
        print("[WS ERROR] Error processing incoming message:")
        traceback.print_exc()

def on_error(ws, error):
    print("[WS ERROR]", error)

def on_close(ws, close_status_code, close_msg):
    print("[WS CLOSED] code:", close_status_code, "msg:", close_msg)

def on_open(ws):
    print("[WS OPEN] Connected to mod. Starting sequence thread.")
    # start the request/decide loop in a background thread
    t = threading.Thread(target=sequence_thread, args=(ws,), daemon=True)
    t.start()

# ---------------- Sequence thread (request -> receive -> decide) ----------------

def sequence_thread(ws):
    """
    Loop:
      - set waiting_state
      - send STATE_REQUEST
      - wait for on_message to fill latest_state (busy-wait with small sleep)
      - compute action from latest_state
      - store latest_cmd (thread-safe)
      - wait remaining time to maintain dt
    """
    global waiting_state, latest_state

    dt = UPDATE_INTERVAL  
    print("[SEQ] Sequence thread started (request rate {:.1f} Hz)".format(1.0/dt))

    # small warmup
    time.sleep(0.5)

    while True:
        start = time.time()
        # request state
        with waiting_state_lock:
            waiting_state = True
        request_state(ws)

        # wait until on_message clears waiting_state OR timeout
        timeout = 0.25  # 250 ms timeout to avoid locking forever
        waited = 0.0
        poll = 0.001
        while True:
            with waiting_state_lock:
                if not waiting_state:
                    break
            time.sleep(poll)
            waited += poll
            if waited >= timeout:
                # timed out: maybe the connection broke or the mod didn't respond
                print("[SEQ] STATE_REQUEST timed out after {:.1f} ms".format(waited*1000))
                break

        # take snapshot of latest_state
        state_snapshot = None
        if latest_state is not None:
            # shallow copy is fine as we don't mutate it
            state_snapshot = dict(latest_state)

        # decide action based on state
        action = ml_policy(state_snapshot)

        # Send ACTION message back to Unity
        send_action(ws, action)

        # print("[SEQ] Decided action:", action)

        # maintain rate
        elapsed = time.time() - start
        remaining = dt - elapsed
        if remaining > 0:
            time.sleep(remaining)
        else:
            # if we're behind, yield a tiny bit
            time.sleep(0.001)

# ---------------- WebSocket helpers ----------------

def request_state(ws):
    payload = {"cmd": "STATE_REQUEST"}
    packet = msgpack.packb(payload, use_bin_type=True)
    try:
        ws.send(packet, opcode=websocket.ABNF.OPCODE_BINARY)
    except Exception:
        print("[WS] send error in request_state()")
        traceback.print_exc()

def send_action(ws, action_dict):
    """
    Serializes InputCommand → MessagePack and sends it.
    """
    try:
        payload = {
            "cmd": action_dict["cmd"],
            "steer": float(action_dict["steer"]),
            "brake": float(action_dict["brake"]),
            "armsUp": float(action_dict["armsUp"]),
            "reset": float(action_dict["reset"]),
        }

        packet = msgpack.packb(payload, use_bin_type=True)
        ws.send(packet, opcode=websocket.ABNF.OPCODE_BINARY)

    except Exception:
        print("[WS] Error sending ACTION")
        traceback.print_exc()




# ---------------- ML policy (replace with your model) ----------------

def ml_policy(state):
    """
    Replace this with your ML model. The state parameter is the StreamData dict
    with 'state' key containing 'position','rotation','localVelocity','localAngularVelocity'
    """
    return {
        "cmd": "ACTION",
        "steer": random.uniform(-1, 1),
        "brake": random.random(),
        "armsUp": random.random(),
        #"reset": 1 if random.random() < 0.1 else 0,
        'reset': 0.0,
    }

# ---------------- cleanup and main ----------------

ws_global = None

def cleanup(sig=None, frame=None):
    global ws_global
    print("\n[EXIT] Cleaning up...")
    try:
        if ws_global:
            ws_global.close()
    except:
        pass
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

def start():
    global ws_global


    # create websocket client and connect
    ws_global = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    ws_global.run_forever()

if __name__ == "__main__":
    start()
