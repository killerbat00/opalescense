#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Support for basic communication with a peer.
The piece-requesting and saving strategies are in piece_handler.py
The coordination with peers is handled in ../client.py

No data is currently sent to the remote peer
"""
import asyncio
from asyncio import StreamReader, StreamWriter, CancelledError, Queue

from .messages import *
from .piece_handler import PieceRequester

logger = logging.getLogger(__name__)


class PeerError(Exception):
    """
    Raised when we encounter an error communicating with the peer.
    """


class PeerInfo:
    def __init__(self, ip: str, port: int, peer_id: Optional[bytes] = None):
        self.ip: str = ip
        self.port: int = port
        self._peer_id: Optional[bytes] = peer_id

    @property
    def peer_id(self) -> str:
        if self._peer_id:
            return self._peer_id.decode("UTF-8")
        return f"{self.ip}:{self.port}"

    @peer_id.setter
    def peer_id(self, val):
        if isinstance(val, bytes):
            self._peer_id = val


class PeerState:
    def __init__(self):
        self.choking = True
        self.interested = False


class PeerConnection:
    """
    Represents a peer and provides methods for communicating with that peer.
    """
    CHUNK_SIZE = 10 * 1024

    # TODO: Add support for sending pieces to the peer
    def __init__(self, peer_info: PeerInfo, info_hash: bytes, requester: PieceRequester, our_info: PeerInfo):
        self.peer: PeerInfo = peer_info
        self.info_hash: bytes = info_hash
        self._hash: int = hash(f"{self.peer.ip}:{self.peer.port}:{self.info_hash.decode('UTF-8')}")
        self._local_peer: PeerInfo = our_info
        self._local_state: PeerState = PeerState()
        self._peer_state: PeerState = PeerState()
        self._requester: PieceRequester = requester
        self.read_task: Optional[asyncio.Task] = None
        self.write_task: Optional[asyncio.Task] = None
        self._stream_reader: Optional[StreamReader] = None
        self._stream_writer: Optional[StreamWriter] = None
        self._msg_to_send_q: Queue = Queue()

    def __str__(self):
        return f"{self.peer.ip}:{self.peer.port}"

    def __hash__(self):
        return self._hash

    def __eq__(self, other):
        return self.peer.port == other.peer.port and self.peer.ip == other.peer.ip and self.info_hash == other.info_hash

    async def download(self):
        await self.make_connection()
        self._msg_to_send_q.put_nowait((f"{self}: Sending interested message.", Interested.encode()))
        self.read_task = asyncio.create_task(self._consume())
        self.write_task = asyncio.create_task(self._produce())
        try:
            await asyncio.gather(self.read_task, self.write_task)
        except (CancelledError, PeerError) as e:
            raise PeerError from e

    async def make_connection(self):
        """
        Starts communication with the peer, sending our handshake and letting the peer know we're interested.
        """
        try:
            logger.debug(f"{self}: Opening connection with peer.")
            self._stream_reader, self._stream_writer = await asyncio.open_connection(
                host=self.peer.ip, port=self.peer.port, local_addr=(self._local_peer.ip, self._local_peer.port)
            )

            if not await self._handshake():
                raise PeerError

        except Exception as oe:
            logger.debug(f"{self}: Exception with connection.\n{oe}")
            raise PeerError from oe

    async def _consume(self):
        """
        Iterates through the data we have, requesting more
        from the protocol if necessary, and tries to decode and add
        a valid message from that data to a queue

        :raises MessageReaderException:
        """
        try:
            while not self.read_task.done():
                msg_len = struct.unpack(">I", await self._stream_reader.readexactly(4))[0]

                if msg_len == 0:
                    logger.debug(f"{self}: Sent {KeepAlive()}")
                    continue

                msg_id = struct.unpack(">B", await self._stream_reader.readexactly(1))[0]
                msg_len -= 1  # the msg_len includes 1 byte for the id, we've consumed that already

                if msg_id == 0:
                    logger.debug(f"{self}: Sent {Choke()}")
                    self._peer_state.choking = True
                elif msg_id == 1:
                    logger.debug(f"{self}: Sent {Unchoke()}")
                    self._peer_state.choking = False
                elif msg_id == 2:
                    # we don't do anything with this right now
                    logger.debug(f"{self}: Sent {Interested()}")
                    self._peer_state.interested = True
                elif msg_id == 3:
                    logger.debug(f"{self}: Sent {NotInterested()}")
                    self._peer_state.interested = False
                elif msg_id == 4:
                    msg = Have.decode(await self._stream_reader.readexactly(msg_len))
                    logger.debug(f"{self}: {msg}")
                    self._requester.add_available_piece(self.peer.peer_id, msg.index)
                elif msg_id == 5:
                    msg = Bitfield.decode(await self._stream_reader.readexactly(msg_len))
                    logger.debug(f"{self}: {msg}")
                    self._requester.add_peer_bitfield(self.peer.peer_id, msg.bitfield)
                elif msg_id == 6:
                    msg = Request.decode(await self._stream_reader.readexactly(msg_len))
                    logger.debug(f"{self}: Requested {msg}")
                    # self._requester.add_peer_request(self.peer.peer_id, msg)
                elif msg_id == 7:
                    msg = Block.decode(await self._stream_reader.readexactly(msg_len))
                    logger.debug(f"{self}: {msg}")
                    self._requester.received_block(self.peer.peer_id, msg)
                elif msg_id == 8:
                    msg = Cancel.decode(await self._stream_reader.readexactly(msg_len))
                    logger.debug(f"{self}: {msg}")
                else:
                    raise PeerError(f"{self}: Unexpected message ID received: {msg_id}")
        except CancelledError:
            logger.debug(f"{self}: CancelledError handled in read_task.")
            self._requester.remove_peer(self.peer.peer_id)
            self.write_task.cancel()
        except asyncio.IncompleteReadError as ire:
            logger.error(f"{self}: Unable to read {ire.expected} bytes , read: {len(ire.partial)}")
            raise PeerError
        except Exception as e:
            logger.debug(f"{self}: Exception with connection.\n{e}")
            raise PeerError from e

    async def _produce(self):
        """
        Sends messages to the peer.
        """
        try:
            while not self.write_task.done():
                try:
                    log, msg = asyncio.wait_for(self._msg_to_send_q.get(), timeout=60.0)
                except asyncio.TimeoutError:
                    log = f"{self}: No message to send, sending KeepAlive."
                    msg = KeepAlive.encode()

                if isinstance(msg, Choke):
                    self._local_state.choking = True
                elif isinstance(msg, Unchoke):
                    logger.debug(f"{self}: Sent {msg}")
                    self._local_state.choking = False
                elif isinstance(msg, Interested):
                    self._local_state.interested = True
                elif isinstance(msg, NotInterested):
                    self._local_state.interested = False

                logger.debug(log)
                self._stream_writer.write(msg)
                await self._stream_writer.drain()

        except asyncio.CancelledError:
            logger.debug(f"{self}: Cancelling and closing connections.")
            self._requester.remove_peer(self.peer.peer_id)
            self.read_task.cancel()
        except Exception as oe:
            logger.debug(f"{self}: Exception with connection.\n{oe}")
            raise PeerError from oe

    async def _handshake(self) -> bool:
        """
        Negotiates the initial handshake with the peer.

        :return: True if the handshake is successful, False otherwise
        """
        logger.debug(f"{self}: Negotiating handshake.")
        sent_handshake = Handshake(self.info_hash, self._local_peer._peer_id)
        self._stream_writer.write(sent_handshake.encode())
        await self._stream_writer.drain()

        try:
            data = await self._stream_reader.readexactly(Handshake.msg_len)
        except asyncio.IncompleteReadError as ire:
            logger.error(f"{self}: Unable to initiate handshake, read: {len(ire.partial)}, expected: {ire.expected}")
            return False

        received = Handshake.decode(data)
        if received.info_hash != self.info_hash:
            logger.error(f"{self}: Unable in initiate handshake. Incorrect info hash received. expected: "
                         f"{self.info_hash}, received {received.info_hash}")
            return False

        if received.peer_id:
            self._local_peer.peer_id = received.peer_id
        return True
