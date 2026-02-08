import argparse
import ast
import json
import logging as log
import os
import queue
import re
import socket
import sys
import threading
import time
import traceback
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from libs.pysecur3.client import MCPClient
from libs.pysecur3.MCP import MCPSetState
import libs.mqtt.client as paho

COMMANDS = {
    "get_door_state": lambda d: get_door_status(d),
    "get_door_position": lambda d: get_door_status(d),
    "up": lambda d: do_door_action("up", d),
    "down": lambda d: do_door_action("down", d),
    "open": lambda d: smart_open(d),        # Smart: checks position first
    "close": lambda d: smart_close(d),      # Smart: checks position first
    "force_open": lambda d: do_door_action("up", d),    # Force: sends impulse regardless
    "force_close": lambda d: do_door_action("down", d), # Force: sends impulse regardless
    "stop": lambda d: do_door_action("stop", d),
    "impulse": lambda d: do_door_action("impulse", d),
    "partial": lambda d: do_door_action("partial", d),
    "light": lambda d: do_door_action("light", d),
    "get_ports": lambda _: get_ports(),
    "get_version": lambda _: get_gw_version(),
    "get_gw_version": lambda _: get_gw_version(),
    "login": lambda _: do_gw_login(),
    "sys_restart": lambda _: init_bisecur_gw(True),
    "init_bisecur_gw": lambda _: init_bisecur_gw(True),
}

# Command queue for handling gateway busy situations
COMMAND_QUEUE = queue.Queue(maxsize=10)
QUEUE_WORKER_RUNNING = False

LOGFORMAT = "%(asctime)s [%(filename)s:%(lineno)3s]  %(message)s"

parser = argparse.ArgumentParser(description="Bisecur2MQTT Service")
parser.add_argument("--bisecur_user", default="")
parser.add_argument("--bisecur_pw", default="")
parser.add_argument("--bisecur_ip", default="")
parser.add_argument("--bisecur_mac", default="FF:FF:FF:FF:FF:FF")
parser.add_argument("--src_mac", default="FF:FF:FF:FF:FF:FF")
parser.add_argument("--mqtt_broker", default="localhost")
parser.add_argument("--mqtt_port", type=int, default=1883)
parser.add_argument("--mqtt_clientid", default="mqtt2bisecur")
parser.add_argument("--mqtt_username", default="")
parser.add_argument("--mqtt_password", default="")
parser.add_argument("--mqtt_tls", type=lambda x: x.lower() == 'true', default=False)
parser.add_argument("--mqtt_topic_base", default="bisecur2mqtt")
parser.add_argument("--mqtt_topic_HA_discovery", default="homeassistant")
parser.add_argument("--logfile", default="mqtt2bisecur.log")
parser.add_argument("--logs", type=lambda x: x.lower() == 'true', default=False)
parser.add_argument("--doors_port", nargs='+', type=int, default=[0])
parser.add_argument("--poll_interval", type=int, default=30, help="Интервал опроса статуса в секундах (по умолчанию: 30)")
parser.add_argument("--poll_max_retries", type=int, default=2, help="Max retries for periodic polling (default: 2)")
args = parser.parse_args()

GATEWAY_VERSION = None
GATEWAY_VERSION_RESP  = None
LOG_TXT_ENABLED = args.logs
LOGFILE = args.logfile
MQTT_TOPIC_BASE = args.mqtt_topic_base
VERSION = "1.0.0"
DEBUG = False
gateway_lock = threading.Lock()
CHECK_STATUS_START = True
MQTT_CLIENT_SUB = None
MQTT_CLIENT_PUB = None
IS_ACTIVE_TASK = threading.Event()
last_request_time = {}
CLI = None
LAST_DOOR_STATE = {}       # Per-door state: {door_id: state}
POS_TRACKING_THREAD = {}   # Per-door tracking: {door_id: thread}
DO_EXIT_THREAD = {}        # Per-door exit flag: {door_id: bool}
MAX_RETRIES = 10
CHECK_INTERVAL = args.poll_interval
POLL_MAX_RETRIES = args.poll_max_retries
GATEWAY_OFFLINE = False
GATEWAY_OFFLINE_COUNT = 0
GATEWAY_OFFLINE_THRESHOLD = 3  # After 3 consecutive failures, consider gateway offline

# Адаптивный интервал опроса — шлюз не рассчитан на постоянный polling
# Родное приложение Hörmann подключается только при открытии
IDLE_POLL_INTERVAL = 300       # Интервал опроса в покое (5 минут)
ACTIVE_POLL_INTERVAL = 10     # Интервал опроса после команды (для tracking)
ACTIVE_POLL_DURATION = 120     # Сколько секунд "активный" режим после команды
LAST_COMMAND_TIME = 0          # Время последней команды пользователя
LAST_GW_ACTIVITY = 0           # Время последней успешной связи со шлюзом
GW_STALE_TIMEOUT = 8           # Шлюз сбрасывает TCP непредсказуемо (5-15с), reconnect заранее

# Per-door failure tracking (НЕ блокировать весь шлюз из-за одной двери)
DOOR_FAILURE_COUNT = {}        # {door_id: count} - счётчик ошибок для каждой двери
DOOR_COOLDOWN_UNTIL = {}       # {door_id: timestamp} - до какого времени пропускать дверь
DOOR_FAILURE_THRESHOLD = 3     # После скольких ошибок подряд ставить кулдаун
DOOR_COOLDOWN_BASE = 120       # Базовый кулдаун в секундах (2 минуты)
DOOR_COOLDOWN_MAX = 600        # Максимальный кулдаун (10 минут)

