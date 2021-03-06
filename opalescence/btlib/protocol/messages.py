# -*- coding: utf-8 -*-

"""
Model classes for messages received over the bittorrent protocol.
"""

from __future__ import annotations

__all__ = ['Message', 'Handshake', 'KeepAlive', 'Choke', 'Unchoke',
           'Interested', 'NotInterested', 'Have', 'Bitfield', 'Request',
           'Block', 'Piece', 'Cancel', 'MESSAGE_TYPES', 'ProtocolMessage']

import hashlib
import struct
from abc import abstractmethod
from typing import Optional, Union, AnyStr

import bitstring

from .errors import NonSequentialBlockError


class Message:
    """
    Base class for messages exchanged with the protocol

    Messages (except the initial handshake) look like:
    <Length prefix><Message ID><Payload>
    """

    def __str__(self):
        return str(type(self).__name__)

    def __repr__(self):
        return str(self)

    def __hash__(self):
        return hash(str(self))

    def __eq__(self, other):
        return type(self) == type(other)

    @abstractmethod
    def encode(self):
        pass

    @abstractmethod
    def decode(self, *args, **kwargs):
        pass


class NoInfoMessage:
    """
    Base class for a protocol message with only a
    message identifier and no additional info
    """
    msg_id = None

    @classmethod
    def decode(cls):
        return cls()

    @classmethod
    def encode(cls) -> bytes:
        return struct.pack(">IB", 1, cls.msg_id)


class Handshake(Message):
    """
    Handles the handshake message with the protocol
    """
    msg_len = 68
    fmt = struct.Struct(">B19s8x20s20s")

    def __init__(self, info_hash: bytes, peer_id: bytes):
        self.info_hash = info_hash
        self.peer_id = peer_id

    def __str__(self):
        return f"Handshake: ({self.peer_id}:{self.info_hash})"

    def __hash__(self):
        return hash((self.info_hash, self.peer_id))

    def __eq__(self, other: Handshake):
        if not isinstance(other, Handshake):
            return False
        return (self.info_hash == other.info_hash and
                self.peer_id == other.peer_id)

    def encode(self) -> bytes:
        """
        :return: handshake data to send to protocol
        """
        return self.fmt.pack(19, b'BitTorrent protocol',
                             self.info_hash, self.peer_id)

    @classmethod
    def decode(cls, handshake_data: bytes) -> Handshake:
        """
        :return: Handshake instance
        """
        unpacked_data = cls.fmt.unpack(handshake_data)
        return cls(unpacked_data[2], unpacked_data[3])


class KeepAlive(Message):
    """
    keep alive message

    <0000>
    """

    @classmethod
    def encode(cls) -> bytes:
        """
        :return: encoded message to be sent to protocol
        """
        return struct.pack(">I", 0)

    @classmethod
    def decode(cls):
        return cls()


class Choke(NoInfoMessage, Message):
    """
    choke message

    <0001><0>
    """
    msg_id = 0


class Unchoke(NoInfoMessage, Message):
    """
    unchoke message

    <0001><1>
    """
    msg_id = 1


class Interested(NoInfoMessage, Message):
    """
    interested message

    <0001><2>
    """
    msg_id = 2


class NotInterested(NoInfoMessage, Message):
    """
    not interested message

    <0001><3>
    """
    msg_id = 3


class Have(Message):
    """
    have message

    <0005><4><index>
    """
    msg_id = 4

    def __init__(self, index: int):
        self.index = index

    def __str__(self):
        return f"Have: {self.index}"

    def __hash__(self):
        return hash(self.index)

    def __eq__(self, other):
        if not isinstance(other, Have):
            return False
        return self.index == other.index

    def encode(self) -> bytes:
        """
        :return: encoded message to be sent to protocol
        """
        return struct.pack(">IBI", 5, self.msg_id, self.index)

    @classmethod
    def decode(cls, data: bytes) -> Have:
        """
        :return: an instance of the have message
        """
        piece = struct.unpack(">I", data)[0]
        return cls(piece)


