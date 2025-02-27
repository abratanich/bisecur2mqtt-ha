import logging
import subprocess
import os
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAIN = "bisecur2mqtt"

def start_bisecur_service():
    """Запускает bisecur2mqtt как отдельный процесс"""
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bisecur2mqtt.py")
    _LOGGER.warning(f"Bisecur2MQTT: Запускаем bisecur2mqtt.py в фоне: {script_path}")

    try:
        process = subprocess.Popen(["python3", script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _LOGGER.warning(f"Bisecur2MQTT: bisecur2mqtt.py запущен с PID {process.pid}")
    except Exception as e:
        _LOGGER.error(f"Ошибка запуска bisecur2mqtt.py: {e}")

async def async_setup(hass: HomeAssistant, config: dict):
    """Настройка интеграции"""
    _LOGGER.warning("Bisecur2MQTT: async_setup() запущен...")
    hass.async_add_executor_job(start_bisecur_service)  # Запускаем bisecur2mqtt в фоне
    return True