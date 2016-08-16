# -*- coding: utf-8 -*-

"""
Provides support for decoding a bencoded string into a python OrderedDict,
bencoding a decoded OrderedDict, and pretty printing said OrderedDict.

author: brian houston morrow

public:
    bdecode()
    bencode()
    pretty_print()
"""
import logging

from collections import OrderedDict
from io import BytesIO

DICT_START = b'd'
DICT_END = b'e'
LIST_START = b'l'
LIST_END = b'e'
NUM_START = b'i'
NUM_END = b'e'
DIVIDER = b':'
DIGITS = [b'0', b'1', b'2', b'3', b'4', b'5', b'6', b'7', b'8', b'9']
VALID_CHARS = [DICT_START, DICT_END, LIST_START, NUM_START, DIVIDER] + DIGITS

logger = logging.getLogger()


class DecodeError(Exception):
    """
    Raised when there's an issue decoding a bencoded object.
    """
    pass


class EncodeError(Exception):
    """
    Raised when there's an issue bencoding an object.
    """
    pass


def bdecode(bencoded_data: bytes) -> OrderedDict:
    """
    Decodes a bencoded bytestring, returning an OrderedDict.
    :param bencoded_data: bencoded data to decode
    :return:              decoded torrent info as a python object
    :raises:              DecodeError
    """
    return _decode(BytesIO(bencoded_data))


def bencode(decoded_data: OrderedDict) -> str:
    """
    Bencodes an OrderedDict and returns the bencoded string.
    :param decoded_data: python object to bencode
    :return:             bencoded string
    :raises:             EncodeError
    """
    return _encode(decoded_data)


def pretty_print(bdecoded_obj: OrderedDict) -> str:
    """
    Prints a nicely formatted representation of a decoded torrent's python object
    :param bdecoded_obj: object to print
    """
    return pp_dict(bdecoded_obj)


def _decode(data_buffer: BytesIO) -> [OrderedDict, list, str, int]:
    """
    Recursively decodes a BytesIO buffer of bencoded data
    :param data_buffer: BytesIO buffer of bencoded data to decode
    :return:            torrent info decoded into a python object
    :raises:            DecodeError
    """
    char = data_buffer.read(1)

    if not char:
        return
    if char == DICT_END:
        return
    elif char == NUM_START:
        return _decode_int(data_buffer)
    elif char in DIGITS:
        return _decode_str(data_buffer)
    elif char == DICT_START:
        decoded_dict = OrderedDict()
        keys = []
        while True:
            key = _decode(data_buffer)
            if not key:
                break
            val = _decode(data_buffer)
            keys.append(key)
            decoded_dict.setdefault(key, val)
        if keys != sorted(keys):
            logger.error("Unable to decode bencoded dictionary. Keys are not sorted.")
            raise DecodeError
        return decoded_dict
    elif char == LIST_START:
        decoded_list = []
        while True:
            item = _decode(data_buffer)
            if not item:
                break
            decoded_list.append(item)
        return decoded_list
    else:
        logger.error("Unable to bdecode stream. {char} is invalid bencoded type of value.".format(char=char))
        raise DecodeError


def _decode_int(data_buffer: BytesIO) -> int:
    """
    decodes a bencoded integer from a BytesIO buffer.
    :param data_buffer: BytesIO object being parsed
    :return:            decoded integer
    :raises:            DecodeError
    """
    data_buffer.seek(-1, 1)
    char = data_buffer.read(1)
    if char != NUM_START:
        logger.error(
            "Error while parsing integer. Found {wrong}, expected {right}.".format(wrong=char, right=NUM_START))
        raise DecodeError
    return _parse_num(data_buffer, delimiter=NUM_END)


def _decode_str(data_buffer: BytesIO) -> str:
    """
    decodes a bencoded string from a BytesIO buffer.
    :param data_buffer: BytesIO object being parsed
    :return:            decoded string
    :raises:            DecodeError
    """
    data_buffer.seek(-1, 1)
    string_len = _parse_num(data_buffer, delimiter=DIVIDER)
    string_val = data_buffer.read(string_len).decode('ISO-8859-1')

    if len(string_val) != string_len:
        logger.error("Unable to read specified string length {length}".format(length=string_len))
        raise DecodeError
    return string_val


