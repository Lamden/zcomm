from unittest import TestCase
from zcomm.services import SubscriptionService, SocketStruct, Protocols
from zcomm import services
import zmq.asyncio
import asyncio


async def stop_server(s, timeout):
    await asyncio.sleep(timeout)
    s.stop()


class TestSubscriptionService(TestCase):
    def setUp(self):
        self.ctx = zmq.asyncio.Context()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.ctx.destroy()
        self.loop.close()

    def test_init(self):
        SubscriptionService(ctx=self.ctx)

    def test_add_subscription_modifies_dict(self):
        s = SubscriptionService(ctx=self.ctx)

        s.add_subscription(SocketStruct.from_string('tcp://127.0.0.1:10001'))
        s.add_subscription(SocketStruct.from_string('tcp://127.0.0.1:10002'))
        s.add_subscription(SocketStruct.from_string('tcp://127.0.0.1:10003'))
        s.add_subscription(SocketStruct.from_string('tcp://127.0.0.1:10004'))

        self.assertTrue(s.subscriptions['tcp://127.0.0.1:10001'])
        self.assertTrue(s.subscriptions['tcp://127.0.0.1:10002'])
        self.assertTrue(s.subscriptions['tcp://127.0.0.1:10003'])
        self.assertTrue(s.subscriptions['tcp://127.0.0.1:10004'])

    def test_remove_subscription_deletes_from_dict(self):
        s = SubscriptionService(ctx=self.ctx)

        s.add_subscription(SocketStruct.from_string('tcp://127.0.0.1:10001'))
        s.add_subscription(SocketStruct.from_string('tcp://127.0.0.1:10002'))
        s.add_subscription(SocketStruct.from_string('tcp://127.0.0.1:10003'))
        s.add_subscription(SocketStruct.from_string('tcp://127.0.0.1:10004'))

        s.remove_subscription(SocketStruct.from_string('tcp://127.0.0.1:10001'))
        s.remove_subscription(SocketStruct.from_string('tcp://127.0.0.1:10003'))

        self.assertIsNone(s.subscriptions.get('tcp://127.0.0.1:10001'))
        self.assertTrue(s.subscriptions['tcp://127.0.0.1:10002'])
        self.assertIsNone(s.subscriptions.get('tcp://127.0.0.1:10003'))
        self.assertTrue(s.subscriptions['tcp://127.0.0.1:10004'])

    def test_pub_sub_single_socket(self):
        pub = self.ctx.socket(zmq.PUB)
        pub.bind('inproc://test1')

        s = SubscriptionService(ctx=self.ctx)

        s.add_subscription(SocketStruct.from_string('inproc://test1'))

        tasks = asyncio.gather(
            s.serve(),
            pub.send(b'howdy'),
            pub.send(b'howdy2'),
            stop_server(s, 0.1)
        )

        loop = asyncio.get_event_loop()
        loop.run_until_complete(tasks)

        self.assertListEqual(s.received, [(b'howdy', 'inproc://test1'), (b'howdy2', 'inproc://test1')])

    def test_pub_sub_multi_sockets(self):
        pub1 = self.ctx.socket(zmq.PUB)
        pub1.bind('inproc://test1')

        pub2 = self.ctx.socket(zmq.PUB)
        pub2.bind('inproc://test2')

        s = SubscriptionService(ctx=self.ctx)

        s.add_subscription(SocketStruct.from_string('inproc://test1'))
        s.add_subscription(SocketStruct.from_string('inproc://test2'))

        tasks = asyncio.gather(
            s.serve(),
            pub1.send(b'howdy'),
            pub2.send(b'howdy2'),
            stop_server(s, 0.1)
        )

        loop = asyncio.get_event_loop()
        loop.run_until_complete(tasks)

        self.assertListEqual(s.received, [(b'howdy', 'inproc://test1'), (b'howdy2', 'inproc://test2')])

    def test_pub_sub_multi_sockets_remove_one(self):
        pub1 = self.ctx.socket(zmq.PUB)
        pub1.bind('inproc://test1')

        pub2 = self.ctx.socket(zmq.PUB)
        pub2.bind('inproc://test2')

        s = SubscriptionService(ctx=self.ctx)

        s.add_subscription(SocketStruct.from_string('inproc://test1'))
        s.add_subscription(SocketStruct.from_string('inproc://test2'))

        async def remove():
            s.remove_subscription(SocketStruct.from_string('inproc://test2'))

        async def delayed_send():
            await asyncio.sleep(0.2)
            pub2.send(b'howdy2')

        tasks = asyncio.gather(
            s.serve(),
            pub1.send(b'howdy'),
            remove(),
            stop_server(s, 0.2),
            delayed_send()
        )

        loop = asyncio.get_event_loop()
        loop.run_until_complete(tasks)

        self.assertListEqual(s.received, [(b'howdy', 'inproc://test1')])
        self.assertListEqual(s.to_remove, [])

    def test_socket_structure_serialization(self):
        s = SocketStruct(Protocols.TCP, '127.0.0.1', 1000)

        self.assertEqual(str(s), 'tcp://127.0.0.1:1000')

    def test_socket_serialization_properly(self):
        s = SocketStruct.from_string('tcp://127.0.0.1:1000')
        self.assertEqual(str(s), 'tcp://127.0.0.1:1000')

    def test_inproc_sock_serialization(self):
        s = SocketStruct(Protocols.INPROC, '127.0.0.1', 1000)

        self.assertEqual(str(s), 'inproc://127.0.0.1')

    def test_from_string_in_proc(self):
        s = SocketStruct.from_string('inproc://blahblahblah')

        self.assertEqual(str(s), 'inproc://blahblahblah')


class TestAsyncServer(TestCase):
    def setUp(self):
        self.ctx = zmq.asyncio.Context()

    def tearDown(self):
        self.ctx.destroy()

    def test_init(self):
        services.AsyncInbox(services._socket('tcp://127.0.0.1:10000'), self.ctx)

    def test_addresses_correct(self):
        m = services.AsyncInbox(services._socket('tcp://127.0.0.1:10000'), self.ctx)

        self.assertEqual(m.address, 'tcp://*:10000')

    def test_sockets_are_initially_none(self):
        m = services.AsyncInbox(services._socket('tcp://127.0.0.1:10000'), self.ctx)

        self.assertIsNone(m.socket)

    def test_setup_frontend_creates_socket(self):
        m = services.AsyncInbox(services._socket('tcp://127.0.0.1:10000'), self.ctx)
        m.setup_socket()

        self.assertEqual(m.socket.type, zmq.ROUTER)
        self.assertEqual(m.socket.getsockopt(zmq.LINGER), m.linger)

    def test_sending_message_returns_it(self):
        m = services.AsyncInbox(services._socket('tcp://127.0.0.1:10000'), self.ctx, linger=500, poll_timeout=500)

        async def get(msg):
            socket = self.ctx.socket(zmq.DEALER)
            socket.connect('tcp://127.0.0.1:10000')

            await socket.send(msg)

            res = await socket.recv()

            return res

        tasks = asyncio.gather(
            m.serve(),
            get(b'howdy'),
            stop_server(m, 0.2),
        )

        loop = asyncio.get_event_loop()
        res = loop.run_until_complete(tasks)

        self.assertEqual(res[1], b'howdy')
