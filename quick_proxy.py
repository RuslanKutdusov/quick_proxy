#!/usr/bin/env python
from Queue import Queue
from datetime import datetime, timedelta

import socket
import errno
import re
from time import sleep
from threading import Thread
from glob import glob
from select import select
from imp import reload

import config


class Dumper:
    def __init__(self, port):
        #last_session_num = len(glob("port%d_*.dmp" % port))
        #self.filename = "port%d_%09d.dmp" % (port, last_session_num + 1)
        self.filename = "port%d.dmp" % (port,)

    def dump(self, data):
        with open(self.filename, "ab") as f:
            f.write(data)


class Proxy(Thread):
    def __init__(self,
                 listen_port, server_host, server_port,
                 listen_ipv6=False):

        Thread.__init__(self, name='port' + str(listen_port))

        server_addrs = socket.getaddrinfo(server_host, server_port, 0, self.get_socket_type())
        server_addr = server_addrs[0]
        if len(server_addrs) > 1:
            readable_addrs = [addr[4][0] for addr in server_addrs]
            print("host %s resolved into several addrs: %s. Using first: %s" %
                  (server_host, readable_addrs, server_addr[4][0]))

        self.listen_port = listen_port

        self.server_family = server_addr[0]
        self.server_socktype = server_addr[1]
        self.server_proto = server_addr[2]
        self.server_sockaddr = server_addr[4][:2]

        self.listen_ipv6 = listen_ipv6

        print("Proxy for port %s ready" % self.listen_port)

    def run(self):
        if self.listen_ipv6:
            proxy = socket.socket(socket.AF_INET6, self.get_socket_type())
            proxy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            proxy.bind(("::", self.listen_port))
        else:
            proxy = socket.socket(socket.AF_INET, self.get_socket_type())
            proxy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            proxy.bind(("0.0.0.0", self.listen_port))

        self.proxy(proxy)

    def proxy(self, proxy):
        raise Exception('Abstract method `proxy` must be override')

    def get_socket_type(self):
        raise Exception('Abstract method `get_socket_type` must be override')


class UDPProxy(Proxy):
    def get_socket_type(self):
        return socket.SOCK_DGRAM

    def proxy(self, proxy):
        server_sockets = set()
        socket_pairs = SocketPairs()
        data_to_send = {}
        data_to_filter = {}
        socket_to_dumper = {}
        proxy.setblocking(0)

        def filter_data(s, data):
            data_to_filter[s] += data
            filter_not_passed = False
            for pattern in config.FILTER_RE:
                if re.search(pattern, data_to_filter[s]):
                    print("Connection dropped at pattern %s" % pattern)
                    filter_not_passed = True

            if not filter_not_passed:
                data_to_filter[s] = (data_to_filter[s][-config.FILTER_WINDOW_SIZE:])
                data_to_send[socket_pairs.get_pair(s)] += data
            else:
                s_pair = socket_pairs.get_pair(s)
                data_to_send[s_pair] = b''
                data_to_send[s] = b''
                data_to_filter[s_pair] = b''
                data_to_filter[s] = b''

        while True:
            want_read = set([proxy]) | server_sockets
            ready_read = select(want_read, [], [], 10)[0]

            if proxy in ready_read:
                data, address = proxy.recvfrom(65536)
                try:
                    server = socket_pairs.get_pair(address)
                except:
                    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    server.setblocking(0)
                    socket_to_dumper[server] = Dumper(self.listen_port)
                    server_sockets.add(server)
                    socket_pairs.add_pair(address, server)

                    data_to_send[address] = b''
                    data_to_send[server] = b''

                    data_to_filter[address] = b''
                    data_to_filter[server] = b''

                    print("Connect to port %s from %s" % (self.listen_port, address))

                socket_to_dumper[server].dump(data)
                filter_data(address, data)

            for s in ready_read:
                if s == proxy:
                    continue  # handled above

                data = s.recv(65536)
                socket_to_dumper[s].dump(data)
                filter_data(s, data)

            for s in data_to_send:
                if len(data_to_send[s]):
                    if isinstance(s, tuple):
                        sent = proxy.sendto(data_to_send[s], s)
                        socket_to_dumper[socket_pairs.get_pair(s)].dump(data_to_send[s][:sent])
                    else:
                        sent = s.sendto(data_to_send[s], self.server_sockaddr)
                        socket_to_dumper[s].dump(data_to_send[s][:sent])

                    data_to_send[s] = data_to_send[s][sent:]