for handler in log.root.handlers[:]:
    log.root.removeHandler(handler)

if DEBUG:
    log.basicConfig(filename=LOGFILE, level=log.DEBUG, format=LOGFORMAT)
else:
    if LOG_TXT_ENABLED:
        log.basicConfig(filename=LOGFILE, level=log.INFO, format=LOGFORMAT)
    else:
        log.basicConfig(level=log.INFO, format=LOGFORMAT)

if not any(isinstance(h, log.StreamHandler) for h in log.getLogger().handlers):
    stderrLogger = log.StreamHandler()
    stderrLogger.setFormatter(log.Formatter(LOGFORMAT))
    log.getLogger().addHandler(stderrLogger)

log.info("🚀 Run bisecur2mqtt...")
log.debug("🚀 DEBUG MODE")


def do_command(cmd, set_door=None):
    global IS_ACTIVE_TASK, LAST_COMMAND_TIME
    IS_ACTIVE_TASK.set()
    LAST_COMMAND_TIME = time.time()
    cmd = cmd.lower().strip()
    resp = None
    try:
        resp = COMMANDS.get(cmd, lambda _: f"Command '{cmd}' is not recognised")(set_door)
        check_mcp_error(resp)
        publish_to_mqtt(f"send_command/response", resp)
    except Exception as ex:
        log.error(ex)
        traceback.print_exc()
        check_mcp_error(resp)
    finally:
        IS_ACTIVE_TASK.clear()


def publish_to_mqtt(topic, payload, topic_base=args.mqtt_topic_base, qos=0, retain=False, ts_only=False):
    if MQTT_CLIENT_SUB:
        if not isinstance(payload, str):
            payload = str(payload)
        try:
            if not ts_only:
                log.debug(f"---> MQTT pub: {topic_base}/{topic} {payload}")
                MQTT_CLIENT_SUB.publish(f"{topic_base}/{topic}", payload, qos=qos, retain=retain)
            log.debug(f"---> MQTT pub: {topic_base}/{topic}_ts {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}")
            MQTT_CLIENT_SUB.publish(f"{topic_base}/{topic}_ts", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), qos=qos,retain=retain)
        except Exception as ex:
            log.error(f"Error in topic: {topic}, payload: {payload}")
            log.error(ex)
    else:
        log.warning(f"Ignoring publish to broker as 'MQTT_CLIENT_PUB' not initialised ({topic} {payload})")


def get_gw_version():
    global GATEWAY_VERSION
    global GATEWAY_VERSION_RESP
    retries = 0

    if GATEWAY_VERSION is not None:
        return GATEWAY_VERSION_RESP, GATEWAY_VERSION

    while retries < MAX_RETRIES:
        try:
            with gateway_lock:
                if CLI is None:
                    log.error("⚠️ Error: CLI not initialized")
                    return None, None

                resp = CLI.get_gw_version()
            if not resp or not hasattr(resp.payload, "command") or not hasattr(resp.payload.command, "gw_version"):
                log.error("❌ Error: Invalid response from `get_gw_version()`")
                return None, None

            GATEWAY_VERSION = resp.payload.command.gw_version
            GATEWAY_VERSION_RESP = resp
            log.info(f"✅ Gateway HW Version: {GATEWAY_VERSION}")
            publish_to_mqtt("attributes/gw_hw_version", GATEWAY_VERSION)

            return GATEWAY_VERSION_RESP, GATEWAY_VERSION

        except Exception as e:
            error_msg = str(e)
            if "PORT_ERROR" in error_msg or "Code: 10" in error_msg:
                retries += 1
                wait_time = 2 * retries
                log.warning(f"🔄Gateway busy (Retries {retries}/{MAX_RETRIES}) - wait {wait_time} sec...")
                time.sleep(wait_time)
                continue

            log.error(f"❌ Unknown error in get_gw_version(): {e}")
            traceback.print_exc()
            return None, None

    log.error("❌ Retry limit reached for `get_gw_version()`")
    return None, None


def get_ports():
    """Get available ports/groups from the gateway."""
    if CLI is None:
        log.error("⚠️ Cannot get ports: CLI not initialized")
        return None, None

    cmd_mcp = {"CMD": "GET_GROUPS", "FORUSER": 0}
    try:
        resp = CLI.jcmp(cmd_mcp)
        ports = resp.payload.payload
        ports = ast.literal_eval(ports.decode("utf-8"))

        log.info(f"Ports for user 0: {json.dumps(ports, indent=4, sort_keys=True)}")
        publish_to_mqtt("attributes/user0_ports", json.dumps(ports))

        return resp, ports
    except Exception as ex:
        log.error(f"Error getting ports: {ex}")
        traceback.print_exc()
        return None, None


def check_broken_pipe(err_msg):
    if "errno 32" in err_msg or "broken pipe" in err_msg:
        log.error("ERROR: Restarting due to broken pipe")
        init_bisecur_gw(True)
        time.sleep(2)  # Пауза после reconnect - шлюз нужно время
        return True
    else:
        return False