class Bitfield(Message):
    """
    bitfield message

    <0001+X><5><bitfield>
    """
    msg_id = 5

    def __init__(self, bitfield: Optional[AnyStr]):
        if isinstance(bitfield, str):
            self.bitfield = bitstring.BitArray("0b" + bitfield)
        elif isinstance(bitfield, bytes):
            self.bitfield = bitstring.BitArray(bytes=bitfield)
        else:
            raise TypeError

    def __str__(self):
        return f"Bitfield: {self.bitfield}"

    def __hash__(self):
        return hash(self.bitfield)

    def __eq__(self, other: Bitfield):
        if not isinstance(other, Bitfield):
            return False
        return self.bitfield == other.bitfield

    def encode(self) -> bytes:
        """
        :return: encoded message to be sent to protocol
        """
        if self.bitfield is None:
            return b''
        bitfield_len = len(self.bitfield)
        return struct.pack(f">IB{bitfield_len}s", 1 + bitfield_len,
                           Bitfield.msg_id, self.bitfield.tobytes())

    @classmethod
    def decode(cls, data: bytes) -> Bitfield:
        """
        :return: an instance of the bitfield message
        """
        bitfield = struct.unpack(f">{len(data)}s", data)[0]
        return cls(bitfield)


class IndexableMessage(Message):
    size = 2 ** 14

    def __init__(self, index: int, begin: int, length: int = size):
        self.index = index
        self.begin = begin
        self.length = length

    def __str__(self):
        return f"({self.index}:{self.begin}:{self.length})"

    def __eq__(self, other: IndexableMessage):
        if not isinstance(other, type(self)):
            return False
        return (self.index == other.index and
                self.begin == other.begin and
                self.length == other.length)

    def __hash__(self):
        return hash((self.index, self.begin, self.length))

    def encode(self):
        raise NotImplementedError

    def decode(self, *args, **kwargs):
        raise NotImplementedError


class Request(IndexableMessage):
    """
    request message

    <0013><6><index><begin><length>
    """
    msg_id = 6
    stale_time = 2

    def __init__(self, index, begin, length):
        super().__init__(index, begin, length)
        self.peer_id = None
        self.requested_at = None
        self.num_retries = 0

    def __str__(self):
        return f"Request: ({super().__str__()})"

    def is_stale(self, current_time) -> bool:
        """
        :param current_time: Current time as a float value.
        :return: True if the Request was sent >= 2 seconds ago.
        """
        if current_time - self.requested_at >= 2:
            return True
        return False

    def encode(self) -> bytes:
        """
        :return: the request message encoded in bytes
        """
        return struct.pack(">IB3I", 13, self.msg_id, self.index, self.begin, self.length)

    @classmethod
    def decode(cls, data: bytes) -> Request:
        """
        :return: a decoded request message
        """
        request = struct.unpack(">3I", data)
        return cls(request[0], request[1], request[2])

    @classmethod
    def from_block(cls, block: Block) -> Request:
        """
        :param block: the block to make a request for
        :return: a request for the given block
        """
        if block.data:
            return cls(block.index, block.begin, len(block.data))
        return cls(block.index, block.begin, block.size)


class Block(IndexableMessage):
    """
    block message

    <0009+X><7><index><begin><block>
    """
    msg_id = 7

    def __init__(self, index: int, begin: int, length: int):
        self.data = b''
        super().__init__(index, begin, length)

    def __str__(self):
        return f"Block: ({super().__str__()})"

    def __hash__(self):
        return hash((self.index, self.begin, len(self.data)))

    def __eq__(self, other: Block):
        if not isinstance(other, Block):
            return False
        return (self.index == other.index and
                self.begin == other.begin and
                self.data == other.data)

    def encode(self) -> bytes:
        """
        :return: the piece message encoded in bytes
        """
        data_len = len(self.data)
        return struct.pack(f'>IBII{data_len}s', 9 + data_len, Block.msg_id,
                           self.index, self.begin, self.data)

    @classmethod
    def decode(cls, data: bytes) -> Block:
        """
        :return: a decoded piece message
        """
        data_len = len(data) - 8  # account for the index and begin bytes
        piece_data = struct.unpack(f">II{data_len}s", data)
        inst = cls(piece_data[0], piece_data[1], len(piece_data[2]))
        inst.data = piece_data[2]
        return inst