class TCPProxy(Proxy):
    def get_socket_type(self):
        return socket.SOCK_STREAM

    def proxy(self, proxy):
        client_sockets = set()
        server_sockets = set()
        socket_pairs = SocketPairs()
        closed_but_data_left_sockets = set()
        data_to_send = {}
        data_to_filter = {}
        socket_to_dumper = {}

        proxy.listen(10)

        while True:
            vaild_sockets = client_sockets | server_sockets
            # clean up closed socket-pairs
            for s in list(socket_pairs.pairs):
                if (s in socket_pairs.pairs and
                            s not in vaild_sockets and
                            socket_pairs.get_pair(s) in socket_pairs.pairs and
                            socket_pairs.get_pair(s) not in vaild_sockets):
                    socket_pairs.del_pair(s)

            # clean up unused datas to send
            vaild_sockets = socket_pairs.pairs

            for s in list(data_to_send):
                if s not in vaild_sockets:
                    del data_to_send[s]

            for s in list(data_to_filter):
                if s not in vaild_sockets:
                    del data_to_filter[s]

            for s in list(socket_to_dumper):
                if s not in vaild_sockets:
                    del socket_to_dumper[s]

            try:
                want_read = set([proxy]) | client_sockets | server_sockets
                have_out_data = [s for s in data_to_send if data_to_send[s]]
                want_write = set(have_out_data)

                ready_read, ready_write = select(want_read, want_write,
                    [], 10)[:2]
            except:
                # clean up bad want_read's and want_write's fd
                for s in want_read:
                    try:
                        select([s], [], [], 0)
                    except:
                        client_sockets.discard(s)
                        server_sockets.discard(s)
                        data_to_send[s] = b''

                for s in want_write:
                    try:
                        select([], [s], [], 0)
                    except:
                        client_sockets.discard(s)
                        server_sockets.discard(s)
                        data_to_send[s] = b''
                continue

            if proxy in ready_read:
                client, address = proxy.accept()
                client.setblocking(0)

                server = socket.socket(self.server_family,
                                       self.server_socktype)
                server.setblocking(0)

                try:
                    server.connect(self.server_sockaddr)
                except socket.error as E:
                    if E.errno == errno.EINPROGRESS or E.errno == 10035:
                        pass  # it is normal to have EINPROGRESS here
                    else:
                        client.close()
                        server.close()
                        continue

                socket_to_dumper[server] = Dumper(self.listen_port)

                socket_pairs.add_pair(client, server)

                data_to_send[client] = b''
                data_to_send[server] = b''

                data_to_filter[client] = b''
                data_to_filter[server] = b''

                client_sockets.add(client)
                server_sockets.add(server)

                print("Connect to port %s from %s" %
                      (self.listen_port, address))

            for s in ready_read:
                if s == proxy:
                    continue  # handled above
                s_pair = socket_pairs.get_pair(s)

                try:
                    data = s.recv(65536)
                except:
                    s_pair.close()
                    break

                if data:
                    if s in server_sockets:
                        socket_to_dumper[s].dump(data)

                    # check if to filter data out
                    data_to_filter[s] += data

                    for pattern in config.FILTER_RE:
                        if re.search(pattern, data_to_filter[s]):
                            print("Connection dropped at pattern %s" % pattern)
                            s.close()
                            s_pair.close()

                    data_to_filter[s] = (
                        data_to_filter[s][-config.FILTER_WINDOW_SIZE:]
                    )

                    data_to_send[s_pair] += data
                else:  # connection was closed
                    if s in server_sockets and data_to_send[s_pair]:
                        closed_but_data_left_sockets.add(s_pair)
                        s_pair.shutdown(socket.SHUT_RD)
                    else:
                        s_pair.close()

                    s.close()
                    break

            for s in ready_write:
                if not data_to_send[s]:
                    if s in closed_but_data_left_sockets:
                        closed_but_data_left_sockets.remove(s)
                        s.close()
                    break

                try:
                    sent = s.send(data_to_send[s])
                except:
                    socket_pairs.get_pair(s).close()
                    break

                if s in server_sockets:
                    socket_to_dumper[s].dump(data_to_send[s][:sent])

                data_to_send[s] = data_to_send[s][sent:]


class SocketPairs:
    def __init__(self):
        self.pairs = {}

    def add_pair(self, s1, s2):
        self.pairs[s1] = s2
        self.pairs[s2] = s1

    def get_pair(self, s):
        return self.pairs[s]

    def del_pair(self, s1, s2=None):
        if s2 is None:
            s2 = self.pairs[s1]

        del self.pairs[s1]
        del self.pairs[s2]


for listen_port, sockaddr in config.PROXYMAPS.items():
    proxy_impl = UDPProxy if len(sockaddr) is 3 and sockaddr[2] else TCPProxy
    p = proxy_impl(listen_port, sockaddr[0], sockaddr[1])
    p.daemon = True
    p.start()

while True:
    sleep(5)
    try:
        import config

        reload(config)
    except Exception as E:
        print("Reload failed: %s" % E)
