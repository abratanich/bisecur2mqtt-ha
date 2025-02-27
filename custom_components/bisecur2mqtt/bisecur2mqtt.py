import asyncio
import os
import re
import sys
import logging as log
import paho.mqtt.client as paho
import time
from datetime import datetime
import socket
import json, ast
import traceback
import threading
import argparse

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pysecur3.client import MCPClient
from pysecur3.MCP import MCPSetState

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
args = parser.parse_args()

LOG_TXT_ENABLED = args.logs
LOGFILE = args.logfile
MQTT_TOPIC_BASE = args.mqtt_topic_base
VERSION = "0.7.3"
DEBUG = False
gateway_lock = threading.Lock()
MQTT_COMMAND_SUBTOPIC = "send_command"
MQTT_QOS = 0
DOORS_PORT = [0, 1]
CHECK_STATUS_START = False
HEARTBEAT_INTERVAL = 10
last_heartbeat = 0
MQTT_CLIENT_SUB = None
MQTT_CLIENT_PUB = None
IS_ACTIVE_TASK = threading.Event()
last_request_time = {}
CLI = None
LAST_DOOR_STATE = None
POS_TRACKING_THREAD = None
DO_EXIT_THREAD = False
MAX_RETRIES = 10
CHECK_INTERVAL = 20
CMD_GET_TYPE = 49
CMD_GET_STATE = 50
CMD_SET_STATE = 51

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
log.debug("DEBUG MODE")


def do_command(cmd, set_door=None):
    global IS_ACTIVE_TASK
    IS_ACTIVE_TASK.set()
    cmd = cmd.lower().strip()
    publish_to_mqtt(f"{MQTT_COMMAND_SUBTOPIC}/command", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), ts_only=True)
    resp = None
    try:
        if cmd in "get_door_state get_door_position":
            resp, _, _ = get_door_status(set_door)

        elif cmd in "up down open close stop impulse partial light":
            sanitised_cmd = cmd.replace("open", "up").replace("close", "down")
            resp = do_door_action(sanitised_cmd, set_door)

        elif cmd == "get_ports":
            resp = get_ports()

        elif cmd in "get_version get_gw_version":
            resp = get_gw_version()

        elif cmd == "login":
            resp = do_gw_login()

        elif cmd in "sys_restart init_bisecur_gw":
            resp = init_bisecur_gw(True)

        else:
            resp = f"Command '{cmd} is not recognised"
        check_mcp_error(resp)
        publish_to_mqtt(f"{MQTT_COMMAND_SUBTOPIC}/response", resp)
    except Exception as ex:
        log.error(ex)
        traceback.print_exc()
        check_mcp_error(resp)
    finally:
        IS_ACTIVE_TASK.clear()


def publish_to_mqtt(topic, payload, topic_base=args.mqtt_topic_base, qos=MQTT_QOS, retain=False, ts_only=False):
    if MQTT_CLIENT_SUB:
        if not isinstance(payload, str):
            payload = str(payload)
        try:
            if not ts_only:
                log.debug(f"---> MQTT pub: {topic_base}/{topic} {payload}")
                MQTT_CLIENT_SUB.publish(f"{topic_base}/{topic}", payload, qos=qos, retain=retain)

            log.debug(f"---> MQTT pub: {topic_base}/{topic}_ts {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}")
            MQTT_CLIENT_SUB.publish(f"{topic_base}/{topic}_ts", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), qos=qos,
                                    retain=retain)
        except Exception as ex:
            log.error(f"Error in topic: {topic}, payload: {payload}")
            log.error(ex)

    else:
        log.warning(f"Ignoring publish to broker as 'MQTT_CLIENT_PUB' not initalised ({topic} {payload})")