def get_door_status(set_door, max_retries=None, allow_reconnect=True):
    """Get door status from gateway.

    Args:
        set_door: door port ID
        max_retries: limit retry attempts (None = MAX_RETRIES)
        allow_reconnect: if False, don't reconnect on error (for periodic polling)
    """
    set_door = int(set_door)
    retries = 0
    global last_request_time, LAST_GW_ACTIVITY
    if set_door not in last_request_time:
        last_request_time[set_door] = 0

    effective_max_retries = max_retries if max_retries is not None else MAX_RETRIES
    while retries < effective_max_retries:
        now = time.time()
        if now - last_request_time[set_door] < 3:
            log.debug(f"⏳ Flood protection ({set_door}), wait...")
            time.sleep(1)
            continue

        lock_acquired = gateway_lock.acquire(blocking=False)
        if not lock_acquired:
            log.warning(f"🚧 Get door status({set_door}) skipped because lock is busy!")
            return None, -1, None
        try:
            last_request_time[set_door] = time.time()
            if CLI is None:
                log.error("⚠️ Error: CLI not initialized")
                return None, -1, None
            log.info(f"📡 Sending a get transition request({set_door})...")
            resp = CLI.get_transition(set_door)

            if resp is None:
                log.warning(f"⚠️ resp=None for door {set_door}")
                return None, -1, None

            state = None
            if resp.payload and hasattr(resp.payload.command, "percent_open"):
                LAST_GW_ACTIVITY = time.time()
                position = resp.payload.command.percent_open
                if position == 0:
                    state = "closed"
                    log.info(f"🚪Door -> {set_door} is closed")
                elif position == 100:
                    state = "open"
                    log.info(f"🚪Door -> {set_door} is open")
                else:
                    log.info(f"🚪Door -> {set_door} is {resp.payload.command.percent_open}% OPEN")
                log.info(f"🚪Door -> {set_door} position: {position} and state {state} to MQTT....")
                publish_to_mqtt(f"garage_door/{set_door}/position", position, retain=True)
                if state:
                    publish_to_mqtt(f"garage_door/{set_door}/state", state, retain=True)
                return resp, position, state
            else:
                log.warning(f"get_transition response has no 'percent_open' (resp: {resp})")
                if not allow_reconnect:
                    # Для polling: сессия повреждена, пусть poll_door_status сделает reconnect
                    return None, -1, None
                retries += 1
                time.sleep(1)
                continue

        except Exception as ex:
            retries += 1
            err_str = str(ex)
            log.error(f"❌ get_door_status error ({retries}/{effective_max_retries}): {err_str}")

            if "PORT_ERROR" in err_str or "Code: 10" in err_str:
                log.warning(f"🔄 Gateway busy, wait 3 sec...")
                time.sleep(3)
                continue

            if allow_reconnect:
                if "PERMISSION_DENIED" in err_str or "Code: 12" in err_str:
                    log.warning(f"🔄 Permission denied, reconnecting...")
                    reconnect_to_bisecur()
                    time.sleep(2)
                    continue
                if "reset" in err_str.lower() or "broken" in err_str.lower() or "errno 32" in err_str.lower():
                    log.warning(f"🔄 Connection lost, reconnecting...")
                    reconnect_to_bisecur()
                    time.sleep(2)
                    continue
                if retries < effective_max_retries:
                    reconnect_to_bisecur()
                    time.sleep(1)
                    continue
            else:
                # Periodic polling: don't reconnect, just return failure
                log.warning(f"⚠️ Door {set_door} poll failed: {err_str[:80]}")
                return None, -1, None
        finally:
            if gateway_lock.locked():
                gateway_lock.release()
    return None, -1, None


def track_realtime_door_position(current_pos=None, last_action=None, set_door=0):
    """Track door position in real-time after command. Per-door tracking."""
    publish_to_mqtt(f"garage_door/{set_door}/position", current_pos, retain=True)

    global LAST_DOOR_STATE, DO_EXIT_THREAD
    set_door = int(set_door)

    state = ""
    last_pos = None

    DO_EXIT_THREAD[set_door] = False
    time.sleep(2)
    while not DO_EXIT_THREAD.get(set_door, False) and ((state != "open" and last_action == "up open") or (
            state != "closed" and last_action in "down close") or current_pos != last_pos):
        time.sleep(3)
        last_pos = current_pos
        resp, current_pos, state = get_door_status(set_door)
        if resp is None:
            DO_EXIT_THREAD[set_door] = True
            break
        if not check_mcp_error(resp):
            if current_pos < last_pos:
                state = "closing"
            elif current_pos > last_pos:
                state = "opening"
            elif current_pos == 100:
                state = "open"
            elif current_pos == 0:
                state = "closed"
            else:
                state = "unknown"
            LAST_DOOR_STATE[set_door] = state
            publish_to_mqtt(f"garage_door/{set_door}/position", current_pos, retain=True)
            publish_to_mqtt(f"garage_door/{set_door}/state", state, retain=True)
    LAST_DOOR_STATE[set_door] = state
    return state


def publish_command_status(action, door, status, message=""):
    """Publish command execution status to MQTT for user feedback."""
    status_obj = {
        "action": action,
        "door": door,
        "status": status,  # pending, retrying, success, failed
        "message": message,
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    }
    publish_to_mqtt("command/status", json.dumps(status_obj))
    log.info(f"📤 Command status: {action} door {door} -> {status} {message}")


