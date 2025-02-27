import voluptuous as vol
from homeassistant import config_entries
from .const import DOMAIN

class BisecurConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Настройка Bisecur2MQTT в UI."""

    async def async_step_user(self, user_input=None):
        """Первый шаг конфигурации."""
        if user_input is not None:
            return self.async_create_entry(title="Bisecur2MQTT", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional("watchdog", default=True): bool,
                }
            ),
        )