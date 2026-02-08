# Bisecur2MQTT Home Assistant Addon

Аддон для интеграции Hörmann Bisecur Gateway с Home Assistant через MQTT.

## Конфигурация Home Assistant

Добавить в `configuration.yaml`:

```yaml
mqtt:
  cover:
    # Главная дверь (порт 0)
    - name: "Главная дверь"
      unique_id: "bisecur_main_door_0"
      device_class: "garage"

      # Топики (правильный формат)
      command_topic: "bisecur2mqtt/send_command/command"
      state_topic: "bisecur2mqtt/garage_door/0/state"
      position_topic: "bisecur2mqtt/garage_door/0/position"
      availability_topic: "bisecur2mqtt/0/state"

      # Команды (умные — проверяют позицию)
      payload_open: "open_0"
      payload_close: "close_0"
      payload_stop: "stop_0"
      payload_available: "online"
      payload_not_available: "offline"

      # Позиция
      position_open: 100
      position_closed: 0
      position_template: "{{ value | round(0) }}"

      # Состояние
      value_template: "{{ value }}"
      state_open: "open"
      state_closed: "closed"
      optimistic: false

      device:
        identifiers:
          - "bisecur_gateway"
        name: "Hörmann Bisecur Gateway"
        manufacturer: "Hörmann"
        model: "Bisecur Gateway"

    # Гаражные ворота (порт 1)
    - name: "Гаражные ворота"
      unique_id: "bisecur_garage_door_1"
      device_class: "garage"

      # Топики
      command_topic: "bisecur2mqtt/send_command/command"
      state_topic: "bisecur2mqtt/garage_door/1/state"
      position_topic: "bisecur2mqtt/garage_door/1/position"
      availability_topic: "bisecur2mqtt/1/state"

      payload_open: "open_1"
      payload_close: "close_1"
      payload_stop: "stop_1"
      payload_available: "online"
      payload_not_available: "offline"

      position_open: 100
      position_closed: 0
      position_template: "{{ value | round(0) }}"

      value_template: "{{ value }}"
      state_open: "open"
      state_closed: "closed"
      optimistic: false

      device:
        identifiers:
          - "bisecur_gateway"
        name: "Hörmann Bisecur Gateway"
        manufacturer: "Hörmann"
        model: "Bisecur Gateway"

bisecur2mqtt:
  bisecur_user: "hauser"
  bisecur_pw: "ваш_пароль"
  bisecur_ip: "192.168.1.19"
  bisecur_mac: "FF:FF:FF:FF:FF:FF"
  mqtt_username: "bisecur"
  mqtt_password: "ваш_mqtt_пароль"
  logs: false
  doors_port: [0, 1]
  poll_interval: 30
  poll_max_retries: 2
```

## Доступные команды

| Команда | Формат | Описание |
|---------|--------|----------|
| Умное открытие | `open_X` | Открывает если закрыто |
| Умное закрытие | `close_X` | Закрывает если открыто |
| Стоп | `stop_X` | Останавливает движение |
| Импульс | `impulse_X` | Переключает (как кнопка) |
| Принудительно открыть | `force_open_X` | Открывает без проверки |
| Принудительно закрыть | `force_close_X` | Закрывает без проверки |
| Позиция | `position_Y_X` | Открывает на Y% |

Где `X` = номер двери (0, 1, 2...), `Y` = процент (0-100)

## Тестирование через MQTT

```bash
# Подписаться на все топики
mosquitto_sub -h 192.168.68.7 -u "bisecur" -P "пароль" -t "bisecur2mqtt/#" -v

# Открыть дверь 1
mosquitto_pub -h 192.168.68.7 -u "bisecur" -P "пароль" -t "bisecur2mqtt/send_command/command" -m "open_1"

# Закрыть дверь 1
mosquitto_pub -h 192.168.68.7 -u "bisecur" -P "пароль" -t "bisecur2mqtt/send_command/command" -m "close_1"

# Установить позицию 50%
mosquitto_pub -h 192.168.68.7 -u "bisecur" -P "пароль" -t "bisecur2mqtt/send_command/command" -m "position_50_1"

# Очистить топик команд
mosquitto_pub -h 192.168.68.7 -u "bisecur" -P "пароль" -t "bisecur2mqtt/send_command/command" -n -r
```

## Топики MQTT

| Топик | Описание |
|-------|----------|
| `bisecur2mqtt/garage_door/{порт}/state` | Состояние (open/closed/opening/closing) |
| `bisecur2mqtt/garage_door/{порт}/position` | Позиция (0-100%) |
| `bisecur2mqtt/{порт}/state` | Доступность (online/offline) |
| `bisecur2mqtt/status/gateway_status` | Статус шлюза |
| `bisecur2mqtt/status/last_heartbeat` | Последний heartbeat |
| `bisecur2mqtt/send_command/command` | Топик для команд |
| `bisecur2mqtt/command/status` | Статус выполнения команды |