def smart_open(set_door):
    """Open door only if not already open. Checks position first."""
    log.info(f"🔓 Smart open door {set_door} - checking position first...")
    resp, position, state = get_door_status(set_door, max_retries=2)

    if position is None or position == -1:
        log.warning(f"⚠️ Cannot get door position, sending UP command anyway")
        return do_door_action("up", set_door)  # UP вместо impulse - гарантированно открывает

    if position >= 95:  # Already open (allowing small margin)
        log.info(f"✅ Door {set_door} already open ({position}%), no action needed")
        publish_command_status("smart_open", set_door, "success", f"Already open ({position}%)")
        return {"status": "already_open", "position": position}

    if state == "opening":
        log.info(f"✅ Door {set_door} already opening ({position}%), no action needed")
        publish_command_status("smart_open", set_door, "success", f"Already opening ({position}%)")
        return {"status": "already_opening", "position": position}

    log.info(f"🔓 Door at {position}%, sending open command...")
    return do_door_action("up", set_door)


def smart_close(set_door):
    """Close door only if not already closed. Checks position first."""
    log.info(f"🔒 Smart close door {set_door} - checking position first...")
    resp, position, state = get_door_status(set_door, max_retries=2)

    if position is None or position == -1:
        log.warning(f"⚠️ Cannot get door position, sending DOWN command anyway")
        return do_door_action("down", set_door)  # DOWN вместо impulse - гарантированно закрывает

    if position <= 5:  # Already closed (allowing small margin)
        log.info(f"✅ Door {set_door} already closed ({position}%), no action needed")
        publish_command_status("smart_close", set_door, "success", f"Already closed ({position}%)")
        return {"status": "already_closed", "position": position}

    if state == "closing":
        log.info(f"✅ Door {set_door} already closing ({position}%), no action needed")
        publish_command_status("smart_close", set_door, "success", f"Already closing ({position}%)")
        return {"status": "already_closing", "position": position}

    log.info(f"🔒 Door at {position}%, sending close command...")
    return do_door_action("down", set_door)


def set_position(set_door, target_position):
    """Open/close door to specific position (0-100%)."""
    target_position = max(0, min(100, int(target_position)))
    log.info(f"🎯 Setting door {set_door} to {target_position}%...")
    publish_command_status("set_position", set_door, "pending", f"Target: {target_position}%")

    # Get current position
    resp, current_pos, state = get_door_status(set_door, max_retries=2)
    if current_pos is None or current_pos == -1:
        log.error("⚠️ Cannot get current position")
        publish_command_status("set_position", set_door, "failed", "Cannot get position")
        return None

    log.info(f"📍 Current: {current_pos}%, Target: {target_position}%")

    # Already at target?
    if abs(current_pos - target_position) <= 5:
        log.info(f"✅ Already at {current_pos}% (target: {target_position}%)")
        publish_command_status("set_position", set_door, "success", f"Already at {current_pos}%")
        return {"status": "already_at_target", "position": current_pos}

    # Determine direction
    if current_pos < target_position:
        direction = "opening"
        compare = lambda pos: pos >= target_position - 3
    else:
        direction = "closing"
        compare = lambda pos: pos <= target_position + 3

    # Send initial impulse
    log.info(f"🔄 Starting {direction}...")
    do_door_action("impulse", set_door)
    time.sleep(1.5)

    # Monitor until target reached
    max_iterations = 30
    for _ in range(max_iterations):
        resp, pos, _ = get_door_status(set_door, max_retries=1)
        if pos is None or pos == -1:
            continue

        log.info(f"   Position: {pos}%")

        if compare(pos):
            log.info(f"🛑 Target reached, stopping...")
            do_door_action("impulse", set_door)  # Stop
            time.sleep(1)
            resp, final_pos, _ = get_door_status(set_door, max_retries=2)
            log.info(f"✅ Final position: {final_pos}%")
            publish_command_status("set_position", set_door, "success", f"Position: {final_pos}%")
            return {"status": "success", "position": final_pos}

        # Check if door stopped moving unexpectedly
        if (direction == "opening" and pos >= 98) or (direction == "closing" and pos <= 2):
            log.warning(f"⚠️ Door reached end ({pos}%) before target")
            publish_command_status("set_position", set_door, "partial", f"Reached {pos}%")
            return {"status": "partial", "position": pos}

        time.sleep(1.5)

    log.error("❌ Timeout waiting for target position")
    publish_command_status("set_position", set_door, "failed", "Timeout")
    return None


