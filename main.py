# !/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Opalescence is a simple torrent client.
"""

import argparse
import asyncio
import logging
import logging.config
import os
import unittest

from opalescence.btlib.client import Client
from opalescence.btlib.torrent import Torrent

_LoggingConfig = {
    "version": 1,
    "formatters": {
        "basic": {
            "format": "%(asctime)s : %(name)s : [%(levelname)s] %(message)s"
        }
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "level": "DEBUG",
            "formatter": "basic",
            "stream": "ext://sys.stdout"

        }
    },
    "loggers": {
        "opalescence": {
            "level": "DEBUG",
            "handlers": ["stdout"]
        }
    },
    "root": {
        "level": "DEBUG",
        "handlers": ["stdout"]
    }
}


def create_logger():
    """
    Creates and configures the application's root logger.
    Configuration is stored in a dictionary
    Configuration is pulled from config/logging.ini
    """
    logging.config.dictConfig(_LoggingConfig)


def create_argparser() -> argparse.ArgumentParser:
    """
    Initializes the root argument parser and all relevant subparsers for supported commands.
    :return:    argparse.ArgumentParser instance that's ready to make things happen
    """
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    test_parser = subparsers.add_parser("test", help="Run the test suite")
    test_parser.set_defaults(func=run_tests)
    download_parser = subparsers.add_parser("download", help="Download a .torrent file")
    download_parser.add_argument('torrent_file')
    download_parser.add_argument('destination')
    download_parser.set_defaults(func=download_file)
    return parser


def main():
    """
    Main entry-point into Opalescence.
    """
    create_logger()
    logging.info("Initializing argument parser and subparsers")
    argparser = create_argparser()

    try:
        args = argparser.parse_args()
        args.func(args)
    except AttributeError:
        logging.debug("Program invoked with no arguments")
        argparser.print_help()


def run_tests(_) -> None:
    """
    Runs the test suite found in the tests/ directory
    :param _: unused
    """
    logging.debug("Running the test suite")

    loader = unittest.defaultTestLoader
    runner = unittest.TextTestRunner()
    suite = loader.discover(os.path.abspath(os.path.join(os.path.dirname(__file__), "tests")))
    runner.run(suite)


def download_file(file_path) -> None:
    """
    Downloads a .torrent file
    :param file_path: .torrent filepath argparse.Namespace object
    """
    logging.debug(f"Downloading {file_path}")
    logging.debug(f"Downloading {file_path.torrent_file}\n"
                  f"to {file_path.destination}")

    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    torrent = Torrent.from_file(file_path.torrent_file)
    client = Client()
    client.download(torrent)

    try:
        loop.run_forever()
        # loop.run_until_complete(task)
    except asyncio.CancelledError:
        logging.warning("Event loop was cancelled")
    finally:
        loop.close()


if __name__ == '__main__':
    main()
    logging.shutdown()
