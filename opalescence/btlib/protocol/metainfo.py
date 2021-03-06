# -*- coding: utf-8 -*-

"""
Support for representing a .torrent file as a python class and
creating a Torrent class (or .torrent file) from a specified file or directory.
"""

from __future__ import annotations

__all__ = ['MetaInfoFile']

import hashlib
import os
from collections import OrderedDict
from logging import getLogger
from pathlib import Path
from typing import List, Optional, Dict

from .bencode import *
from .errors import DecodeError, EncodeError, MetaInfoCreationError
from .fileio import FileItem
from .messages import Piece, Block

logger = getLogger(__name__)


def _get_and_decode(d: dict, k: str, encoding="UTF-8"):
    return d.get(k, b'').decode(encoding)


def _pc(piece_string: bytes, *, length: int = 20, start: int = 0):
    """
    Pieces a bytestring into pieces of specified length.

    :param piece_string: string to piece
    :param length:       piece length
    :return:             generator expression yielding pieces
    """
    return (piece_string[0 + i:length + i] for i in
            range(start, len(piece_string), length))


def _validate_torrent_dict(decoded_dict: OrderedDict) -> bool:
    """
    Verifies a given decoded dictionary contains valid keys.

    Currently only checks for the minimum required torrent keys.
    If a dictionary contains all valid keys + extra keys, it will be validated.

    :param decoded_dict: dict representing bencoded .torrent file
    :return:             True if valid
    :raises:             MetaInfoCreationError
    """
    min_info_req_keys: List[str] = ["piece length", "pieces"]
    min_files_req_keys: List[str] = ["length", "path"]

    dict_keys: List = list(decoded_dict.keys())
    if not dict_keys:
        logger.error("No valid keys in dictionary.")
        raise MetaInfoCreationError

    if "info" not in dict_keys or \
        ("announce" not in dict_keys and
         "announce-list" not in dict_keys):
        logger.error(f"Required key not found.")
        raise MetaInfoCreationError

    info_keys: list = list(decoded_dict["info"].keys())
    if not info_keys:
        logger.error("No valid keys in info dictionary.")
        raise MetaInfoCreationError

    for key in min_info_req_keys:
        if key not in info_keys:
            logger.error("Required key not found: %s" % key)
            raise MetaInfoCreationError

    if len(decoded_dict["info"]["pieces"]) % 20 != 0:
        logger.error("Piece length not a multiple of 20.")
        raise MetaInfoCreationError

    multiple_files: bool = "files" in info_keys

    if multiple_files:
        file_list = decoded_dict["info"]["files"]

        if not file_list:
            logger.error("No file list.")
            raise MetaInfoCreationError

        for f in file_list:
            for key in min_files_req_keys:
                if key not in f.keys():
                    logger.error("Required key not found: %s" % key)
                    raise MetaInfoCreationError
    else:
        if "length" not in info_keys:
            logger.error("Required key not found: 'length'")
            raise MetaInfoCreationError

    # we made it!
    return True


