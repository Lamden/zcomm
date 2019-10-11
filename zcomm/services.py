import zmq
import asyncio
import json


class SocketEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, SocketStruct):
            return str(o)
        return json.JSONEncoder.default(self, o)


class Protocols:
    TCP = 0
    INPROC = 1
    ICP = 2
    PROTOCOL_STRINGS = ['tcp://', 'inproc://', 'icp://']


# syntactic sugar yum yum
def _socket(s: str):
    return SocketStruct.from_string(s)


class SocketStruct:
    def __init__(self, protocol: int, id: str, port: int=0):
        self.protocol = protocol
        self.id = id

        if protocol == Protocols.INPROC:
            port = 0
        self.port = port

    def zmq_url(self):
        if not self.port:
            return '{}{}'.format(Protocols.PROTOCOL_STRINGS[self.protocol], self.id)
        else:
            return '{}{}:{}'.format(Protocols.PROTOCOL_STRINGS[self.protocol], self.id, self.port)

    @classmethod
    def from_string(cls, str):
        protocol = Protocols.TCP

        for protocol_string in Protocols.PROTOCOL_STRINGS:
            if len(str.split(protocol_string)) > 1:
                protocol = Protocols.PROTOCOL_STRINGS.index(protocol_string)
                str = str.split(protocol_string)[1]

        if protocol != Protocols.INPROC:
            _id, port = str.split(':')
            port = int(port)

            return cls(protocol=protocol, id=_id, port=port)
        else:
            return cls(protocol=protocol, id=str, port=None)

    @classmethod
    def is_valid(cls, s):
        return ':' in s

    def __str__(self):
        return self.zmq_url()

    def __repr__(self):
        return '<ZMQ Socket: "{}">'.format(self.__str__())

    def __eq__(self, other):
        return self.protocol == other.protocol and \
            self.id == other.id and \
            self.port == other.port


# Pushes current task to the back of the event loop
async def defer():
    await asyncio.sleep(0)


class SubscriptionService:
    def __init__(self, ctx: zmq.Context, timeout=100, linger=2000):
        # Socket constants
        self.ctx = ctx
        self.timeout = timeout
        self.linger = linger

        # State variables
        self.subscriptions = {}
        self.running = False

        # Async queues
        self.received = []
        self.to_remove = []

    def add_subscription(self, socket_id: SocketStruct, filter=b''):
        subscription = self.ctx.socket(zmq.SUB)
        subscription.setsockopt(zmq.SUBSCRIBE, filter)
        subscription.setsockopt(zmq.LINGER, self.linger)

        subscription.connect(str(socket_id))

        self.subscriptions[str(socket_id)] = subscription

    def _destroy_socket(self, socket_id: SocketStruct):
        socket = self.subscriptions.get(str(socket_id))
        if socket is not None:
            socket.close()

            del self.subscriptions[str(socket_id)]

    def remove_subscription(self, socket_id: SocketStruct):
        if self.running:
            self.to_remove.append(socket_id)
        else:
            self._destroy_socket(socket_id)

    async def serve(self):
        self.running = True

        while self.running:
            await asyncio.sleep(0)

            for address, socket in self.subscriptions.items():
                try:
                    event = await socket.poll(timeout=self.timeout, flags=zmq.POLLIN)
                    if event:
                        msg = await socket.recv()
                        self.received.append((msg, address))
                except zmq.error.ZMQError as e:
                    socket.close()

                    socket = self.ctx.socket(zmq.SUB)
                    socket.setsockopt(zmq.SUBSCRIBE, b'')
                    socket.setsockopt(zmq.LINGER, self.linger)

                    socket.connect(str(address))

            # Destory sockets async
            for address in self.to_remove:
                self._destroy_socket(address)
            self.to_remove = []

    def stop(self):
        self.running = False


class RequestReplyService:
    def __init__(self, socket_id: SocketStruct, ctx: zmq.Context, linger=2000, poll_timeout=2000):
        self.address = str(socket_id)
        self.ctx = ctx

        self.socket = None

        self.linger = linger
        self.poll_timeout = poll_timeout

        self.running = False

    async def serve(self):
        self.socket = self.ctx.socket(zmq.REP)
        self.socket.setsockopt(zmq.LINGER, self.linger)
        self.socket.bind(self.address)

        self.running = True

        while self.running:
            try:
                event = await self.socket.poll(timeout=self.poll_timeout, flags=zmq.POLLIN)
                if event:
                    msg = await self.socket.recv()
                    result = self.handle_msg(msg)

                    if result is None:
                        result = b''

                    await self.socket.poll(timeout=self.poll_timeout, flags=zmq.POLLOUT)
                    await self.socket.send(result)

            except zmq.error.ZMQError as e:
                self.socket = self.ctx.socket(zmq.REP)
                self.socket.setsockopt(zmq.LINGER, self.linger)
                self.socket.bind(self.address)

        self.socket.close()

    def handle_msg(self, msg):
        return msg

    def stop(self):
        self.running = False


async def get(socket_id: SocketStruct, msg: bytes, ctx:zmq.Context, timeout=500, linger=2000, retries=10, dealer=False):
    if retries < 0:
        return None

    if dealer:
        socket = ctx.socket(zmq.DEALER)
    else:
        socket = ctx.socket(zmq.REQ)

    socket.setsockopt(zmq.LINGER, linger)
    try:
        # Allow passing an existing socket to save time on initializing a _new one and waiting for connection.
        socket.connect(str(socket_id))

        await socket.send(msg)

        event = await socket.poll(timeout=timeout, flags=zmq.POLLIN)
        if event:
            response = await socket.recv()

            socket.close()

            return response
        else:
            socket.close()
            return None
    except Exception as e:
        socket.close()
        return await get(socket_id, msg, ctx, timeout, linger, retries-1)


class AsyncInbox:
    def __init__(self, socket_id: SocketStruct, ctx: zmq.Context, linger=2000, poll_timeout=2000):
        socket_id.id = '*'

        self.address = str(socket_id)

        self.ctx = ctx

        self.socket = None

        self.linger = linger
        self.poll_timeout = poll_timeout

        self.running = False

    async def serve(self):
        self.setup_socket()

        self.running = True

        while self.running:
            try:
                event = await self.socket.poll(timeout=self.poll_timeout, flags=zmq.POLLIN)
                if event:
                    _id = await self.socket.recv()
                    msg = await self.socket.recv()
                    asyncio.ensure_future(self.handle_msg(_id, msg))

            except zmq.error.ZMQError:
                self.socket.close()
                self.setup_socket()

        self.socket.close()

    async def handle_msg(self, _id, msg):
        await self.return_msg(_id, msg)

    async def return_msg(self, _id, msg):
        sent = False
        while not sent:
            try:
                await self.socket.send_multipart([_id, msg])
                sent = True
            except zmq.error.ZMQError:
                self.socket.close()
                self.setup_socket()

    def setup_socket(self):
        self.socket = self.ctx.socket(zmq.ROUTER)
        self.socket.setsockopt(zmq.LINGER, self.linger)
        self.socket.bind(self.address)

    def stop(self):
        self.running = False


class AsyncOutbox:
    def __init__(self, socket_id: SocketStruct, ctx: zmq.Context, linger=2000, poll_timeout=2000):
        self.ctx = ctx
        self.address = str(socket_id)

        self.linger = linger
        self.poll_timeout = poll_timeout

        self.socket = self.ctx.socket(zmq.DEALER)
        self.socket.setsockopt(zmq.LINGER, self.linger)

    def send(self, _id, msg_type, msg_payload):
        pass