def do_door_action(action, set_door, max_retries=5):
    """Execute door action with automatic retry on failure. Per-door state tracking."""
    global LAST_DOOR_STATE, POS_TRACKING_THREAD, DO_EXIT_THREAD, LAST_GW_ACTIVITY
    value = None
    set_door = int(set_door)

    if action == "stop":
        door_state = LAST_DOOR_STATE.get(set_door)
        if door_state == "opening":
            action = "down"
        elif door_state == "closing":
            action = "up"
        else:
            log_msg = f"Ignoring 'stop' - door {set_door} movement unknown (state: '{door_state}')"
            log.warning(log_msg)
            publish_command_status("stop", set_door, "failed", log_msg)
            return log_msg

    if action not in "impulse up down partial light stop":
        log.error(f"Unknown action '{action}'")
        publish_command_status(action, set_door, "failed", "Unknown action")
        return None

    port = set_door
    retries = 0
    backoff_times = [0.3, 0.5, 1.0, 1.5, 2.0]  # Быстрый backoff для команд

    publish_command_status(action, set_door, "pending")

    while retries <= max_retries:
        try:
            # Проверка и принудительный reconnect если CLI не инициализирован
            if CLI is None:
                log.warning("⚠️ CLI not initialized, trying to init...")
                init_bisecur_gw(True)
                if CLI is None:
                    log.error("❌ Cannot init gateway")
                    publish_command_status(action, set_door, "failed", "Gateway not initialized")
                    return None

            # Проверка соединения и reconnect
            if not CLI.is_connected() or CLI.last_error:
                log.warning(f"🔄 Gateway needs reconnect (connected={CLI.is_connected()}, last_error={CLI.last_error})")
                publish_command_status(action, set_door, "retrying", "Reconnecting to gateway")
                try:
                    CLI.reconnect()
                    do_gw_login()
                    CLI.last_error = None  # Сбросить ошибку после успешного reconnect
                except Exception as reconn_ex:
                    log.error(f"Reconnect failed: {reconn_ex}")
                    retries += 1
                    time.sleep(backoff_times[min(retries - 1, len(backoff_times) - 1)])
                    continue

            mcp_cmd = MCPSetState.construct(port)
            action_resp = CLI.generic(mcp_cmd, False)

            if not check_mcp_error(action_resp):
                LAST_GW_ACTIVITY = time.time()
                current_pos = action_resp.payload.command.percent_open if hasattr(action_resp.payload.command, "percent_open") else -1
                publish_command_status(action, set_door, "success", f"Position: {current_pos}%")

                # Start position tracking thread (per-door)
                if set_door in POS_TRACKING_THREAD and POS_TRACKING_THREAD[set_door].is_alive():
                    DO_EXIT_THREAD[set_door] = True
                    time.sleep(0.3)
                    counter = 0
                    while POS_TRACKING_THREAD[set_door].is_alive() and counter < 15:
                        time.sleep(0.7)
                        counter += 0.5

                POS_TRACKING_THREAD[set_door] = threading.Thread(
                    name=f'pos_tracking_door_{set_door}',
                    target=track_realtime_door_position,
                    args=(current_pos, action, set_door)
                )
                POS_TRACKING_THREAD[set_door].start()
                return action_resp
            else:
                raise Exception("MCP error in response")

        except Exception as ex:
            error_str = str(ex).lower()
            retries += 1

            # Check for recoverable errors
            if "port_error" in error_str or "code: 10" in error_str:
                if retries <= max_retries:
                    wait_time = backoff_times[min(retries - 1, len(backoff_times) - 1)]
                    log.warning(f"🔄 Gateway busy, retry {retries}/{max_retries} in {wait_time}s...")
                    publish_command_status(action, set_door, "retrying", f"Gateway busy, retry {retries}/{max_retries}")
                    time.sleep(wait_time)
                    continue

            # Check for connection errors (включая reset by peer)
            if any(x in error_str for x in ["errno 32", "broken pipe", "connection", "reset by peer", "errno 104"]):
                if retries <= max_retries:
                    log.warning(f"🔄 Connection lost, reconnecting... retry {retries}/{max_retries}")
                    publish_command_status(action, set_door, "retrying", f"Reconnecting, retry {retries}/{max_retries}")
                    try:
                        CLI.reconnect()
                        do_gw_login()
                        CLI.last_error = None  # Сбросить ошибку
                    except Exception as reconn_ex:
                        log.error(f"Reconnect failed: {reconn_ex}")
                    time.sleep(0.3)  # Быстрее retry для команд
                    continue

            # Для любых других ошибок - пробуем reconnect и retry
            if retries <= max_retries:
                log.warning(f"🔄 Unknown error, trying reconnect... retry {retries}/{max_retries}: {ex}")
                publish_command_status(action, set_door, "retrying", f"Retry {retries}/{max_retries}")
                try:
                    CLI.reconnect()
                    do_gw_login()
                    CLI.last_error = None
                except Exception as reconn_ex:
                    log.error(f"Reconnect failed: {reconn_ex}")
                time.sleep(backoff_times[min(retries - 1, len(backoff_times) - 1)])
                continue

            # Max retries reached
            log.error(f"❌ Command failed after {max_retries} retries: {ex}")
            publish_command_status(action, set_door, "failed", str(ex)[:100])
            return None

    # Max retries exceeded
    publish_command_status(action, set_door, "failed", f"Max retries ({max_retries}) exceeded")
    return None


def do_gw_login():
    """Authenticate with the Bisecur gateway."""
    if CLI is None:
        log.error("⚠️ Cannot login: CLI not initialized")
        return None

    bisecur_user = args.bisecur_user
    bisecur_pw = args.bisecur_pw
    log.debug(f"Logging in to Bisecur Gateway as user '{bisecur_user}'")
    try:
        CLI.login(bisecur_user, bisecur_pw)
        if CLI.token:
            log.info(f"✅ User '{bisecur_user}' logged in with token '{CLI.token}'")
            time.sleep(2)  # Пауза после логина - шлюз медленный
            CLI.last_error = None  # Сброс ошибок после успешного логина
            return CLI.token
        else:
            log.warning(f"🔴 Gateway login failed for user '{bisecur_user}'")
            return None
    except Exception as ex:
        log.error(f"🔴 Login exception: {ex}")
        traceback.print_exc()
        return None


