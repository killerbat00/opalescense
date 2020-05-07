# -*- coding: utf-8 -*-

"""
Support for representing a .torrent file as a python class and
creating a Torrent class (or .torrent file) from a specified file or directory.
"""

import hashlib
import logging
import os
from collections import OrderedDict
from typing import NamedTuple, List, Optional

from btproto import Decoder, Encoder, DecodeError, EncodeError

logger = logging.getLogger(__name__)


class CreationError(Exception):
    """
    Raised when we encounter problems creating a torrent.
    """


class FileItem(NamedTuple):
    """
    An individual file within a torrent.
    """
    path: str
    size: int


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
    :raises:             CreationError
    """
    min_req_keys: List[bytes] = [b"info", b"announce"]
    min_info_req_keys: List[bytes] = [b"piece length", b"pieces", b"name"]
    min_files_req_keys: List[bytes] = [b"length", b"path"]

    logger.debug(
        "Validating torrent metainfo dictionary {d}".format(d=decoded_dict))

    dict_keys: List = list(decoded_dict.keys())

    if not dict_keys:
        logger.error("No valid keys in dictionary.")
        raise CreationError

    for key in min_req_keys:
        if key not in dict_keys:
            logger.error(f"Required key not found: {key}")
            raise CreationError

    info_keys: List = list(decoded_dict[b"info"].keys())

    if not info_keys:
        logger.error("No valid keys in info dictionary.")
        raise CreationError

    for key in min_info_req_keys:
        if key not in info_keys:
            logger.error(f"Required key not found: {key}")
            raise CreationError

    if len(decoded_dict[b"info"][b"pieces"]) % 20 != 0:
        logger.error("Piece length not a multiple of 20.")
        raise CreationError

    multiple_files: bool = b"files" in info_keys

    if multiple_files:
        file_list = decoded_dict[b"info"][b"files"]

        if not file_list:
            logger.error("No file list.")
            raise CreationError

        for f in file_list:
            for key in min_files_req_keys:
                if key not in f.keys():
                    logger.error(f"Required key not found: {key}")
                    raise CreationError
    else:
        if b"length" not in info_keys:
            logger.error("Required key not found: b'length'")
            raise CreationError

    # we made it!
    return True


class MetaInfoFile:
    """
    Represents the metainfo for a torrent. Doesn't include any download state.

    Unsupported metainfo keys:
        encoding
    """

    def __init__(self):
        self.files: List[FileItem] = []
        self.meta_info: Optional[OrderedDict] = None
        self.info_hash: bytes = None
        self.pieces: List[bytes] = []

    def __str__(self):
        return f"{self.name}:{self.info_hash}"

    def __repr__(self):
        return f"<MetaInfoFile: {self.name}:{self.info_hash}"

    @classmethod
    def from_file(cls, filename: str) -> "MetaInfoFile":
        """
        Class method to create a torrent object from a .torrent metainfo file

        :param filename: path to .torrent file
        :raises CreationError:
        :return: Torrent instance
        """
        logger.debug(f"Creating a torrent from {filename}")
        torrent: MetaInfoFile = cls()

        if not os.path.exists(filename):
            logger.error(f"Path does not exist {filename}")
            raise CreationError

        try:
            with open(filename, 'rb') as f:
                data: bytes = f.read()
                torrent.meta_info = Decoder(data).decode()

            if torrent.meta_info and isinstance(torrent.meta_info, OrderedDict):
                _validate_torrent_dict(torrent.meta_info)
                info: bytes = Encoder(torrent.meta_info[b"info"]).encode()
                torrent.info_hash = hashlib.sha1(info).digest()
                torrent._gather_files()
                torrent.pieces = list(_pc(torrent.meta_info[b"info"][b"pieces"]))
            else:
                logger.error(f"Unable to create empty MetaInfoFile.")
                raise CreationError

        except (EncodeError, DecodeError, IOError) as e:
            logger.error(f"Encountered error creating MetaInfoFile.")
            logger.info(e, exc_info=True)
            raise CreationError from e

        return torrent

    @classmethod
    def from_path(cls, path: str, trackers: list, *,
                  comment: str = "", piece_size: int = 32768,
                  private: bool = False) -> "MetaInfoFile":
        """
        Class method to create a torrent object from a specified filepath. The path can be
        a single file or a directory

        :param path:
        :param trackers:
        :param comment:
        :param piece_size:
        :param private:
        :raises CreationError:
        """
        raise NotImplementedError

    def _collect_pieces(self):
        """
        The real workhorse of torrent creation.
        Reads through all specified files, breaking them into piece_length chunks and storing their 20byte sha1
        digest into the pieces list
        :raises CreationError:
        """
        raise NotImplementedError

    def to_file(self, output_filename: str):
        """
        Writes the torrent metainfo dictionary back to a .torrent file

        :param output_filename: The output filename of the torrent
        :raises CreationError:
        """
        logger.debug(f"Writing .torrent file: {output_filename}")

        if not output_filename:
            logger.error("No output filename provided.")
            raise CreationError

        with open(output_filename, 'wb+') as f:
            try:
                data: bytes = Encoder(self.meta_info).encode()
                f.write(data)
            except EncodeError as ee:
                logger.error("Unable to write metainfo file {output_filename}")
                logger.info(ee, exc_info=True)
                raise CreationError from ee

    def _gather_files(self):
        """
        Gathers the files located in the torrent
        """
        # TODO: filepaths are a list containing string elements
        if self.multi_file:
            for f in self.meta_info[b"info"][b"files"]:
                path = None
                if isinstance(f[b"path"], list):
                    path = f[b"path"][0].decode("UTF-8")
                elif isinstance(f[b"path"], bytes):
                    path = f[b"path"].decode("UTF-8")
                self.files.append([FileItem(path, f[b"length"])])

        else:
            self.files.append(
                FileItem(self.meta_info[b"info"][b"name"].decode("UTF-8"),
                         self.meta_info[b"info"][b"length"]))

    @property
    def multi_file(self) -> bool:
        """
        TODO: proper handling of multi-file torrents.
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
        return b"files" in self.meta_info[b"info"]

    @property
    def announce_urls(self) -> List[str]:
        """
        The announce URL of the tracker.
        According to BEP 0012 (http://bittorrent.org/beps/bep_0012.html),
        if announce-list is present, it is used instead of announce.
        :return: a list of announce URLs for the tracker
        """
        urls: List[str] = []
        if b"announce-list" in self.meta_info:
            # announce list is a list of lists of strings
            for url_list in self.meta_info[b"announce-list"]:
                inner_list: List[str] = []
                for url in url_list:
                    inner_list.append(url.decode("UTF-8"))
                urls += inner_list
        else:
            urls.append(self.meta_info[b"announce"].decode("UTF-8"))
        return urls

    @property
    def comment(self) -> Optional[str]:
        """
        :return: the torrent's comment
        """
        if b"comment" in self.meta_info:
            return self.meta_info[b"comment"].decode("UTF-8")

    @property
    def created_by(self) -> Optional[str]:
        """
        :return: the torrent's creation program
        """
        if b"created by" in self.meta_info:
            return self.meta_info[b"created by"].decode("UTF-8")

    @property
    def creation_date(self) -> Optional[int]:
        """
        :return: the torrent's creation date
        """
        if b"creation date" in self.meta_info:
            return self.meta_info[b"creation date"]

    @property
    def private(self) -> bool:
        """
        :return: True if the torrent is private, False otherwise
        """
        return bool(self.meta_info[b"info"].get(b"private", False))

    @property
    def piece_length(self) -> int:
        """
        :return: Length in bytes for each piece
        """
        return self.meta_info[b"info"][b"piece length"]

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
        return sum([f.size for f in self.files])

    @property
    def num_pieces(self) -> int:
        """
        :return: the total number of pieces in the torrent
        """
        return len(self.pieces)

    @property
    def name(self) -> str:
        """
        :return: the torrent's name; either the single filename or the directory
        name.
        """
        return self.meta_info[b"info"][b"name"].decode("UTF-8")
