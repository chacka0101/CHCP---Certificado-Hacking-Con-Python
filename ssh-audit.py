#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
   The MIT License (MIT)
   
   Copyright (C) 2016 Andris Raugulis (moo@arthepsy.eu)
   
   Permission is hereby granted, free of charge, to any person obtaining a copy
   of this software and associated documentation files (the "Software"), to deal
   in the Software without restriction, including without limitation the rights
   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
   copies of the Software, and to permit persons to whom the Software is
   furnished to do so, subject to the following conditions:
   
   The above copyright notice and this permission notice shall be included in
   all copies or substantial portions of the Software.
   
   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
   THE SOFTWARE.
"""
from __future__ import print_function
import os, io, sys, socket, struct, random, errno

SSH_BANNER = 'SSH-2.0-OpenSSH_7.3'

def usage():
	p = os.path.basename(sys.argv[0])
	out.head('# {0} v1.0.20160812, moo@arthepsy.eu'.format(p))
	out.info('\nusage: {} [-nv] host[:port]\n'.format(p))
	out.info('   -v  verbose')
	out.info('   -n  disable colors' + os.linesep)
	sys.exit(1)

class Output(object):
	colors = True
	verbose = False
	
	_colors = {
		'head': 36,
		'good': 32,
		'fail': 31,
		'warn': 33,
	}
	def sep(self):
		print()
	def _colorized(self, color):
		return lambda x: print(color + x + '\033[0m')
	def __getattr__(self, name):
		if self.colors and os.name == 'posix' and name in self._colors:
			color = '\033[0;{0}m'.format(self._colors[name])
			return self._colorized(color)
		else:
			return lambda x: print(x)

class KexParty(object):
	encryption = []
	mac = []
	compression = []
	languages = []

class Kex(object):
	cookie = None
	kex_algorithms = []
	key_algorithms = []
	server = KexParty()
	client = KexParty()
	follows = False
	unused = 0
	
	@classmethod
	def parse(cls, payload):
		kex = cls()
		buf = ReadBuf(payload)
		kex.cookie = buf.read(16)
		kex.kex_algorithms = buf.read_list()
		kex.key_algorithms = buf.read_list()
		kex.client.encryption = buf.read_list()
		kex.server.encryption = buf.read_list()
		kex.client.mac = buf.read_list()
		kex.server.mac = buf.read_list()
		kex.client.compression = buf.read_list()
		kex.server.compression = buf.read_list()
		kex.client.languages = buf.read_list()
		kex.server.languages = buf.read_list()
		kex.follows = buf.read_bool()
		kex.unused = buf.read_int()
		return kex

class ReadBuf(object):
	def __init__(self, data = None):
		super(ReadBuf, self).__init__()
		self._buf = io.BytesIO(data) if data else io.BytesIO()
		self._len = len(data) if data else 0
	
	@property
	def unread_len(self):
		return self._len - self._buf.tell()
	
	def read(self, size):
		return self._buf.read(size)
	
	def read_line(self):
		return self._buf.readline().rstrip().decode('utf-8')
	
	def read_int(self):
		return struct.unpack('>I', self.read(4))[0]
	
	def read_bool(self):
		return struct.unpack('b', self.read(1))[0] != 0
	
	def read_list(self):
		list_size = self.read_int()
		return self.read(list_size).decode().split(',')

class WriteBuf(object):
	def __init__(self, data = None):
		super(WriteBuf, self).__init__()
		self._wbuf = io.BytesIO(data) if data else io.BytesIO()
	
	def write(self, data):
		self._wbuf.write(data)
	
	def write_byte(self, v):
		self.write(struct.pack('>B', v))
	
	def write_bool(self, v):
		self.write_byte(1 if v else 0)
	
	def write_int(self, v):
		self.write(struct.pack('>I', v))
	
	def write_string(self, v):
		if not isinstance(v, bytes):
			v = bytes(bytearray(v, 'utf-8'))
		n = len(v)
		self.write(struct.pack('>I', n))
		self.write(v)
	
	def write_list(self, v):
		self.write_string(','.join(v))
	
	def write_mpint(self, v):
		length = v.bit_length() // 8 + 1
		data = [(v >> i*8) & 0xff for i in reversed(range(length))]
		data = bytes(bytearray(data))
		self.write_string(data)

class SSH(object):
	MSG_KEXINIT     = 20
	MSG_NEWKEYS     = 21
	MSG_KEXDH_INIT  = 30
	MSG_KEXDH_REPLY = 32
	
	class Socket(ReadBuf, WriteBuf):
		SM_BANNER_SENT = 1
		
		def __init__(self, host, port, cto = 3.0, rto = 5.0):
			self.__block_size = 8
			self.__state = 0
			self.__header = []
			self.__banner = None
			super(SSH.Socket, self).__init__()
			try:
				self.__sock = socket.create_connection((host, port), cto)
				self.__sock.settimeout(rto)
			except Exception as e:
				out.fail('[fail] {}'.format(e))
				sys.exit(1)
		
		def __enter__(self):
			return self
		
		def get_banner(self):
			if self.__state < self.SM_BANNER_SENT:
				self.send_banner()
			while self.__banner is None:
				s, e = self.recv()
				if s < 0:
					break
				while self.__banner is None and self.unread_len > 0:
					line = self.read_line()
					if len(line.strip()) == 0:
						continue
					if line.startswith('SSH-'):
						self.__banner = line
					else:
						self.__header.append(line)
			return self.__banner, self.__header
		
		def recv(self, size = 2048):
			try:
				data = self.__sock.recv(size)
			except socket.timeout as e:
				r = 0 if e.strerror == 'timed out' else -1
				return (r, e)
			except socket.error as e:
				r = 0 if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK) else -1
				return (r, e)
			if len(data) == 0:
				return (-1, None)
			pos = self._buf.tell()
			self._buf.seek(0, 2)
			self._buf.write(data)
			self._len += len(data)
			self._buf.seek(pos, 0)
			return (len(data), None)
		
		def send(self, data):
			try:
				self.__sock.send(data)
				return (0, None)
			except socket.error as e:
				return (-1, e)
			self.__sock.send(data)
		
		def send_banner(self, banner = SSH_BANNER):
			self.send(banner.encode() + b'\r\n')
			if self.__state < self.SM_BANNER_SENT:
				self.__state = self.SM_BANNER_SENT
		
		def read_packet(self):
			while self.unread_len < self.__block_size:
				s, e = self.recv()
				if s < 0:
					return -1, e
			header = self.read(self.__block_size)
			if len(header) == 0:
				out.fail('[exception] empty ssh packet (no data)')
				sys.exit(1)
			packet_size = struct.unpack('>I', header[:4])[0]
			rest = header[4:]
			lrest = len(rest)
			padding = ord(rest[0:1])
			packet_type = ord(rest[1:2])
			if (packet_size - lrest) % self.__block_size != 0:
				out.fail('[exception] invalid ssh packet (block size)')
				sys.exit(1)
			rlen = packet_size - lrest
			while self.unread_len < rlen:
				s, e = self.recv()
				if s < 0:
					return -1, e
			buf = self.read(rlen)
			packet = rest[2:] + buf[0:packet_size - lrest]
			payload = packet[0:packet_size - padding]
			return packet_type, payload
		
		def send_packet(self):
			payload = self._wbuf.getvalue()
			self._wbuf.truncate(0)
			self._wbuf.seek(0)
			padding = -(len(payload) + 5) % 8
			if padding < 4:
				padding += 8
			plen = len(payload) + padding + 1
			pad_bytes = b'\x00' * padding
			data = struct.pack('>Ib', plen, padding) + payload + pad_bytes
			return self.send(data)
		
		def __del__(self):
			self.__cleanup()
		
		def __exit__(self, ex_type, ex_value, tb):
			self.__cleanup()
		
		def __cleanup(self):
			try:
				self.__sock.shutdown(socket.SHUT_RDWR)
				self.__sock.close()
			except:
				pass

class KexDH(object):
	def __init__(self, alg, g, p):
		self.__alg = alg
		self.__g = g
		self.__p = p
		self.__q = (self.__p - 1) // 2
		self.__x = None
	
	def send_init(self, s):
		r = random.SystemRandom()
		self.__x = r.randrange(2, self.__q)
		self.__e = pow(self.__g, self.__x, self.__p)
		s.write_byte(SSH.MSG_KEXDH_INIT)
		s.write_mpint(self.__e)
		s.send_packet()

class KexGroup1(KexDH):
	def __init__(self):
		# rfc2409: second oakley group
		p = int('ffffffffffffffffc90fdaa22168c234c4c6628b80dc1cd129024e088a67'
		        'cc74020bbea63b139b22514a08798e3404ddef9519b3cd3a431b302b0a6d'
		        'f25f14374fe1356d6d51c245e485b576625e7ec6f44c42e9a637ed6b0bff'
		        '5cb6f406b7edee386bfb5a899fa5ae9f24117c4b1fe649286651ece65381'
		        'ffffffffffffffff', 16)
		super(KexGroup1, self).__init__('sha1', 2, p)

class KexGroup14(KexDH):
	def __init__(self):
		# rfc3526: 2048-bit modp group
		p = int('ffffffffffffffffc90fdaa22168c234c4c6628b80dc1cd129024e088a67'
		        'cc74020bbea63b139b22514a08798e3404ddef9519b3cd3a431b302b0a6d'
		        'f25f14374fe1356d6d51c245e485b576625e7ec6f44c42e9a637ed6b0bff'
		        '5cb6f406b7edee386bfb5a899fa5ae9f24117c4b1fe649286651ece45b3d'
		        'c2007cb8a163bf0598da48361c55d39a69163fa8fd24cf5f83655d23dca3'
		        'ad961c62f356208552bb9ed529077096966d670c354e4abc9804f1746c08'
		        'ca18217c32905e462e36ce3be39e772c180e86039b2783a2ec07a28fb5c5'
		        '5df06f4c52c9de2bcbf6955817183995497cea956ae515d2261898fa0510'
		        '15728e5a8aacaa68ffffffffffffffff', 16)
		super(KexGroup14, self).__init__('sha1', 2, p)

def get_ssh_version(version_desc):
	if version_desc.startswith('d'):
		return ('Dropbear SSH', version_desc[1:])
	else:
		return ('OpenSSH', version_desc)

def get_alg_since_text(alg_desc):
	tv = []
	versions = alg_desc[0]
	for v in versions[0].split(','):
		ssh_prefix, ssh_version = get_ssh_version(v)
		tv.append('{0} {1}'.format(ssh_prefix, ssh_version))
	return 'available since ' + ', '.join(tv).rstrip(', ')

def get_alg_timeframe(alg_desc, result = {}):
	versions = alg_desc[0]
	vlen = len(versions)
	for i in range(3):
		if i > vlen - 1:
			if i == 2 and vlen > 1:
				cversions = versions[1]
			else:
				continue
		else:
			cversions = versions[i]
		if cversions is None:
			continue
		for v in cversions.split(','):
			ssh_prefix, ssh_version = get_ssh_version(v)
			if not ssh_prefix in result:
				result[ssh_prefix] = [None, None, None]
			prev, push = result[ssh_prefix][i], False
			if prev is None:
				push = True
			elif i == 0 and prev < ssh_version:
				push = True
			elif i > 0 and prev > ssh_version:
				push = True
			if push:
				result[ssh_prefix][i] = ssh_version
	return result

def get_ssh_timeframe(kex):
	alg_timeframe = {}
	algs = {'kex': kex.kex_algorithms, 
	        'key': kex.key_algorithms,
	        'enc': kex.server.encryption,
	        'mac': kex.server.mac}
	for alg_type, alg_list in algs.items():
		for alg_name in alg_list:
			alg_desc = KEX_DB[alg_type].get(alg_name)
			if alg_desc is None:
				continue
			alg_timeframe = get_alg_timeframe(alg_desc, alg_timeframe)
	return alg_timeframe

WARN_OPENSSH72_LEGACY = 'disabled (in client) since OpenSSH 7.2, legacy algorithm'
FAIL_OPENSSH70_LEGACY = 'removed since OpenSSH 7.0, legacy algorithm'
FAIL_OPENSSH70_WEAK   = 'removed (in server) and disabled (in client) since OpenSSH 7.0, weak algorithm'
FAIL_OPENSSH70_LOGJAM = 'disabled (in client) since OpenSSH 7.0, logjam attack'
INFO_OPENSSH69_CHACHA = 'default cipher since OpenSSH 6.9.'
FAIL_OPENSSH67_UNSAFE = 'removed (in server) since OpenSSH 6.7, unsafe algorithm'
FAIL_OPENSSH61_REMOVE = 'removed since OpenSSH 6.1, removed from specification'
FAIL_OPENSSH31_REMOVE = 'removed since OpenSSH 3.1'
FAIL_DBEAR67_DISABLED = 'disabled since Dropbear SSH 2015.67'
FAIL_DBEAR53_DISABLED = 'disabled since Dropbear SSH 0.53'
FAIL_PLAINTEXT        = 'no encryption/integrity'

TEXT_CURVES_WEAK      = 'using weak elliptic curves'
TEXT_RNDSIG_KEY       = 'using weak random number generator could reveal the key'
TEXT_MODULUS_SIZE     = 'using small 1024-bit modulus'
TEXT_MODULUS_CUSTOM   = 'using custom size modulus (possibly weak)'
TEXT_HASH_WEAK        = 'using weak hashing algorithm'
TEXT_CIPHER_MODE      = 'using weak cipher mode'
TEXT_BLOCK_SIZE       = 'using small 64-bit block size'
TEXT_CIPHER_WEAK      = 'using weak cipher'
TEXT_ENCRYPT_AND_MAC  = 'using encrypt-and-MAC mode'
TEXT_TAG_SIZE         = 'using small 64-bit tag size'

KEX_DB = {
	'kex': {
		'diffie-hellman-group1-sha1': [['2.3.0,d0.28', '6.6', '6.9'], [FAIL_OPENSSH67_UNSAFE, FAIL_OPENSSH70_LOGJAM], [TEXT_MODULUS_SIZE, TEXT_HASH_WEAK]],
		'diffie-hellman-group14-sha1': [['3.9,d0.53'], [], [TEXT_HASH_WEAK]],
		'diffie-hellman-group14-sha256': [['7.3,d2016.73']],
		'diffie-hellman-group16-sha512': [['7.3,d2016.73']],
		'diffie-hellman-group18-sha512': [['7.3']],
		'diffie-hellman-group-exchange-sha1': [['2.3.0', '6.6', None], [FAIL_OPENSSH67_UNSAFE], [TEXT_HASH_WEAK]],
		'diffie-hellman-group-exchange-sha256': [['4.4'], [], [TEXT_MODULUS_CUSTOM]],
		'ecdh-sha2-nistp256': [['5.7,d2013.62'], [TEXT_CURVES_WEAK]],
		'ecdh-sha2-nistp384': [['5.7,d2013.62'], [TEXT_CURVES_WEAK]],
		'ecdh-sha2-nistp521': [['5.7,d2013.62'], [TEXT_CURVES_WEAK]],
		'curve25519-sha256@libssh.org': [['6.5,d2013.62']],
		'kexguess2@matt.ucc.asn.au': [['d2013.57']],
	},
	'key': {
		'rsa-sha2-256': [['7.2']],
		'rsa-sha2-512': [['7.2']],
		'ssh-ed25519': [['6.5']],
		'ssh-ed25519-cert-v01@openssh.com': [['6.5']],
		'ssh-rsa': [['2.5.0,d0.28']],
		'ssh-dss': [['2.1.0,d0.28', '6.9'], [FAIL_OPENSSH70_WEAK], [TEXT_MODULUS_SIZE, TEXT_RNDSIG_KEY]],
		'ecdsa-sha2-nistp256': [['5.7,d2013.62'], [TEXT_CURVES_WEAK], [TEXT_RNDSIG_KEY]],
		'ecdsa-sha2-nistp384': [['5.7,d2013.62'], [TEXT_CURVES_WEAK], [TEXT_RNDSIG_KEY]],
		'ecdsa-sha2-nistp521': [['5.7,d2013.62'], [TEXT_CURVES_WEAK], [TEXT_RNDSIG_KEY]],
		'ssh-rsa-cert-v00@openssh.com': [['5.4', '6.9'], [FAIL_OPENSSH70_LEGACY], []],
		'ssh-dss-cert-v00@openssh.com': [['5.4', '6.9'], [FAIL_OPENSSH70_LEGACY], [TEXT_MODULUS_SIZE, TEXT_RNDSIG_KEY]], 
		'ssh-rsa-cert-v01@openssh.com': [['5.6']],
		'ssh-dss-cert-v01@openssh.com': [['5.6', '6.9'], [FAIL_OPENSSH70_WEAK], [TEXT_MODULUS_SIZE, TEXT_RNDSIG_KEY]],
		'ecdsa-sha2-nistp256-cert-v01@openssh.com': [['5.7'], [TEXT_CURVES_WEAK], [TEXT_RNDSIG_KEY]],
		'ecdsa-sha2-nistp384-cert-v01@openssh.com': [['5.7'], [TEXT_CURVES_WEAK], [TEXT_RNDSIG_KEY]],
		'ecdsa-sha2-nistp521-cert-v01@openssh.com': [['5.7'], [TEXT_CURVES_WEAK], [TEXT_RNDSIG_KEY]],
	},
	'enc': {
		'none': [['1.2.2,d2013.56'], [FAIL_PLAINTEXT]],
		'3des-cbc': [['1.2.2,d0.28', '6.6', None], [FAIL_OPENSSH67_UNSAFE], [TEXT_CIPHER_WEAK, TEXT_CIPHER_MODE, TEXT_BLOCK_SIZE]],
		'3des-ctr': [['d0.52']],
		'blowfish-cbc': [['1.2.2,d0.28', '6.6,d0.52', '7.1,d0.52'], [FAIL_OPENSSH67_UNSAFE, FAIL_DBEAR53_DISABLED], [WARN_OPENSSH72_LEGACY, TEXT_CIPHER_MODE, TEXT_BLOCK_SIZE]], 
		'twofish-cbc': [['d0.28', 'd2014.66'], [FAIL_DBEAR67_DISABLED], [TEXT_CIPHER_MODE]],
		'twofish128-cbc': [['d0.47', 'd2014.66'], [FAIL_DBEAR67_DISABLED], [TEXT_CIPHER_MODE]],
		'twofish256-cbc': [['d0.47', 'd2014.66'], [FAIL_DBEAR67_DISABLED], [TEXT_CIPHER_MODE]],
		'twofish128-ctr': [['d2015.68']],
		'twofish256-ctr': [['d2015.68']],
		'cast128-cbc': [['2.1.0', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, TEXT_CIPHER_MODE, TEXT_BLOCK_SIZE]],
		'arcfour': [['2.1.0', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, TEXT_CIPHER_WEAK]],
		'arcfour128': [['4.2', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, TEXT_CIPHER_WEAK]],
		'arcfour256': [['4.2', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, TEXT_CIPHER_WEAK]],
		'aes128-cbc': [['2.3.0,d0.28', '6.6', None], [FAIL_OPENSSH67_UNSAFE], [TEXT_CIPHER_MODE]],
		'aes192-cbc': [['2.3.0', '6.6', None], [FAIL_OPENSSH67_UNSAFE], [TEXT_CIPHER_MODE]],
		'aes256-cbc': [['2.3.0,d0.47', '6.6', None], [FAIL_OPENSSH67_UNSAFE], [TEXT_CIPHER_MODE]],
		'rijndael128-cbc': [['2.3.0', '3.0.2'], [FAIL_OPENSSH31_REMOVE], [TEXT_CIPHER_MODE]],
		'rijndael192-cbc': [['2.3.0', '3.0.2'], [FAIL_OPENSSH31_REMOVE], [TEXT_CIPHER_MODE]],
		'rijndael256-cbc': [['2.3.0', '3.0.2'], [FAIL_OPENSSH31_REMOVE], [TEXT_CIPHER_MODE]],
		'rijndael-cbc@lysator.liu.se': [['2.3.0', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, TEXT_CIPHER_MODE]],
		'aes128-ctr': [['3.7,d0.52']],
		'aes192-ctr': [['3.7']],
		'aes256-ctr': [['3.7,d0.52']],
		'aes128-gcm@openssh.com': [['6.2']],
		'aes256-gcm@openssh.com': [['6.2']],
		'chacha20-poly1305@openssh.com': [['6.5'], [], [], [INFO_OPENSSH69_CHACHA]],
	},
	'mac': {
		'none': [['d2013.56'], [FAIL_PLAINTEXT]],
		'hmac-sha1': [['2.1.0,d0.28'], [], [TEXT_ENCRYPT_AND_MAC, TEXT_HASH_WEAK]],
		'hmac-sha1-96': [['2.5.0,d0.47', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, TEXT_ENCRYPT_AND_MAC, TEXT_HASH_WEAK]],
		'hmac-sha2-256': [['5.9,d2013.56'], [], [TEXT_ENCRYPT_AND_MAC]],
		'hmac-sha2-256-96': [['5.9', '6.0'], [FAIL_OPENSSH61_REMOVE], [TEXT_ENCRYPT_AND_MAC]],
		'hmac-sha2-512': [['5.9,d2013.56'], [], [TEXT_ENCRYPT_AND_MAC]],
		'hmac-sha2-512-96': [['5.9', '6.0'], [FAIL_OPENSSH61_REMOVE], [TEXT_ENCRYPT_AND_MAC]],
		'hmac-md5': [['2.1.0,d0.28', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, TEXT_ENCRYPT_AND_MAC, TEXT_HASH_WEAK]],
		'hmac-md5-96': [['2.5.0', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, TEXT_ENCRYPT_AND_MAC, TEXT_HASH_WEAK]],
		'hmac-ripemd160': [['2.5.0', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, TEXT_ENCRYPT_AND_MAC]],
		'hmac-ripemd160@openssh.com': [['2.1.0', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, TEXT_ENCRYPT_AND_MAC]],
		'umac-64@openssh.com': [['4.7'], [], [TEXT_ENCRYPT_AND_MAC, TEXT_TAG_SIZE]],
		'umac-128@openssh.com': [['6.2'], [], [TEXT_ENCRYPT_AND_MAC]],
		'hmac-sha1-etm@openssh.com': [['6.2'], [], [TEXT_HASH_WEAK]],
		'hmac-sha1-96-etm@openssh.com': [['6.2', '6.6', None], [FAIL_OPENSSH67_UNSAFE], [TEXT_HASH_WEAK]],
		'hmac-sha2-256-etm@openssh.com': [['6.2']],
		'hmac-sha2-512-etm@openssh.com': [['6.2']],
		'hmac-md5-etm@openssh.com': [['6.2', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, TEXT_HASH_WEAK]],
		'hmac-md5-96-etm@openssh.com': [['6.2', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY, TEXT_HASH_WEAK]],
		'hmac-ripemd160-etm@openssh.com': [['6.2', '6.6', '7.1'], [FAIL_OPENSSH67_UNSAFE], [WARN_OPENSSH72_LEGACY]],
		'umac-64-etm@openssh.com': [['6.2'], [], [TEXT_TAG_SIZE]],
		'umac-128-etm@openssh.com': [['6.2']],
	}
}

def output_algorithms(alg_type, algorithms, maxlen=0):
	for algorithm in algorithms:
		output_algorithm(alg_type, algorithm, maxlen)

def output_algorithm(alg_type, alg_name, alg_max_len=0):
	prefix = '(' + alg_type + ') '
	if alg_max_len == 0:
		alg_max_len = len(alg_name)
	padding = ' ' * (alg_max_len - len(alg_name))
	texts = []
	if alg_name in KEX_DB[alg_type]:
		alg_desc = KEX_DB[alg_type][alg_name]
		ldesc = len(alg_desc)
		for idx, level in enumerate(['fail', 'warn', 'info']):
			if level == 'info':
				texts.append((level, get_alg_since_text(alg_desc)))
			idx = idx + 1
			if ldesc > idx:
				for t in alg_desc[idx]: 
					texts.append((level, t))
		if len(texts) == 0:
			texts.append(('info', ''))
	else:
		texts.append(('warn', 'unknown algorithm'))
	first = True
	for (level, text) in texts:
		f = getattr(out, level)
		text = '[' + level + '] ' + text
		if first:
			if first and level == 'info':
				f = out.good
			f(prefix + alg_name + padding + ' -- ' + text)
			first = False
		else:
			if out.verbose:
				f(prefix + alg_name + padding + ' -- ' + text)
			else:
				f(' ' * len(prefix + alg_name) + padding + ' `- ' + text)

def output_compatibility(kex, client=False):
	ssh_timeframe = get_ssh_timeframe(kex)
	cp = 2 if client else 1
	comp_text = []
	for sshd_name, v in ssh_timeframe.items():
		if v[cp] is None:
			comp_text.append('{} {}+'.format(sshd_name, v[0]))
		elif v[0] == v[1]:
			comp_text.append('{} {}'.format(sshd_name, v[0]))
		else:
			if v[1] < v[0]:
				comp_text.append('{} {}+ (some functionality from {})'.format(sshd_name, v[0], v[1]))
			else:
				comp_text.append('{} {}-{}'.format(sshd_name, v[0], v[1]))
	if len(comp_text) > 0:
		out.good('[info] compatibility: ' + ', '.join(comp_text))

def output(banner, header, kex):
	if banner is not None or kex is not None:
		out.head('# general')
	if len(header) > 0:
		out.info('[info] header: ' + '\n'.join(header))
	if banner is not None:
		out.good('[info] banner: ' + banner)
		if banner.startswith('SSH-1.99-'):
			out.fail('[fail] protocol SSH1 enabled')
	if kex is None:
		return
	output_compatibility(kex)
	compressions = [x for x in kex.server.compression if x != 'none']
	if len(compressions) > 0:
		cmptxt = 'enabled ({0})'.format(', '.join(compressions))
	else:
		cmptxt = 'disabled'
	out.good('[info] compression is ' + cmptxt)
	ml = lambda l: max(len(i) for i in l)
	maxlen = max(ml(kex.kex_algorithms),
	             ml(kex.key_algorithms),
	             ml(kex.server.encryption),
	             ml(kex.server.mac))
	out.head('\n# key exchange algorithms')
	output_algorithms('kex', kex.kex_algorithms, maxlen)
	out.head('\n# host-key algorithms')
	output_algorithms('key', kex.key_algorithms, maxlen)
	out.head('\n# encryption algorithms (ciphers)')
	output_algorithms('enc', kex.server.encryption, maxlen)
	out.head('\n# message authentication code algorithms')
	output_algorithms('mac', kex.server.mac, maxlen)
	out.sep()


def parse_int(v):
	try:
		return int(v)
	except:
		return 0

def parse_args():
	host = None
	port = 22
	for arg in sys.argv[1:]:
		if arg.startswith('-'):
			arg = arg.lstrip('-')
			if arg == 'n': out.colors = False
			elif arg == 'v': out.verbose = True
			continue
		s = arg.split(':')
		host = s[0].strip()
		if len(s) > 1:
			port = parse_int(s[1])
	if not host or port <= 0:
		usage()
	return host, port

def main():
	host, port = parse_args()
	s = SSH.Socket(host, port)
	err = None
	banner, header = s.get_banner()
	if banner is None:
		err = '[exception] did not receive banner.'
	if err is None:
		packet_type, payload = s.read_packet()
		if packet_type < 0:
			err = '[exception] error reading packet ({0})'.format(payload)
		elif packet_type != SSH.MSG_KEXINIT:
			err = '[exception] did not receive MSG_KEXINIT (20), instead received unknown message ({0})'.format(packet_type)
	if err:
		output(banner, header, None)
		out.fail(err)
		sys.exit(1)
	kex = Kex.parse(payload)
	output(banner, header, kex)

if __name__ == '__main__':
	out = Output()
	main()