def check_mcp_error(resp):
    """Check for MCP errors and publish to MQTT. Returns error_obj if error, None otherwise."""
    error_obj = None

    if CLI and CLI.last_error:
        log.error(f"CLI.last_error detected: {CLI.last_error}")
        if resp and hasattr(resp, "payload") and hasattr(resp.payload, "command") and hasattr(resp.payload.command, "error_code"):
            error_obj = {
                "error_code": resp.payload.command.error_code.value,
                "error": resp.payload.command.error_code.name
            }
        else:
            error_obj = {"error_code": "Unknown", "error": str(resp) if resp else "Unknown error"}

    elif resp and hasattr(resp, "payload") and hasattr(resp.payload, "command_id"):
        if resp.payload.command_id == 1 and hasattr(resp.payload.command, "error_code"):
            log.error(f"MCP error: {resp.payload.command.error_code.name} (code: {resp.payload.command.error_code.value})")
            error_obj = {
                "error_code": resp.payload.command.error_code.value,
                "error": resp.payload.command.error_code.name
            }

    if error_obj:
        publish_to_mqtt("send_command/error", json.dumps(error_obj))
    else:
        publish_to_mqtt("send_command/error", "")

    return error_obj


def on_message(mosq, userdata, msg):
    log.info(f"---> Topic '{msg.topic}' received command '{msg.payload.decode('utf-8')}'")
    cmd = msg.payload.decode('utf-8').strip()
    parts = cmd.split("_")

    # Handle position command: position_50_1 (set door 1 to 50%)
    if re.match(r"^position_\d+_\d+$", cmd):
        target_pos = int(parts[1])
        door = int(parts[2])
        if door in args.doors_port:
            log.info(f"🎯 Position command: door {door} to {target_pos}%")
            threading.Thread(target=set_position, args=(door, target_pos), daemon=True).start()
        else:
            log.warning(f"Door {door} not in configured ports")
    # Handle standard command: open_1, close_1, etc.
    elif re.match(r"^[a-zA-Z]+_\d+$", cmd):
        if int(parts[1]) in args.doors_port:
            log.info(f"Door: {parts[1]} and Command: {parts[0]}")
            do_command(parts[0], parts[1])
        else:
            log.warning(f"Door {parts[1]} not in configured ports")
    else:
        log.warning(f"Received invalid command format: {cmd}")


def on_connect(client, userdata, flags, rc):
    clear_command_topic()
    log.info(f"📡 Connected to MQTT broker (RC={rc}). Subscribing to command topic.")
    if rc == 0:
        sub_topic = f"{MQTT_TOPIC_BASE}/send_command/command"
        client.subscribe(sub_topic, 0)
        log.info(f"✅ Subscribed to {sub_topic}")
        for set_door in args.doors_port:
            publish_to_mqtt(f"{set_door}/state", "online", retain=True)
            log.info(f"🚪 Set door {set_door} availability to online")
        for set_door in args.doors_port:
            init_ha_discovery(set_door)
    else:
        log.error(f"❌ MQTT connection failed with code {rc}")


def on_disconnect(mosq, userdata, rc):
    log.info(f"📡 MQTT session disconnected (rc={rc})!!!")
    clear_command_topic()
    for set_door in args.doors_port:
        publish_to_mqtt(f"{set_door}/state", "offline", retain=True)
        log.info(f"Performing actions for port: {set_door}")
    time.sleep(10)


def clear_command_topic():
    log.info("📡 Clearing MQTT command topic...")
    MQTT_CLIENT_PUB.publish(f"{MQTT_TOPIC_BASE}/send_command/command", "\0", qos=0, retain=True)


def restart_script():
    log.error("MCPError.PERMISSION_DENIED detected. Restarting script...")
    python = sys.executable
    os.execl(python, python, *sys.argv)


def reconnect_to_bisecur():
    """Переподключение к шлюзу BiSecur.

    НЕ делает warm-up — warm-up делается в poll_door_status через get_transition.
    get_gw_version не прогревает door subsystem, поэтому бесполезен как warm-up.
    """
    global CLI, LAST_GW_ACTIVITY
    log.warning("🔄 Reconnecting to Bisecur Gateway...")
    try:
        if CLI:
            try:
                CLI.disconnect()
            except:
                pass
        time.sleep(1)
        init_bisecur_gw(True)
        if CLI and CLI.token:
            LAST_GW_ACTIVITY = time.time()
            time.sleep(3)  # Пауза после логина — шлюзу нужно время
            log.info("✅ Reconnect OK")
            return True
        else:
            log.error("❌ Failed to reconnect to Bisecur Gateway!")
            return False
    except Exception as e:
        log.error(f"❌ Error while reconnecting: {e}")
        return False


def init_ha_discovery(set_door):
    payload = {"door_commands_list": ["impulse", "up", "down", "partial", "stop", "light"],
               "json_attributes_topic": f"{MQTT_TOPIC_BASE}/{set_door}/attributes",
               "name": "Bisecur Gateway: Garage Door",
               "schema": "state",
               "supported_features": ["impulse", "up", "down", "partial", "stop", "light", "get_door_state",
                                      "get_ports", "login", "sys_reset"],
               "availability_topic": f"{MQTT_TOPIC_BASE}/{set_door}/state", "payload_available": "online",
               "payload_not_available": "offline", "unique_id": "bs_garage_door", "device_class": "garage",
               "payload_close": "down", "payload_open": "up", "payload_stop": "impulse", "position_open": 100,
               "position_closed": 0, "position_topic": f"{MQTT_TOPIC_BASE}/{set_door}/garage_door/position",
               "state_topic": f"{MQTT_TOPIC_BASE}/{set_door}/garage_door/state",
               "command_topic": f"{MQTT_TOPIC_BASE}/send_command/command"}
    bisecur_mac = args.bisecur_mac.replace(':', '')
    bisecur_ip = args.bisecur_ip
    payload["connections"] = ["mac", bisecur_mac, "ip", bisecur_ip]
    payload["sw_version"] = VERSION
    _, payload["gw_hw_version"] = get_gw_version()
    publish_to_mqtt(f"cover/bisecur/{set_door}/config", json.dumps(payload), f"{args.mqtt_topic_HA_discovery}")
    publish_to_mqtt("attributes/system_version", VERSION)
    publish_to_mqtt("attributes/gw_ip_address", bisecur_ip)
    publish_to_mqtt("attributes/gw_mac_address", bisecur_mac)