def get_gw_version():
    retries = 0
    max_retries = 10

    while retries < max_retries:
        try:
            # ✅ Используем `with` для автоматического управления Lock
            with gateway_lock:
                if CLI is None:
                    log.error("⚠️ Error: CLI not initialized")
                    return None, None

                resp = CLI.get_gw_version()
            if not resp or not hasattr(resp.payload, "command") or not hasattr(resp.payload.command, "gw_version"):
                log.error("❌ Error: Invalid response from `get_gw_version()`")
                return None, None

            version = resp.payload.command.gw_version
            log.info(f"✅ Gateway HW Version: {version}")
            publish_to_mqtt("attributes/gw_hw_version", version)
            return resp, version

        except Exception as e:
            error_msg = str(e)
            if "PORT_ERROR" in error_msg or "Code: 10" in error_msg:
                retries += 1
                wait_time = 2 * retries
                log.warning(f"🔄Gateway busy (Retries {retries}/{max_retries}) - wait {wait_time} sec...")
                time.sleep(wait_time)
                continue

            log.error(f"❌ Unknown error in get_gw_version(): {e}")
            traceback.print_exc()
            return None, None

    log.error("❌ Retry limit reached for `get_gw_version()`")
    return None, None


def get_ports():
    cmd_mcp = {"CMD": "GET_GROUPS", "FORUSER": 0}
    try:
        resp = CLI.jcmp(cmd_mcp)
        ports = resp.payload.payload
        ports = ast.literal_eval(ports.decode("utf-8"))  # convert from binary

        log.info(f"Ports for user 0: {json.dumps(ports, indent=4, sort_keys=True)}")
        publish_to_mqtt("attributes/user0_ports", json.dumps(ports))

        return resp, ports
    except Exception as ex:
        log.error(ex)
        traceback.print_exc()


def check_broken_pipe(err_msg):
    if "errno 32" in err_msg or "broken pipe" in err_msg:
        log.error("ERROR: Restarting due to broken pipe")
        init_bisecur_gw(True)
        return True
    else:
        return False


def get_door_status(set_door):
    set_door = int(set_door)
    retries = 0
    global last_request_time
    if set_door not in last_request_time:
        last_request_time[set_door] = 0

    while retries < MAX_RETRIES:
        now = time.time()
        if now - last_request_time[set_door] < 2:  # ⏳ Ограничиваем частоту запросов
            log.warning(f"⏳ Skipping get_door_status({set_door}) to prevent flooding.")
            time.sleep(1)
            continue
        if not gateway_lock.acquire(blocking=False):
            log.warning(f"🚧 Get door status({set_door}) skipped because lock is busy!")
            return None, -1, None  # Возвращаем пустые данные
        try:
            if CLI is None:
                log.error("⚠️ Error: CLI not initialized")
                return None, -1, None
            log.info(f"📡 Sending a get transition request({set_door})...")
            resp = CLI.get_transition(set_door)
            state = None
            if resp and resp.payload and hasattr(resp.payload.command, "percent_open"):
                position = resp.payload.command.percent_open
                if position == 0:
                    state = "closed"
                    log.info(f"🚪Door -> {set_door} is closed")
                elif position == 100:
                    state = "open"
                    log.info(f"🚪Door -> {set_door} is open")
                else:
                    log.info(f"🚪Door -> {set_door} is {resp.payload.command.percent_open}% OPEN")
            else:
                position = -1
                log.warning(f"get_transition response has no 'percentage_open' attribute (resp: {resp})")
            log.info(f"🚪Door -> {set_door} position: {position} and state {state} to MQTT....")
            publish_to_mqtt(f"garage_door/{set_door}/position", position)
            if state:
                publish_to_mqtt(f"garage_door/{set_door}/state", state)
            return resp, position, state
        except Exception as ex:
            if DEBUG:
                traceback.print_exc()
                log.error(f"ERROR: {ex}")
            if "PORT_ERROR" in str(ex) or "Code: 10" in str(ex):
                retries += 1
                wait_time = 2
                log.warning(f"🔄 Gateway busy (Retries {retries}/{MAX_RETRIES}) - wait {wait_time} sec...")
                time.sleep(wait_time)
                continue
            if "PERMISSION_DENIED" in str(ex) or "Code: 12" in str(ex):
                reconnect_to_bisecur()
            if check_broken_pipe(str(ex).lower()):
                break
            if CLI.last_error and retries < 5:
                log.error(f"🔴 ERROR CLI error found {CLI.last_error}")  # TODO!!!
                time.sleep(.9)
                retries += 1
            else:
                break
        finally:
            gateway_lock.release()
    return None, -1, None


