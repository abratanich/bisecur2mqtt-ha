from homeassistant.components.update import UpdateEntity

class BiSecurUpdate(UpdateEntity):
    def __init__(self, hass):
        self._attr_name = "BiSecur2MQTT Update"
        self._attr_unique_id = "bisecur2mqtt_update"
        self._attr_installed_version = "1.0"
        self._attr_latest_version = "1.0"

    def update(self):
        self._attr_latest_version = "1.0"