def _parse_num(data_buffer: BytesIO, delimiter: bytes) -> int:
    """
    parses an bencoded integer up to specified delimiter from a BytesIO buffer.
    :param data_buffer: BytesIO object being parsed
    :param delimiter:   delimiter do indicate the end of the number
    :return:            decoded number
    :raises:            DecodeError
    """
    parsed_num = bytes()
    while True:
        char = data_buffer.read(1)
        if char not in DIGITS or char == '':
            if char != delimiter:
                logger.error("Invalid character while parsing integer.\
                               Found {wrong}, expected {right}".format(wrong=char, right=delimiter))
                raise DecodeError
            else:
                break
        parsed_num += char
    return int(parsed_num.decode('ISO-8859-1'))


# --- encoding
def _encode(obj: [dict, list, str, int]) -> str:
    """
    Recursively bencodes an OrderedDict
    :param obj:     object to decode
    :return:        bencoded string
    :raises:        EncodeError
    """
    if isinstance(obj, dict):
        contents = DICT_START.decode("ISO-8859-1")
        for k, v in obj.items():
            contents += _encode_str(k)
            contents += _encode(v)
        contents += DICT_END.decode("ISO-8859-1")
        return contents
    elif isinstance(obj, list):
        contents = LIST_START.decode("ISO-8859-1")
        for item in obj:
            contents += _encode(item)
        contents += LIST_END.decode("ISO-8859-1")
        return contents
    elif isinstance(obj, str):
        return _encode_str(obj)
    elif isinstance(obj, int):
        return _encode_int(obj)
    else:
        logger.error("Unexpected object found {obj}".format(obj=obj))
        raise EncodeError


def _encode_int(int_obj: int) -> str:
    """
    bencodes an integer.
    :param int_obj: integer to bencode
    :return:        bencoded string of the specified integer
    """
    return "{start}{num}{end}".format(start=NUM_START.decode("ISO-8859-1"),
                                      num=int_obj,
                                      end=NUM_END.decode("ISO-8859-1"))


def _encode_str(string_obj: str) -> str:
    """
    bencode a string
    :param string_obj: string to bencode
    :return:           bencoded string of the specified string
    """
    return "{length}{div}{str}".format(length=len(string_obj),
                                       div=DIVIDER.decode("ISO-8859-1"),
                                       str=string_obj)


def pp_list(decoded_list: list, lvl: int = 0) -> str:
    """
    Recursively prints items in a list inside a torrent object
    mutually recursive with pp_dict
    :param decoded_list: the decoded list
    :param lvl:          current recursion level (used for indentation)
    :return:            pretty-printed list
    """
    str_ = ""
    for itm in decoded_list:
        if isinstance(itm, OrderedDict):
            str_ += pp_dict(itm, lvl)
        elif isinstance(itm, list):
            str_ += pp_list(itm, lvl)
        elif isinstance(itm, str) or isinstance(itm, int):
            str_ += "{pad}{val}".format(pad="\t" * lvl, val=itm)
    return str_


def pp_dict(decoded_dict: OrderedDict, lvl: int = 0) -> str:
    """
    Recursively prints keys and values from an OrderedDict representing a torrent
    mutually recursive with pp_list
    :param decoded_dict: dict to print
    :param lvl:          current recursion level (used for indentation)
    :return:            pretty-printed dictionary
    """
    str_ = ""
    for k, v in decoded_dict.items():
        str_ += "{pad}{val}\n".format(pad="\t" * lvl, val=k)
        if isinstance(v, dict):
            str_ += pp_dict(v, lvl=lvl + 1)
        elif isinstance(v, list):
            str_ += pp_list(v, lvl=lvl + 1)
        elif isinstance(v, str) or isinstance(v, int):
            str_ += "{pad}{val}\n".format(pad="\t" * (lvl + 1), val=v)
    return str_
