import socket
import logging

from libs.pysecur3.MCP import *


class MCPClient:
    def __init__(self, ip, port, src_mac, dst_mac):
        self.gw_ip = ip
        self.gw_port = port
        self.src_mac = src_mac
        self.dst_mac = dst_mac

        self.soc = None
        self.socbuff = b''

        self.tag = 0
        self.token = 0

        self.last_error = None

    def load_login(self, token, tag=0):
        self.tag = tag
        self.token = token

    def construct_packet(self, cmd):
        payload = MCP.construct(cmd, tag=self.tag, token=self.token)
        packet = MCPPacket.construct(self.src_mac, self.dst_mac, payload)
        return packet.to_bytes()

    def connect(self):
        logging.debug('Connecting to %s:%d' % (self.gw_ip, self.gw_port))
        self.soc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.soc.settimeout(5)
        self.soc.connect((self.gw_ip, self.gw_port))

    # def recv_cmd(self, throw=True):
    #     self.last_error = None
    #     logging.debug('Switched to receive mode: Awaiting previous command response packets')
    #     buff = b''
    #     total_len = None
    #     timeout_count = 0
    #     MAX_TIMEOUT_COUNT = 5
    #     while timeout_count < MAX_TIMEOUT_COUNT:
    #         try:
    #             if not total_len:
    #                 if len(self.socbuff) >= 28:
    #                     total_len = int.from_bytes(bytes.fromhex(self.socbuff[:28].decode())[12:14],
    #                                                byteorder='big', signed=False)
    #                     total_len = (total_len * 2) + 13 * 2
    #             if total_len and len(self.socbuff) >= total_len:
    #                 buff = self.socbuff[:total_len]
    #                 self.socbuff = self.socbuff[total_len:]
    #                 break
    #             temp = self.soc.recv(4096)
    #             if not temp:
    #                 break
    #             self.socbuff += temp
    #         except socket.error as e:
    #             timeout_count += 1
    #             logging.warn(f"MCPClient.recv_cmd() socket error ({timeout_count}/{MAX_TIMEOUT_COUNT}): {e}")
    #             self.last_error = {"value": f"Socket timeout ({timeout_count}/{MAX_TIMEOUT_COUNT})", "code": -1}
    #     if not buff:
    #         logging.error("No response received from device")
    #         return None
    #     logging.debug('Data received: %s' % buff)
    #     try:
    #         response_packet = MCPPacket.from_bytes(buff)
    #     except Exception as e:
    #         logging.error("Error decoding MCPPacket: %s", e)
    #         return None
    #     if throw == True and response_packet.payload.command_id == 1:
    #         self.last_error = response_packet.payload.command.error_code
    #         raise Exception('Device responded with error! Code: %d Reason: %s' % (
    #             response_packet.payload.command.error_code.value, response_packet.payload.command.error_code.name))
    #     return response_packet

    def recv_cmd(self,throw=True):
    	self.last_error = None
    	logging.debug('Switched to receive mode: Awaiting previous command response packets')
    	buff = b''
    	total_len = None
    	timeout_count = 0
    	MAX_TIMEOUT_COUNT =5
    	while True and timeout_count < MAX_TIMEOUT_COUNT:
    		try:
    			if not total_len:
    				if len(self.socbuff) >= 28:
    					total_len = int.from_bytes(bytes.fromhex(self.socbuff[:28].decode())[12:14], byteorder = 'big', signed = False)
    					total_len = (total_len * 2) + 13*2 # because it's hex encoded on the wire + 1 byte CRC + 2 times MAC addr hex encoded

    			if total_len:
    				if len(self.socbuff) >= total_len:
    					buff = self.socbuff[:total_len]
    					self.socbuff = self.socbuff[total_len:]
    					break

    			temp = self.soc.recv(4096)
    			if not temp:
    				break

    			self.socbuff += temp
    		except socket.error as e:
    			timeout_count += 1
    			logging.warn(f"MCPClient.recv_cmd() socket error ({timeout_count}/{MAX_TIMEOUT_COUNT}): {e}")
    			self.last_error = {"value": f"Socket timeout ({timeout_count}/{MAX_TIMEOUT_COUNT})", "code": -1}
    		except Exception as e:
    			logging.error(f"client.py exception (line 60): {e}")


    	logging.debug('Data received: %s' % buff)
    	logging.debug('Data received: %s' % bytes.fromhex(buff.decode()))
    	response_packet = MCPPacket.from_bytes(buff)

    	if throw == True and response_packet.payload.command_id == 1:
    		self.last_error = response_packet.payload.command.error_code
    		raise Exception('Device responded with error! Code: %d Reason: %s' % (response_packet.payload.command.error_code.value, response_packet.payload.command.error_code.name))

    	return response_packet

    def sr(self, cmd, throw=True):
        if not self.soc:
            self.connect()
        self.last_error = None
        packet_bytes = self.construct_packet(cmd)
        logging.debug('Sending bytes: %s' % packet_bytes)
        self.soc.sendall(packet_bytes)
        return self.recv_cmd(throw)

    def login(self, username, password):
        logging.debug('Login called!')
        logging.debug('Crafing packet')
        cmd = MCPLogin.construct(username, password)

        resp = self.sr(cmd)
        logging.debug(resp)

        if isinstance(resp.payload.command, MCPLoginResponse):
            self.token = resp.payload.command.auth_token
            self.tag = resp.payload.command.auth_tag

        elif isinstance(resp.payload.command, MCPLogout):
            resp = self.recv_cmd()
            self.token = resp.payload.command.auth_token
            self.tag = resp.payload.command.auth_tag

        return {"token": self.token, "tag": self.tag}

    def get_user_rights(self):
        logging.debug('get_user_rights')
        cmd = MCPGetUserRights.construct()

        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def jcmp(self, request, throw_errors=True):
        """
		request needs to be a dict
		"""
        logging.debug('jcmp')
        cmd = JCMP.construct(request)
        resp = self.sr(cmd, throw_errors)
        logging.debug(resp)
        return resp

    def get_wifi_state(self):
        logging.debug('get_wifi_state')
        cmd = MCPGetWifiState.construct()

        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def scan_wifi(self):
        logging.debug('scan_wifi')
        cmd = MCPScanWifi.construct()

        resp = self.sr(cmd)
        logging.debug(resp)

        while resp.payload.payload != b'':
            resp = self.recv_cmd()
            logging.debug(resp)
        return resp

    def wifi_found(self, data):
        logging.debug('wifi_found')
        cmd = MCPWifiFound.construct(data)

        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def get_gw_version(self, throw_errors=True):
        logging.debug('get_gw_version')
        cmd = MCPGETGWVersion.construct()

        resp = self.sr(cmd, throw_errors)
        logging.debug(resp)
        return resp

    def generic(self, cmd, throw_errors=True):
        logging.debug('generic')
        resp = self.sr(cmd, throw_errors)
        logging.debug(resp)
        return resp

    def logout(self):
        logging.debug('logout')
        cmd = MCPLogout.construct()

        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def get_user_name(self, user_id):
        logging.debug('get_user_name')

        cmd = MCPGetUserName.construct(user_id)

        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def add_user(self, username, password, owerflow=None):
        logging.debug('add_user')
        cmd = MCPAddUser.construct(username, password, owerflow)

        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def change_password_of_user(self, user_id, newpassword):
        logging.debug('change_password_of_user')
        cmd = MCPChangePasswordOfUser.construct(user_id, newpassword)
        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def change_password(self, newpassword):
        logging.debug('change_password')
        cmd = MCPChangePassword.construct(newpassword)
        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def set_name(self, name):
        logging.debug('set_name')
        cmd = MCPSetName.construct(name)
        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def debug(self, data):
        logging.debug('debug')
        cmd = MCPDebug.construct(data)
        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def get_mac(self):
        logging.debug('get_mac')
        cmd = MCPGetMAC.construct()
        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def ping(self):
        logging.debug('ping')
        cmd = MCPPing.construct()
        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def set_user_rights(self, user_id, user_rights):
        logging.debug('set_user_rights')
        cmd = MCPSetUserRights.construct(user_id, user_rights)
        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def get_user_ids(self):
        logging.debug('get_user_ids')
        cmd = MCPGetUserIds.construct()
        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def remove_user(self, user_id):
        logging.debug('remove_user')
        cmd = MCPRemoveUser.construct(user_id)
        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    def get_transition(self, port_id):
        logging.debug('get_transition')
        cmd = MCPGetTransition.construct(port_id)
        resp = self.sr(cmd)
        logging.debug(resp)
        return resp

    @staticmethod
    def discover_devices(self):
        disc = MCPDiscover()
        disc.run()

        if len(disc.devices) > 0:
            for ip in disc.devices:
                print('Found device of version %s on address %s' % (disc.devices[ip], ip))
        return disc.devices
