import logging
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAIN = "bisecur2mqtt"


def start_bisecur_service():
    """Запускает bisecur2mqtt"""
    try:
        from .bisecur2mqtt import main  # Импортируем main() только при запуске
        _LOGGER.info("Запуск bisecur2mqtt.py в отдельном потоке...")
        main()  # Вызываем main() без asyncio
    except Exception as e:
        _LOGGER.error(f"Ошибка запуска bisecur2mqtt: {e}")


async def async_setup(hass: HomeAssistant, config: dict):
    """Настройка интеграции"""
    _LOGGER.info("Настройка интеграции Bisecur2MQTT...")
    hass.async_add_executor_job(start_bisecur_service)  # Запуск bisecur2mqtt в фоне
    return True