class Cancel(IndexableMessage):
    """
    cancel message

    <0013><8><index><begin><length>
    """
    msg_id = 8

    def __str__(self):
        return f"Cancel: ({super().__str__()})"

    def encode(self) -> bytes:
        """
        :return: the cancel message encoded in bytes
        """
        return struct.pack(">IBIII", 13, Cancel.msg_id, self.index, self.begin,
                           self.length)

    @classmethod
    def decode(cls, data: bytes) -> Cancel:
        """
        :return: a decoded cancel message
        """
        cancel_data = struct.unpack(">III", data)
        return cls(cancel_data[0], cancel_data[1], cancel_data[2])


class Piece:
    """
    Represents a piece of the torrent.
    Pieces are made up of blocks.

    Not really a message itself.
    """

    def __init__(self, index: int, length: int, block_size: int):
        self.index: int = index
        self.length: int = length
        self.present: int = 0

        self._block_size: int = block_size
        self._blocks: list[Block] = []
        self._written: bool = False
        self._create_blocks()

    def __str__(self):
        return f"Piece: ({self.index}:{self.length}:{self.remaining})"

    def __repr__(self):
        return str(self)

    def __hash__(self):
        return hash((self.index, self.length, self.data))

    def __eq__(self, other: Piece):
        if not isinstance(other, Piece):
            return False
        equal = self.index == other.index and self.length and other.length
        if self.complete and other.complete:
            equal &= self.data == other.data
        return equal

    @property
    def data(self) -> bytes:
        if not self._written:
            return b''.join([b.data for b in self._blocks])

    @property
    def complete(self) -> bool:
        """
        :return: True if all blocks have been bytes_downloaded
        """
        return self.present == self.length

    @property
    def remaining(self) -> int:
        """
        :return: The number of bytes remaining in this piece.
        """
        return self.length - self.present

    @property
    def blocks(self) -> list[Block]:
        """
        :return: The list of blocks for this piece if the piece is incomplete.
        """
        if self.complete:
            return []
        return [block for block in self._blocks if len(block.data) == 0]

    def _create_blocks(self):
        """
        Creates the blocks that make up this piece.
        """
        if self._written:
            return

        num_blocks = (self.length + self._block_size - 1) // self._block_size
        self._blocks = [Block(self.index, idx * self._block_size, self._block_size)
                        for idx in range(num_blocks)]

    def add_block(self, block: Block):
        """
        Adds a block to this piece.
        :param block: The block message containing the block's info
        """
        if self.complete:
            return

        assert self.index == block.index

        block_index = block.begin // self._block_size
        if block_index < -1 or block_index > len(self._blocks):
            raise NonSequentialBlockError

        self._blocks[block_index] = block
        self.present += len(block.data)

    def mark_written(self):
        """
        Marks the piece as written to disk.
        """
        self.present = self.length
        self._written = True
        self._blocks = []

    def reset(self):
        """
        Resets the piece leaving it in a state equivalent to immediately after
        initializing.
        """
        self.present = 0
        self._written = False
        self._create_blocks()

    def hash(self) -> Optional[bytes]:
        """
        Returns the hash of the piece's data.
        """
        if self._written or not self.complete:
            return
        return hashlib.sha1(self.data).digest()


MESSAGE_TYPES = {
    0: Choke,
    1: Unchoke,
    2: Interested,
    3: NotInterested,
    4: Have,
    5: Bitfield,
    6: Request,
    7: Block,
    8: Cancel
}

ProtocolMessage = Union[
    Handshake, KeepAlive, Choke, Unchoke, Interested, NotInterested, Have, Bitfield,
    Request, Block, Cancel]