def track_realtime_door_position(current_pos=None, last_action=None, set_door=0):
    publish_to_mqtt(f"garage_door/{set_door}/position", current_pos)

    global LAST_DOOR_STATE
    global DO_EXIT_THREAD

    state = ""
    last_pos = None

    DO_EXIT_THREAD = False
    time.sleep(2)
    while not DO_EXIT_THREAD and ((state != "open" and last_action == "up open") or (
            state != "closed" and last_action in "down close") or current_pos != last_pos):
        time.sleep(3)
        last_pos = current_pos
        resp, current_pos, state = get_door_status(set_door)
        if resp is None:
            DO_EXIT_THREAD = True
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
            LAST_DOOR_STATE = state
            publish_to_mqtt(f"garage_door/{set_door}/position", current_pos)
            publish_to_mqtt(f"garage_door/{set_door}/state", state)
    LAST_DOOR_STATE = state
    return LAST_DOOR_STATE


def do_door_action(action, set_door):
    global LAST_DOOR_STATE
    value = None
    if action == "stop":
        if LAST_DOOR_STATE == "opening":
            action = "down"
        elif LAST_DOOR_STATE == "closing":
            action = "up"
        else:
            log_msg = f"Ignoring 'stop' command as current door movement direction unknown (LAST_DOOR_STATE is '{LAST_DOOR_STATE}')"
            log.warning(log_msg)
            return log_msg
    if action in "impulse up down partial light stop":
        try:
            port = int(set_door)
            mcp_cmd = MCPSetState.construct(port)
            action_resp = CLI.generic(mcp_cmd, False)
            if not check_mcp_error(action_resp):
                current_pos = action_resp.payload.command.percent_open if hasattr(action_resp.payload.command,
                                                                                  "percent_open") else -1
                global POS_TRACKING_THREAD
                if POS_TRACKING_THREAD and POS_TRACKING_THREAD.is_alive():
                    global DO_EXIT_THREAD
                    DO_EXIT_THREAD = True
                    time.sleep(0.3)
                    counter = 0
                    log.debug(f"...Active thread count: {threading.activeCount()} ")
                    while POS_TRACKING_THREAD.is_alive() and counter < 15:
                        log.debug(
                            f"\t----> WAITING .... POS_TRACKING_THREAD.is_alive: {POS_TRACKING_THREAD.is_alive()}")
                        time.sleep(.7)
                        counter += 0.5
                if POS_TRACKING_THREAD: log.debug(
                    f"----> POS_TRACKING_THREAD.is_alive: {POS_TRACKING_THREAD.is_alive()}")
                POS_TRACKING_THREAD = threading.Thread(
                    name='pos_tracking_thread',
                    target=track_realtime_door_position,
                    args=(current_pos, action, set_door)
                )
                POS_TRACKING_THREAD.start()
                log.debug(f"New thread spawned for 'do_command': {POS_TRACKING_THREAD}")
            return action_resp
        except Exception as ex:
            log.error(f"ERROR: {ex}")
            log.error(f"action: {action}, port: {port}, value: {value}")
            check_broken_pipe(str(ex).lower())
            return None
    else:
        log.error(f"Port '{action}_port' is not defined in")
        return None


def do_gw_login():
    bisecur_user = args.bisecur_user
    bisecur_pw = args.bisecur_pw
    log.debug(f"✅Logging in to Bisecur Gateway as user '{bisecur_user}'")
    CLI.login(bisecur_user, bisecur_pw)
    if CLI.token:
        log.info(f"✅ User '{bisecur_user}' logged in to Bisecur Gateway with token '{CLI.token}'")
        return CLI.token
    else:
        log.warning(f"🔴Bisecur Gateway login failed for user '{bisecur_user}'. Exiting...")
        return None


