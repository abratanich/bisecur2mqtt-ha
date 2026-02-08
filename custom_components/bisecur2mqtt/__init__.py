import logging
import subprocess
import os
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAIN = "bisecur2mqtt"


def start_bisecur_service(config):
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bisecur2mqtt.py")

    _LOGGER.info(f"Bisecur2MQTT: Running bisecur2mqtt.py in pid: {script_path}")

    try:
        args = [
                   "python3", script_path,
                   "--bisecur_user", str(config.get("bisecur_user", "")),
                   "--bisecur_pw", str(config.get("bisecur_pw", "")),
                   "--bisecur_ip", str(config.get("bisecur_ip", "")),
                   "--bisecur_mac", str(config.get("bisecur_mac", "FF:FF:FF:FF:FF:FF")),
                   "--src_mac", str(config.get("src_mac", "FF:FF:FF:FF:FF:FF")),
                   "--mqtt_broker", str(config.get("mqtt_broker", "core-mosquitto")),
                   "--mqtt_port", str(config.get("mqtt_port", 1883)),
                   "--mqtt_clientid", str(config.get("mqtt_clientid", "mqtt2bisecur")),
                   "--mqtt_username", str(config.get("mqtt_username", "bisecur")),
                   "--mqtt_password", str(config.get("mqtt_password", "bisecur")),
                   "--mqtt_tls", "true" if config.get("mqtt_tls", False) else "false",
                   "--mqtt_topic_base", str(config.get("mqtt_topic_base", "bisecur2mqtt")),
                   "--mqtt_topic_HA_discovery", str(config.get("mqtt_topic_HA_discovery", "homeassistant")),
                   "--logfile", str(config.get("logfile", "/config/custom_components/bisecur2mqtt/bisecur2mqtt.log")),
                   "--logs", "true" if config.get("logs", False) else "false",
                   "--doors_port"
               ] + list(map(str, config.get("doors_port", [0])))
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _LOGGER.info(f"Bisecur2MQTT: bisecur2mqtt.py run с PID {process.pid}")
        import time
        time.sleep(3)

        if process.poll() is not None:
            stderr_output = process.stderr.read().decode()
            _LOGGER.error(f"Bisecur2MQTT: Script crashed! Error output:\n{stderr_output}")
        else:
            _LOGGER.info("Bisecur2MQTT: Script seems to be running fine.")

    except Exception as e:
        _LOGGER.error(f"Error starting bisecur2mqtt.py: {e}")


async def async_setup(hass: HomeAssistant, config: dict):
    _LOGGER.info("Bisecur2MQTT: async_setup() run...")

    if DOMAIN not in config:
        _LOGGER.error("Bisecur2MQTT: Missing configuration in configuration.yaml!")
        return False

    hass.async_add_executor_job(start_bisecur_service, config[DOMAIN])
    return True
