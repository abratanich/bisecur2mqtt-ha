# Bisecur2MQTT Home Assistant Addon

This addon allows you to run bisecur2mqtt as a background service in Home Assistant.
[configuration.yaml](custom_components%2Fbisecur2mqtt%2Fconfiguration.yaml)
```
mqtt:
  cover:
    - name: "Main door"
      unique_id: "bisecur_main_door"
      device_class: "garage"
      availability:
        - topic: "bisecur2mqtt/garage_door/0/state"
          payload_available: "online"
          payload_not_available: "offline"
      position_topic: "bisecur2mqtt/garage_door/0/position"
      position_template: "{{ value | round(0) }}"
      position_open: 100
      position_closed: 0
      set_position_topic: "bisecur2mqtt/garage_door/0/position"
      command_topic: "bisecur2mqtt/send_command/command"
      payload_open: "open_0"
      payload_close: "open_0"
      payload_stop: "open_0"
      state_topic: "bisecur2mqtt/garage_door/0/state"
      value_template: "{{ value }}"
      state_open: "open"
      state_closed: "closed"
      device:
        identifiers:
          - "Hörmann Bisecur Gateway"
        manufacturer: "Hörmann"
        model: "Bisecur Gateway"
        sw_version: "2.50.0"

    - name: "Garage door"
      unique_id: "bisecur_garage_door"
      device_class: "garage"
      availability:
        - topic: "bisecur2mqtt/garage_door/1/state"
          payload_available: "online"
          payload_not_available: "offline"
      position_topic: "bisecur2mqtt/garage_door/1/position"
      position_template: "{{ value | round(0) }}"
      position_open: 100
      position_closed: 0
      set_position_topic: "bisecur2mqtt/garage_door/1/position"
      command_topic: "bisecur2mqtt/send_command/command"
      payload_open: "open_1"
      payload_close: "open_1"
      payload_stop: "open_1"
      state_topic: "bisecur2mqtt/garage_door/1/state"
      value_template: "{{ value }}"
      state_open: "open"
      state_closed: "closed"
      device:
        identifiers:
          - "Hörmann Bisecur Gateway"
        manufacturer: "Hörmann"
        model: "Bisecur Gateway"
        sw_version: "2.50.0"
bisecur2mqtt:
  bisecur_user: "hauser"
  bisecur_pw: "pass"
  bisecur_ip: "192.168.68.19"
  bisecur_mac: "FF:1F:12:14:1E:FF"
  mqtt_username: "bisecur"
  mqtt_password: "bisecur"
  logs: true
```
```
mosquitto_sub -h 192.168.68.7 -u "bisecur" -P "bisecur" -t "bisecur2mqtt/#" -v
mosquitto_pub -h 192.168.68.10 -p 1883 -u "bisecur" -P "bisecur" -t bisecur2mqtt/send_command/command -m "open_0"
```