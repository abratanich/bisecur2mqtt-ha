import logging
import asyncio
import os
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

DOMAIN = "bisecur2mqtt"
WATCHDOG_INTERVAL = 30

service_task = None
watchdog_enabled = True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    global watchdog_enabled
    watchdog_enabled = entry.data.get("watchdog", True)
    hass.data[DOMAIN] = {}
    await start_bisecur_service()
    if watchdog_enabled:
        async_track_time_interval(hass, check_service_status, WATCHDOG_INTERVAL)
    hass.services.async_register(DOMAIN, "start", start_bisecur_service)
    hass.services.async_register(DOMAIN, "stop", stop_bisecur_service)
    hass.services.async_register(DOMAIN, "restart", restart_bisecur_service)
    _LOGGER.info("Bisecur2MQTT интеграция загружена.")
    return True

async def start_bisecur_service(_=None):
    """Запускаем bisecur2mqtt.py"""
    global service_task
    if service_task and not service_task.done():
        _LOGGER.warning("Bisecur2MQTT уже запущен!")
        return
    _LOGGER.info("Запуск Bisecur2MQTT...")
    loop = asyncio.get_event_loop()
    service_task = loop.run_in_executor(None, os.system, "python3 /config/bisecur2mqtt/bisecur2mqtt.py")

async def stop_bisecur_service(_=None):
    global service_task
    if service_task:
        service_task.cancel()
        _LOGGER.info("Bisecur2MQTT остановлен.")
        service_task = None

async def restart_bisecur_service(_=None):
    await stop_bisecur_service()
    await start_bisecur_service()

async def check_service_status(_):
    global service_task
    if not watchdog_enabled:
        return
    if service_task is None or service_task.done():
        _LOGGER.warning("Bisecur2MQTT не работает! Перезапуск...")
        await start_bisecur_service()