def check_mcp_error(resp):
    if CLI.last_error:  # TODO!!! Tidy up...
        log.error(f"--- 1. CLI.last_error: {CLI.last_error}")
        if hasattr(resp, "payload") and hasattr(resp.payload, "command") and hasattr(resp.payload.command,
                                                                                     "error_code"):
            error_obj = {"error_code": resp.payload.command.error_code.value,
                         "error": resp.payload.command.error_code.name}
        else:
            error_obj = {"error_code": "Unknown", "error": str(resp) if resp else "Unknown error"}
        publish_to_mqtt(f"{MQTT_COMMAND_SUBTOPIC}/error", json.dumps(error_obj))

    elif resp and hasattr(resp, "resp.payload") and hasattr(resp,
                                                            "resp.payload.command_id") and resp.payload.command_id == 1 and hasattr(
        resp.payload.command, "error_code"):
        log.error(
            f"MCP error '{resp.payload.command.error_code.value}' occurred (code: {resp.payload.command.error_code.name}) ")
        # Error 12 is Permission Denied
        log.error(f"--- 2. CLI.last_error: {CLI.last_error}")
        error_obj = {"error_code": resp.payload.command.error_code.value, "error": resp.payload.command.error_code.name}
        publish_to_mqtt(f"{MQTT_COMMAND_SUBTOPIC}/error", json.dumps(error_obj))
        return error_obj

    else:
        publish_to_mqtt(f"{MQTT_COMMAND_SUBTOPIC}/error", "")
        return None


def on_message(mosq, msg):
    log.info(f"---> Topic '{msg.topic}' received command '{msg.payload.decode('utf-8')}'")
    cmd = msg.payload.decode('utf-8')
    parts = cmd.split("_")
    if re.match(r"^[a-zA-Z]+_\d+$", cmd):
        if int(parts[1]) in DOORS_PORT:
            log.info(f"Door: {parts[1]} and Command: {parts[0]}")
            do_command(parts[0], parts[1])
        else:
            log.info(f"Door: {parts[1]} not inside: {parts[0]}")
    else:
        log.warning(f"Received invalid command format: {cmd}")


def on_connect(mosq, flags, result_code):
    sub_topic = f"{MQTT_TOPIC_BASE}/{MQTT_COMMAND_SUBTOPIC}/command"
    log.info(f"📡 Connected to MQTT broker. Subscribing to '{sub_topic}'")
    if MQTT_CLIENT_SUB is not None:
        MQTT_CLIENT_SUB.subscribe(sub_topic, MQTT_QOS)
        clear_command_topic()
        log.info(f"📡 Set state online")
        for set_door in DOORS_PORT:
            publish_to_mqtt(f"{set_door}/state", "online")
            init_ha_discovery(set_door)
    else:
        log.error("❌ ERROR: MQTT_CLIENT_SUB is None! Cannot subscribe!")
        return


def on_disconnect(mosq, rc):
    log.info(f"📡 MQTT session disconnected (rc={rc})!!!")
    clear_command_topic()
    for set_door in DOORS_PORT:
        publish_to_mqtt(f"{set_door}/state", "offline")
        log.info(f"Performing actions for port: {set_door}")
    time.sleep(10)


def clear_command_topic():
    log.info("📡 Clearing MQTT command topic...")
    MQTT_CLIENT_PUB.publish(f"{MQTT_TOPIC_BASE}/{MQTT_COMMAND_SUBTOPIC}/command", "", qos=MQTT_QOS, retain=True)


def restart_script():
    log.error("MCPError.PERMISSION_DENIED detected. Restarting script...")
    python = sys.executable
    os.execl(python, python, *sys.argv)


def reconnect_to_bisecur():
    global CLI
    log.warning("⚠️ PERMISSION_DENIED error! Reconnecting to Bisecur Gateway...")
    try:
        if CLI:
            CLI.logout()
            log.info("🔌 Logged out from Bisecur Gateway")
        time.sleep(3)
        CLI = init_bisecur_gw()
        if CLI:
            log.info("✅ Successfully reconnected to Bisecur Gateway!")
        else:
            log.error("❌ Failed to reconnect to Bisecur Gateway!")
    except Exception as e:
        log.error(f"❌ Error while reconnecting to Bisecur Gateway: {e}")
        traceback.print_exc()


