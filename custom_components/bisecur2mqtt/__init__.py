import logging
import subprocess
import os
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAIN = "bisecur2mqtt"


def start_bisecur_service(config):
    """Запускает bisecur2mqtt с параметрами из configuration.yaml"""
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bisecur2mqtt.py")

    _LOGGER.warning(f"Bisecur2MQTT: Запускаем bisecur2mqtt.py в фоне: {script_path}")

    try:
        # Передаём настройки в bisecur2mqtt.py через аргументы командной строки
        args = [
            "python3", script_path,
            "--bisecur_user", config.get("bisecur_user", ""),
            "--bisecur_pw", config.get("bisecur_pw", ""),
            "--bisecur_ip", config.get("bisecur_ip", ""),
            "--bisecur_mac", config.get("bisecur_mac", "FF:FF:FF:FF:FF:FF"),
            "--src_mac", config.get("src_mac", "FF:FF:FF:FF:FF:FF"),
            "--mqtt_broker", config.get("mqtt_broker", "localhost"),
            "--mqtt_port", config.get("mqtt_port", 1883),
            "--mqtt_clientid", config.get("mqtt_clientid", "mqtt2bisecur"),
            "--mqtt_username", config.get("mqtt_username", "bisecur"),
            "--mqtt_password", config.get("mqtt_password", "bisecur"),
            "--mqtt_tls", config.get("mqtt_tls", None),
            "--mqtt_topic_base", config.get("mqtt_topic_base", "bisecur2mqtt"),
            "--mqtt_topic_HA_discovery", config.get("mqtt_topic_HA_discovery", "homeassistant"),
            "--logfile", config.get("logfile", "mqtt2bisecur.log"),
            "--logs", config.get("logs", False)
        ]

        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _LOGGER.warning(f"Bisecur2MQTT: bisecur2mqtt.py запущен с PID {process.pid}")

    except Exception as e:
        _LOGGER.error(f"Ошибка запуска bisecur2mqtt.py: {e}")


async def async_setup(hass: HomeAssistant, config: dict):
    """Настройка интеграции"""
    _LOGGER.warning("Bisecur2MQTT: async_setup() запущен...")

    if DOMAIN not in config:
        _LOGGER.error("Bisecur2MQTT: Отсутствует конфигурация в configuration.yaml!")
        return False

    hass.async_add_executor_job(start_bisecur_service, config[DOMAIN])  # Запускаем bisecur2mqtt в фоне
    return True