def init_bisecur_gw(is_restart=False):
    global CLI
    if is_restart and CLI and not hasattr(CLI, "last_error"):
        CLI.logout()

    # Init Bisecur Gateway stuff
    src_mac = args.src_mac.replace(':', '') if args.src_mac else "FFFFFFFFFFFF"
    bisecur_mac = args.bisecur_mac.replace(':', '') if args.bisecur_mac else ""
    bisecur_ip = args.bisecur_ip if args.bisecur_ip else None
    if not (bisecur_ip and bisecur_mac):
        log.error("ERROR: bisecur Gateway IP and MAC addresses must be specified in the config file")
    log.debug(f"INIT: Gateway IP: {bisecur_ip}, bisecur_mac: {bisecur_mac}, src_mac: {src_mac}")
    CLI = MCPClient(bisecur_ip, 4000, bytes.fromhex(src_mac), bytes.fromhex(bisecur_mac))
    login_token = do_gw_login()
    if not login_token:
        log.error("ERROR: login token")

    return login_token


def poll_door_status(set_door):
    """Опрос одной двери — fresh connect стратегия.

    Шлюз BiSecur НЕ рассчитан на постоянные соединения (родное приложение
    подключается только при открытии). Каждый опрос:
    1. Fresh reconnect (disconnect → connect → login → 3с пауза)
    2. get_transition как warm-up (первый запрос может быть PORT_ERROR)
    3. Если warm-up не удался — retry get_transition
    4. Если всё плохо — второе подключение

    Returns: (resp, position, state) or (None, -1, None) on real failure.
    """
    set_door = int(set_door)

    for conn_attempt in range(2):
        # Fresh connect для каждого poll cycle
        try:
            reconnect_to_bisecur()
        except Exception as e:
            log.error(f"❌ Reconnect failed ({conn_attempt+1}/2): {e}")
            time.sleep(3)
            continue

        # До 3 попыток get_transition (первая = warm-up)
        for query_attempt in range(3):
            resp, position, state = get_door_status(set_door, max_retries=1, allow_reconnect=False)
            if resp is not None:
                return resp, position, state

            # Проверяем тип ошибки
            err_info = str(CLI.last_error) if CLI and CLI.last_error else ""

            if "PORT_ERROR" in err_info or "Code: 10" in err_info:
                CLI.last_error = None
                if query_attempt < 2:
                    log.debug(f"🔄 PORT_ERROR (warm-up), retry через 3с...")
                    time.sleep(3)
                    continue
                log.warning(f"⚠️ Дверь {set_door}: PORT_ERROR 3x, порт не отвечает")
                return None, -1, None

            # Мёртвый сокет или другая ошибка → новое подключение
            if CLI and CLI.last_error:
                CLI.last_error = None
            break  # Выход из query loop → следующее подключение

        time.sleep(3)

    log.warning(f"⚠️ Дверь {set_door}: не удалось опросить после 2 подключений")
    return None, -1, None


def periodic_door_status_check():
    """Периодический опрос статуса дверей.

    Шлюз BiSecur НЕ рассчитан на постоянный polling (родное приложение
    подключается только при открытии). Адаптивный интервал:
    - В покое: IDLE_POLL_INTERVAL (300с/5мин) — минимальная нагрузка на шлюз
    - После команды: ACTIVE_POLL_INTERVAL (10с) на 2 мин — для tracking
    - Per-door cooldown: если дверь не отвечает, опрашиваем ещё реже
    """
    global DOOR_FAILURE_COUNT, DOOR_COOLDOWN_UNTIL

    while True:
        if IS_ACTIVE_TASK.is_set():
            log.debug("⏳ Команда выполняется, пропуск опроса.")
            time.sleep(5)
            continue

        for i, set_door in enumerate(args.doors_port):
            # Per-door cooldown check
            now = time.time()
            if set_door in DOOR_COOLDOWN_UNTIL and now < DOOR_COOLDOWN_UNTIL[set_door]:
                remaining = int(DOOR_COOLDOWN_UNTIL[set_door] - now)
                log.debug(f"⏳ Дверь {set_door} в кулдауне ещё {remaining}с")
                continue

            log.info(f"🔄 Опрос двери {set_door}...")
            resp, position, state = poll_door_status(set_door)

            if resp is not None:
                log.info(f"✅ Дверь {set_door}: position {position}, state {state}")
                DOOR_FAILURE_COUNT[set_door] = 0
                if set_door in DOOR_COOLDOWN_UNTIL:
                    del DOOR_COOLDOWN_UNTIL[set_door]
            else:
                DOOR_FAILURE_COUNT[set_door] = DOOR_FAILURE_COUNT.get(set_door, 0) + 1
                count = DOOR_FAILURE_COUNT[set_door]
                log.warning(f"⚠️ Дверь {set_door} не отвечает ({count} раз подряд)")

                if count >= DOOR_FAILURE_THRESHOLD:
                    cooldown = min(DOOR_COOLDOWN_BASE * (2 ** (count - DOOR_FAILURE_THRESHOLD)), DOOR_COOLDOWN_MAX)
                    DOOR_COOLDOWN_UNTIL[set_door] = time.time() + cooldown
                    log.warning(f"🛑 Дверь {set_door} уходит в кулдаун на {int(cooldown)}с после {count} ошибок")

            # Пауза между дверями — дать шлюзу отдохнуть
            if i < len(args.doors_port) - 1:
                time.sleep(2)

        timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        publish_to_mqtt("status/last_heartbeat", timestamp)

        # Адаптивный интервал: чаще после команд, реже в покое
        since_command = time.time() - LAST_COMMAND_TIME
        if LAST_COMMAND_TIME > 0 and since_command < ACTIVE_POLL_DURATION:
            interval = ACTIVE_POLL_INTERVAL
            log.debug(f"⏱️ Активный режим, опрос через {interval}с")
        else:
            interval = IDLE_POLL_INTERVAL
        time.sleep(interval)


