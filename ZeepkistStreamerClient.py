import websocket
import msgpack
import threading
import time
import traceback
import signal
import sys
import random
import json

# vgamepad
import vgamepad as vg

WS_URL = "ws://localhost:8080"

# Thread-shared state
latest_state = None           # dict populated by on_message when StreamData is received
waiting_state = False         # set True by sequence thread when it requested a state
waiting_state_lock = threading.Lock()

latest_cmd = None             # dict with latest ACTION decided locally
latest_cmd_lock = threading.Lock()

# Virtual gamepad
vgpad = None

# Tuning
UPDATE_HZ = 20
UPDATE_INTERVAL = 1.0 / UPDATE_HZ

# ---------------- WebSocket callbacks ----------------

def on_message(ws, message):
    """
    Receive StreamData (binary MessagePack). Save into latest_state and mark waiting_state False.
    """
    global latest_state, waiting_state
    try:
        if isinstance(message, bytes):
            obj = msgpack.unpackb(message, raw=False)
            # Expecting StreamData shaped like {"state": {...}, "timestamp": ...}
            # Save it
            latest_state = obj
            state = latest_state.get("state", {})
            pos  = state.get("position", [0,0,0])
            rot  = state.get("rotation", [0,0,0])
            lv   = state.get("localVelocity", [0,0,0])
            lav  = state.get("localAngularVelocity", [0,0,0])

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
    global waiting_state, latest_state, latest_cmd
    dt = 1.0 / UPDATE_HZ   # request rate: 20 Hz (tune as needed)
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

        # update latest_cmd (thread-safe latest-wins)
        with latest_cmd_lock:
            latest_cmd = action

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

# ---------------- Virtual controller updater thread ----------------

def clamp(v, a, b):
    return max(a, min(b, v))

def map_stick_x(v):
    # v expected in -1..1 -> vgamepad left_joystick_float expects -1..1
    return clamp(v if v is not None else 0.0, -1.0, 1.0)

def map_trigger(v):
    # v expected in 0..1 -> map to 0..255
    x = clamp(v if v is not None else 0.0, 0.0, 1.0)
    return int(x * 255)

def virtual_controller_loop():
    """
    Runs at UPDATE_HZ. Reads latest_cmd and updates vgpad accordingly.
    """
    global vgpad, latest_cmd
    if vgpad is None:
        print("[VC] No virtual pad available; exiting controller loop.")
        return

    print("[VC] Virtual controller loop started at {:.1f} Hz".format(UPDATE_HZ))
    last_reset_pressed = False
    while True:
        start = time.time()
        # read latest_cmd
        cmd = None
        with latest_cmd_lock:
            if latest_cmd is not None:
                # copy for local use
                cmd = dict(latest_cmd)

        # default neutral
        steer = 0.0
        brake = 0.0
        armsUp = 0.0
        reset = 0.0

        if cmd is not None and cmd.get("cmd") == "ACTION":
            steer = float(cmd.get("steer", 0.0))
            brake = float(cmd.get("brake", 0.0))
            armsUp = float(cmd.get("armsUp", 0.0))
            reset = float(cmd.get("reset", 0.0))

        # map steer -> left stick X
        sx = map_stick_x(steer)
        sy = 0.0
        try:
            vgpad.left_joystick_float(sx, sy)
        except Exception:
            # some vgamepad versions use different method names; handle gracefully
            try:
                # integer API fallback (if available)
                # map -1..1 -> -32767..32767
                ix = int(sx * 32767)
                iy = 0
                vgpad.left_joystick(ix, iy)
            except Exception:
                print("[VC] left_joystick update failed; check vgamepad API")
                traceback.print_exc()

        # map brake -> left trigger
        try:
            vgpad.left_trigger(map_trigger(brake))
        except Exception:
            print("[VC] left_trigger failed")
            traceback.print_exc()

        # map armsUp -> right trigger
        try:
            vgpad.right_trigger(map_trigger(armsUp))
        except Exception:
            print("[VC] right_trigger failed")
            traceback.print_exc()

        # map reset -> Y button (simple threshold)
        if reset > 0.5:
            vgpad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_Y)
            last_reset_pressed = True
        else:
            if last_reset_pressed:
                vgpad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_Y)
                last_reset_pressed = False

        # Finally push to driver
        try:
            vgpad.update()
        except Exception:
            print("[VC] vgpad.update() raised exception")
            traceback.print_exc()

        elapsed = time.time() - start
        sleep_t = UPDATE_INTERVAL - elapsed
        if sleep_t > 0:
            time.sleep(sleep_t)
        else:
            time.sleep(0.001)

# ---------------- create virtual pad ----------------

def create_virtual_x360():
    try:
        pad = vg.VX360Gamepad()
        print("[VC] Virtual X360 created")
        return pad
    except Exception:
        print("[VC] Failed creating virtual X360. Ensure ViGEmBus is installed and you're running as admin.")
        traceback.print_exc()
        return None

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
vc_thread = None

def cleanup(sig=None, frame=None):
    global ws_global, vgpad
    print("\n[EXIT] Cleaning up...")
    try:
        if ws_global:
            ws_global.close()
    except:
        pass
    try:
        if vgpad:
            vgpad.reset()
            vgpad = None
    except:
        pass
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

def start():
    global vgpad, vc_thread, ws_global

    # create virtual pad
    vgpad = create_virtual_x360()
    if vgpad is None:
        print("Virtual pad creation failed; exiting.")
        return

    # start virtual controller thread
    vc_thread = threading.Thread(target=virtual_controller_loop, daemon=True)
    vc_thread.start()

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