class MetaInfoFile:
    """
    Represents the metainfo for a torrent. Doesn't include any download state.

    Unsupported metainfo keys:
        encoding
    """

    def __init__(self):
        self.files: Dict[int, FileItem] = {}
        self.meta_info: Optional[OrderedDict] = None
        self.info_hash: bytes = b''
        self.piece_hashes: list[bytes] = []
        self.pieces: list[Piece] = []
        self.destination: Optional[Path] = None

    def __str__(self):
        return f"{self.name}"

    def __repr__(self):
        return f"<MetaInfoFile: {self}>"

    @classmethod
    def from_file(cls, filename: Path, destination: Path) -> MetaInfoFile:
        """
        Class method to create a torrent object from a .torrent metainfo file

        :param filename: path to .torrent file
        :param destination: destination ptah for torrent
        :raises MetaInfoCreationError:
        :return: Torrent instance
        """
        logger.info("Creating a metainfo object from %s" % filename)
        torrent: MetaInfoFile = cls()

        if not os.path.exists(filename):
            logger.error("Path does not exist %s" % filename)
            raise MetaInfoCreationError

        torrent.destination = destination

        try:
            with open(filename, 'rb') as f:
                torrent.meta_info = Decode(f.read())

            if not torrent.meta_info or not isinstance(torrent.meta_info, OrderedDict):
                logger.error("Unable to create torrent object."
                             "No metainfo decoded from file.")
                raise MetaInfoCreationError

            _validate_torrent_dict(torrent.meta_info)
            info: bytes = Encode(torrent.meta_info["info"])
            torrent.info_hash = hashlib.sha1(info).digest()

            torrent._gather_files()
            torrent._collect_pieces()

        except (EncodeError, DecodeError, IOError, Exception) as e:
            logger.debug("Encountered %s in MetaInfoFile.from_file" % type(e).__name__)
            raise MetaInfoCreationError from e

        return torrent

    def to_file(self, output_filename: str):
        """
        Writes the torrent metainfo dictionary back to a .torrent file

        :param output_filename: The output filename of the torrent
        :raises MetaInfoCreationError:
        """
        logger.info("Writing .torrent file: %s" % output_filename)

        if not output_filename:
            logger.error("No output filename provided.")
            raise MetaInfoCreationError

        with open(output_filename, 'wb+') as f:
            try:
                data: bytes = Encode(self.meta_info)
                f.write(data)
            except EncodeError as ee:
                logger.error("Encountered %s while writing metainfo file %s" %
                             (type(ee).__name__, output_filename))
                raise MetaInfoCreationError from ee

    def check_existing_pieces(self) -> None:
        """
        Checks the existing files on disk and verifies their piece hashes,
        marking them complete as appropriate.
        """
        assert self.files

        fps = {}
        try:
            for i, file in self.files.items():
                if file.exists:
                    fps[i] = open(file.path, "rb")
                    fps[i].seek(0)
                else:
                    fps[i] = None

            for i, piece in enumerate(self.pieces):
                file_index, file_offset = FileItem.file_for_offset(self.files,
                                                                   i * self.piece_length)

                if file_index not in fps:
                    continue  # probably raise an error.

                fp, file = fps[file_index], self.files[file_index]
                if fp is None or not file.exists:
                    continue

                fp.seek(file_offset)
                # Handle pieces spanning two files.
                if file_offset + piece.length > file.size:
                    next_file_index = file_index + 1

                    if next_file_index not in fps or fps[next_file_index] is None:
                        continue

                    first_file_len = file.size - file_offset
                    piece_data = fp.read(first_file_len)
                    fp = fps[next_file_index]
                    fp.seek(0)
                    piece_data += fp.read(piece.length - first_file_len)
                # piece is contained within a single file
                else:
                    piece_data = fp.read(piece.length)

                if len(piece_data) == piece.length:
                    if hashlib.sha1(piece_data).digest() == self.piece_hashes[i]:
                        piece.mark_written()
                    else:
                        piece.reset()
        finally:
            for fp in fps.values():
                if fp is not None:
                    fp.close()

    def _gather_files(self) -> None:
        """
        Gathers the files located in the torrent

        For a single file torrent,
            the meta_info["info"]["name"] is the torrent's
            content's file basename and meta_info["info"]["length"] is its size

        For multiple file torrents,
            the meta_info["info"]["name"] is the torrent's
            content's directory name and
            meta_info["info"]["files"] contains the content's file basename
            meta_info["info"]["files"]["length"] is the file's size
            meta_info["info"]["length"] doesn't contribute anything here
        """
        logger.info("Gathering files for .torrent: %s" % self)

        if self.multi_file:
            file_list = self.meta_info["info"]["files"]
            if not file_list:
                logger.error("No file list.")
                raise MetaInfoCreationError
            offset = 0
            for i, f in enumerate(file_list):
                length = f.get("length", 0)
                path = Path("/".join([x.decode("UTF-8") for x in f.get("path", [])]))
                filepath = self.destination / path
                exists = filepath.exists()
                self.files[i] = FileItem(filepath, length, offset, exists)
                offset += length
        else:
            filepath = self.destination / Path(_get_and_decode(self.meta_info["info"],
                                                               "name"))
            exists = filepath.exists()
            length = self.meta_info["info"].get("length", 0)
            self.files[0] = FileItem(filepath, length, 0, exists)

    def _collect_pieces(self) -> None:
        """
        Collects the piece hashes from the metainfo file and
        creates `Piece` objects for each piece.
        """
        logger.info("Collecting pieces and hashes for .torrent: %s" % self)
        self.piece_hashes = list(_pc(self.meta_info["info"]["pieces"]))

        num_pieces = len(self.piece_hashes)
        block_size = min(self.piece_length, Block.size)
        for piece_index in range(num_pieces):
            piece_length = self.piece_length
            if piece_index == num_pieces - 1:
                piece_length = self.last_piece_length

            self.pieces.append(Piece(piece_index, piece_length, block_size))

    @property
    def multi_file(self) -> bool:
        """
        Returns True if this is a torrent with multiple files.
        """
        return "files" in self.meta_info["info"]

    @property
    def announce_urls(self) -> List[List[str]]:
        """
        The announce URL of the tracker.
        According to BEP 0012 (http://bittorrent.org/beps/bep_0012.html),
        if announce-list is present, it is used instead of announce.
        :return: a list of announce URLs for the tracker
        """
        if "announce-list" in self.meta_info:
            return [[x.decode("UTF-8") for x in url_list]
                    for url_list in self.meta_info["announce-list"]]
        return [[_get_and_decode(self.meta_info, "announce")]]

    @property
    def comment(self) -> str:
        """
        :return: the torrent's comment
        """
        return _get_and_decode(self.meta_info, "comment")

    @property
    def created_by(self) -> Optional[str]:
        """
        :return: the torrent's creation program
        """
        return _get_and_decode(self.meta_info, "created by")

    @property
    def creation_date(self) -> Optional[int]:
        """
        :return: the torrent's creation date
        """
        if "creation date" in self.meta_info:
            return self.meta_info["creation date"]

    @property
    def private(self) -> bool:
        """
        :return: True if the torrent is private, False otherwise
        """
        return bool(self.meta_info["info"].get("private", False))

    @property
    def piece_length(self) -> int:
        """
        :return: Nominal length in bytes for each piece
        """
        return self.meta_info["info"]["piece length"]

    @property
    def last_piece_length(self) -> int:
        """
        :return: Length in bytes of the last piece of the torrent
        """
        return self.total_size - ((self.num_pieces - 1) * self.piece_length)

    @property
    def total_size(self) -> int:
        """
        :return: the total size of the file(s) in the torrent metainfo
        """
        return sum([f.size for f in self.files.values()])

    @property
    def present(self) -> int:
        """
        :return: the number of bytes present
        """
        return sum([piece.present for piece in self.pieces])

    @property
    def remaining(self) -> int:
        """
        :return: remaining number of bytes
        """
        return sum([piece.remaining for piece in self.pieces])

    @property
    def complete(self) -> bool:
        return self.remaining == 0

    @property
    def num_pieces(self) -> int:
        """
        :return: the total number of pieces in the torrent
        """
        return len(self.piece_hashes)

    @property
    def name(self) -> str:
        """
        :return: the torrent's name; either the single filename or the directory
        name.
        """
        return _get_and_decode(self.meta_info["info"], "name")