def init_ha_discovery(set_door):
    payload = {"door_commands_list": ["impulse", "up", "down", "partial", "stop", "light"],
               "json_attributes_topic": f"{MQTT_TOPIC_BASE}/{set_door}/attributes",
               "name": "Bisecur Gateway: Garage Door",
               "schema": "state",
               "supported_features": ["impulse", "up", "down", "partial", "stop", "light", "get_door_state",
                                      "get_ports", "login", "sys_reset"],
               "availability_topic": f"{MQTT_TOPIC_BASE}/{set_door}/state", "payload_available": "online",
               "payload_not_available": "offline", "unique_id": "bs_garage_door", "device_class": "garage",
               "payload_close": "down", "payload_open": "up", "payload_stop": "impulse", "position_open": 100.0,
               "position_closed": 0.0, "position_topic": f"{MQTT_TOPIC_BASE}/{set_door}/garage_door/position",
               "state_topic": f"{MQTT_TOPIC_BASE}/{set_door}/garage_door/state",
               "command_topic": f"{MQTT_TOPIC_BASE}/{MQTT_COMMAND_SUBTOPIC}/command"}
    bisecur_mac = args.bisecur_mac.replace(':', '')
    bisecur_ip = args.bisecur_ip
    payload["connections"] = ["mac", bisecur_mac, "ip", bisecur_ip]
    payload["sw_version"] = VERSION
    _, payload["gw_hw_version"] = get_gw_version()

    mqtt_topic_HA_discovery = args.mqtt_topic_HA_discovery
    publish_to_mqtt(f"cover/bisecur/{set_door}/config", json.dumps(payload), f"{mqtt_topic_HA_discovery}")
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


def periodic_door_status_check():
    while True:
        if not IS_ACTIVE_TASK.is_set():  # Проверяем, выполняется ли команда
            for set_door in DOORS_PORT:
                log.info(f"🔄 Gate status survey -> {set_door}")
                resp, position, state = get_door_status(set_door)
                if resp is None:
                    log.warning(f"⚠️ Failed to get gate state {set_door}")

            timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            publish_to_mqtt("status/last_heartbeat", timestamp)
        else:
            log.info("⏳ Skipping gate status check: command in progress.")

        time.sleep(CHECK_INTERVAL)


def main():
    global MQTT_CLIENT_SUB, MQTT_CLIENT_PUB

    clientid = args.mqtt_clientid if args.mqtt_clientid else f"biscure2mqtt-{os.getpid()}"
    MQTT_CLIENT_SUB = paho.Client(f"{clientid}_sub", clean_session=True)
    MQTT_CLIENT_PUB = paho.Client(f"{clientid}_pub", clean_session=False)

    for set_door in DOORS_PORT:
        MQTT_CLIENT_SUB.will_set(f"{MQTT_TOPIC_BASE}/{set_door}/state", "offline", qos=0)

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
        for set_door in DOORS_PORT:
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
        except Exception as e:
            log.error(f"❌ loop_forever() crashed: {e}")
            traceback.print_exc()
        except socket.error:
            print("... doing sleep(5)")
            time.sleep(5)
        except KeyboardInterrupt:
            log.info("Shutting down connections")
        finally:
            log.info("Exiting system. Changing MQTT state to 'offline'")
            for set_door in DOORS_PORT:
                MQTT_CLIENT_PUB.publish(f"{MQTT_TOPIC_BASE}/{set_door}/state", "offline")
            MQTT_CLIENT_SUB.loop_stop()
            if CLI:
                if hasattr(CLI,
                           "last_error") and CLI.last_error is not None and "value" in CLI.last_error and CLI.last_error.value == 12:
                    log.info(f"Logging out of Bisecur Gateway ({CLI.token})")
                    CLI.logout()
                elif hasattr(CLI, "last_error"):
                    log.debug(f"Pre-logout: Biscure last error: ({CLI.last_error})")
            log.info(f"Active threads: {threading.activeCount()}")
            log.debug("Tidying up spawned threads...")
            main_thread = threading.currentThread()
            for t in threading.enumerate():
                if t is main_thread:
                    log.info(f"... ignoring main_thread '{main_thread.getName()}'")
                    continue
                log.info(f"...joining spawned thread '{t.getName()}'")
                t.join()
            log.info("Done!")


if __name__ == '__main__':
    main()
