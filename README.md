# Bisecur2MQTT Home Assistant Addon

This addon allows you to run bisecur2mqtt as a background service in Home Assistant.
[configuration.yaml](custom_components%2Fbisecur2mqtt%2Fconfiguration.yaml)
```
bisecur2mqtt:
  bisecur_user: "hauser"
  bisecur_pw: "pass"
  bisecur_ip: "192.168.68.19"
  bisecur_mac: "80:1F:12:14:1E:FF"
  mqtt_username: "bisecur"
  mqtt_password: "pass"
  logs: false

mqtt:
  cover:
    - name: "Main door"
      unique_id: "bisecur_main_door"
      device_class: "garage"
      availability:
        - topic: "bisecur2mqtt/garage_door/0/state"
          payload_available: "online"
          payload_not_available: "offline"
      value_template: "{{ value_json.position }}"
      position_topic: "bisecur2mqtt/garage_door/0/position"
      position_template: "{{ value_json.value | round(0) }}"
      position_open: 100
      position_closed: 0
      set_position_topic: "bisecur2mqtt/garage_door/0/position"
      command_topic: "bisecur2mqtt/garage_door/commands"
      payload_open: "open_0"
      payload_close: "open_0"
      payload_stop: "open_0"
      state_topic: "bisecur2mqtt/garage_door/0/state"
      state_open: "open"
      state_closed: "closed"
      json_attributes_topic: "bisecur2mqtt/homeassistant/cover/bisecur_0/config"
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
      value_template: "{{ value_json.position }}"
      position_topic: "bisecur2mqtt/garage_door/1/position"
      position_template: "{{ value_json.value | round(0) }}"
      position_open: 100
      position_closed: 0
      set_position_topic: "bisecur2mqtt/garage_door/1/position"
      command_topic: "bisecur2mqtt/garage_door/commands"
      payload_open: "open_1"
      payload_close: "open_1"
      payload_stop: "open_1"
      state_topic: "bisecur2mqtt/garage_door/1/state"
      state_open: "open"
      state_closed: "closed"
      json_attributes_topic: "bisecur2mqtt/homeassistant/cover/bisecur_1/config"
      device:
        identifiers:
          - "Hörmann Bisecur Gateway"
        manufacturer: "Hörmann"
        model: "Bisecur Gateway"
        sw_version: "2.50.0"
```