def main():
    global MQTT_CLIENT_SUB, MQTT_CLIENT_PUB

    # Init mqtt
    userdata = {}

    clientid = args.mqtt_clientid if args.mqtt_clientid else f"biscure2mqtt-{os.getpid()}"
    MQTT_CLIENT_SUB = paho.Client(f"{clientid}_sub", clean_session=False)
    MQTT_CLIENT_PUB = paho.Client(f"{clientid}_pub", clean_session=False)

    for set_door in args.doors_port:
        MQTT_CLIENT_SUB.will_set(f"{MQTT_TOPIC_BASE}/{set_door}/state", "offline", qos=0, retain=True)

    MQTT_CLIENT_SUB.on_message = on_message
    MQTT_CLIENT_SUB.on_connect = on_connect
    MQTT_CLIENT_SUB.on_disconnect = on_disconnect

    if args.mqtt_username:
        MQTT_CLIENT_SUB.username_pw_set(args.mqtt_username, args.mqtt_password)
        MQTT_CLIENT_PUB.username_pw_set(args.mqtt_username, args.mqtt_password)

    if args.mqtt_tls:
        MQTT_CLIENT_SUB.tls_set()

    try:
        MQTT_CLIENT_SUB.connect(args.mqtt_broker, args.mqtt_port, 60)
    except Exception as e:
        log.error(f"❌ MQTT connect failed: {e}")
        MQTT_CLIENT_SUB = None
        return

    MQTT_CLIENT_PUB.connect(args.mqtt_broker, args.mqtt_port, 60)
    log.info("📡 Connecting to MQTT broker")

    # Init Bisecur Gateway
    init_bisecur_gw()

    if DEBUG:
        log.debug("Getting bisecur Gateway 'groups' for user 0...")
        cmd = {"CMD": "GET_GROUPS", "FORUSER": 0}
        CLI.jcmp(cmd)

    if CHECK_STATUS_START:
        log.info("🚀 Check status doors start scripts...")
        for set_door in args.doors_port:
            get_door_status(set_door)

    log.info("🚀 Starting a flow to check the gate status...")
    status_thread = threading.Thread(target=periodic_door_status_check, daemon=True)
    status_thread.start()
    log.info("✅ Thread periodic_door_status_check started successfully")

    while True:
        log.info("🔄 Entering loop_forever()... (script should not exit)")
        try:
            log.info(f"🔄 MQTT_CLIENT_SUB: {MQTT_CLIENT_SUB}")
            log.info(f"🔄 MQTT_CLIENT_PUB: {MQTT_CLIENT_PUB}")
            MQTT_CLIENT_SUB.loop_forever()
            log.error("❌ loop_forever() unexpectedly exited!")
        except socket.error:
            print("... doing sleep(5)")
            time.sleep(5)
        except Exception as e:
            log.error(f"❌ loop_forever() crashed: {e}")
            traceback.print_exc()
        except KeyboardInterrupt:
            log.info("Shutting down connections")
        finally:
            log.info("Exiting system. Changing MQTT state to 'offline'")
            for set_door in args.doors_port:
                MQTT_CLIENT_PUB.publish(f"{MQTT_TOPIC_BASE}/{set_door}/state", "offline", retain=True)
            MQTT_CLIENT_SUB.loop_stop()
            if CLI:
                if hasattr(CLI,
                           "last_error") and CLI.last_error is not None and "value" in CLI.last_error and CLI.last_error.value == 12:
                    log.info(f"Logging out of Bisecur Gateway ({CLI.token})")
                    CLI.logout()
                elif hasattr(CLI, "last_error"):
                    log.debug(f"Pre-logout: Biscure last error: ({CLI.last_error})")
            log.info(f"Active threads: {threading.active_count()}")
            log.debug("Tidying up spawned threads...")
            main_thread = threading.current_thread()
            for t in threading.enumerate():
                if t is main_thread:
                    log.info(f"... ignoring main_thread '{main_thread.name}'")
                    continue
                log.info(f"...joining spawned thread '{t.name}'")
                t.join()
            log.info("Done!")


if __name__ == '__main__':
    main()
