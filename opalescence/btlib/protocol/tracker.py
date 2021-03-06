# -*- coding: utf-8 -*-

"""
Support for communication with HTTP trackers.
"""

from __future__ import annotations

__all__ = ['TrackerTask']

import asyncio
import contextlib
import dataclasses
import functools
import http.client
import logging
import socket
import struct
import urllib
from collections import deque
from typing import Optional, List, Tuple
from urllib.parse import urlencode

from .bencode import Decode
from .errors import TrackerConnectionError, NoTrackersError
from .metainfo import MetaInfoFile
from .peer_info import PeerInfo

logger = logging.getLogger(__name__)

EVENT_STARTED = "started"
EVENT_COMPLETED = "completed"
EVENT_STOPPED = "stopped"


@dataclasses.dataclass
class TrackerParameters:
    info_hash: bytes
    peer_id: bytes
    port: int
    uploaded: int
    downloaded: int
    left: int
    compact: int
    event: str


async def http_request(url: str, params: TrackerParameters) -> TrackerResponse:
    """
    Makes the HTTP request to the tracker url with the given params.
    :param url: tracker URL
    :param params: announce parameters
    :raises TrackerConnectionError: if a non-200 response or failure response is
    received.
    :return: `TrackerResponse` object containing data returned by tracker.
    """
    url_info = _construct_url(url, params)
    if not url_info:
        raise TrackerConnectionError

    scheme, path, url = url_info[0], url_info[1], url_info[2]

    if scheme == "http":
        conn = http.client.HTTPConnection(path, timeout=5)
    else:
        conn = http.client.HTTPSConnection(path, timeout=5)

    with contextlib.closing(conn) as tracker_conn:
        await asyncio.get_running_loop().run_in_executor(
            None, functools.partial(tracker_conn.request, "GET", url)
        )
        resp = tracker_conn.getresponse()
        if resp.status != 200:
            raise TrackerConnectionError
        tracker_resp = TrackerResponse(Decode(resp.read()))
        if tracker_resp.failed:
            raise TrackerConnectionError
        return tracker_resp


def _construct_url(url: str, params: TrackerParameters) -> Optional[tuple[str, str, str]]:
    """
    Constructs a tracker URL with the given parameters.

    :param url: The URL from the metainfo file.
    :param params: The parameters to send to the tracker.
    :return: tuple of URL scheme, path, and full query parameter string
    """
    url = urllib.parse.urlparse(url)
    scheme = url.scheme

    if scheme not in ["http", "https"]:
        return

    if not (url.netloc or url.path):
        return

    query_params = urllib.parse.parse_qs(url.query)
    query_params.update(dataclasses.asdict(params))
    query_param_str = urllib.parse.urlencode(query_params)

    path = url.netloc
    if not path:
        path = url.path
    if not path:
        return

    result_url = url._replace(scheme="", netloc="", query=query_param_str).geturl()
    return scheme, path, result_url


class TrackerConnection:
    """
    Communication with the tracker.
    Does not currently support the announce-list extension from
    BEP 0012: http://bittorrent.org/beps/bep_0012.html. Instead, when one tracker
    disconnects or fails, or runs out of trackers, we hop round robin to the next tracker.
    Does not support the scrape convention.

    TODO: Allow multiple trackers to run concurrently?
    """
    DEFAULT_INTERVAL: int = 60  # 1 minute

    def __init__(self, local_info, meta_info: MetaInfoFile):
        self.client_info = local_info
        self.torrent = meta_info
        self.announce_urls = deque(
            set(url for tier in meta_info.announce_urls for url in tier))
        self.interval = self.DEFAULT_INTERVAL

    async def announce(self, event: str = "") -> TrackerResponse:
        """
        Makes an announce request to the tracker and returns the received peers.

        :raises TrackerConnectionError: if the tracker's HTTP code is not 200,
                                        we timed out making a request to the tracker,
                                        the tracker sent a failure, or we
                                        are unable to bdecode the tracker's response.
        :raises NoTrackersError:        if there are no tracker URls to query.
        :returns: `TrackerResponse` containing the tracker's response on success.
        """
        # TODO: respect proper order of announce urls according to BEP 0012.
        if len(self.announce_urls) == 0:
            raise NoTrackersError

        remaining = self.torrent.remaining
        if remaining == 0 and not event:
            event = EVENT_COMPLETED

        url = self.announce_urls.popleft()
        logger.info("Making %s announce to %s" % (event, url))
        params = TrackerParameters(self.torrent.info_hash,
                                   self.client_info.peer_id_bytes,
                                   self.client_info.port,
                                   0,
                                   self.torrent.present,
                                   remaining,
                                   1,
                                   event)

        try:
            decoded_data = await http_request(url, params)
            if decoded_data is None:
                raise TrackerConnectionError
        except Exception as e:
            logger.error("%s received in announce." % type(e).__name__)
            raise TrackerConnectionError from e

        if event != EVENT_COMPLETED and event != EVENT_STOPPED:
            self.interval = decoded_data.interval
            self.announce_urls.appendleft(url)
            return decoded_data

    async def cancel_announce(self) -> None:
        """
        Informs the tracker we are gracefully shutting down.
        """
        with contextlib.suppress(TrackerConnectionError, NoTrackersError):
            await self.announce(event=EVENT_STOPPED)

    async def completed(self) -> None:
        """
        Informs the tracker we have completed downloading this torrent
        """
        with contextlib.suppress(TrackerConnectionError, NoTrackersError):
            await self.announce(event=EVENT_COMPLETED)


class TrackerResponse:
    """
    TrackerResponse received from the tracker after an announce request.
    """

    def __init__(self, data: dict):
        self.data: dict = data
        self.failed: bool = "failure reason" in self.data

    @property
    def failure_reason(self) -> Optional[str]:
        """
        :return: the failure reason
        """
        if self.failed:
            return self.data.get("failure reason", b"Unknown").decode("UTF-8")

    @property
    def interval(self) -> int:
        """
        :return: the tracker's specified interval between announce requests
        """
        min_interval = self.data.get("min interval", None)
        if not min_interval:
            return self.data.get("interval", TrackerConnection.DEFAULT_INTERVAL)
        interval = self.data.get("interval", TrackerConnection.DEFAULT_INTERVAL)
        return min(min_interval, interval)

    @property
    def seeders(self) -> int:
        """
        :return: seeders, the number of peers with the entire file
        """
        return self.data.get("complete", 0)

    @property
    def leechers(self) -> int:
        """
        :return: leechers, the number of peers that are not seeders
        """
        return self.data.get("incomplete", 0)

    def get_peers(self) -> Optional[List[Tuple[str, int]]]:
        """
        :raises TrackerConnectionError:
        :return: the list of peers. The response can be given as a
        list of dictionaries about the peers, or a string
        encoding the ip address and ports for the peers
        """
        peers = self.data.get("peers")

        if not peers:
            return

        if isinstance(peers, bytes):
            split_peers = [peers[i:i + 6] for i in range(0, len(peers), 6)]
            p = [(socket.inet_ntoa(p[:4]), struct.unpack(">H", p[4:])[0]) for
                 p in split_peers]
            return p
        elif isinstance(peers, list):
            return [(p["ip"].decode("UTF-8"), int(p["port"])) for p in peers]
        else:
            raise TrackerConnectionError

    def get_peer_list(self) -> list[PeerInfo]:
        """
        :return: a list of `PeerInfo` objects of the returned peers.
        """
        peers = []
        peer_list = self.get_peers()
        for peer in peer_list:
            peers.append(PeerInfo(peer[0], peer[1]))
        return peers


class TrackerTask(TrackerConnection):
    def __init__(self, local_info, meta_info, peer_queue):
        super().__init__(local_info, meta_info)
        self._peer_queue: asyncio.Queue[PeerInfo] = peer_queue
        self._tracker_resp_queue: asyncio.Queue[TrackerResponse] = asyncio.Queue()
        self.task: Optional[asyncio.Task] = None

    def start(self):
        """
        Starts this task by scheduling a coroutine on the event loop.
        """
        if not self.task:
            self.task = asyncio.create_task(self._main())

    def stop(self):
        """
        Stops this task by cancelling the scheduled coroutine.
        """
        if self.task:
            self.task.cancel()

    async def _main(self):
        """
        Schedules and waits on the coroutines that send a recurring announce to a
        peer and populate the available peer queue with the response.
        """
        announce_task = asyncio.create_task(
            self._recurring_announce(self._tracker_resp_queue))
        receive_task = asyncio.create_task(
            self._receive_peers(self._tracker_resp_queue))

        try:
            await asyncio.gather(announce_task, receive_task)
        except Exception as exc:
            # exceptions raised by announce_task and receive_task
            # are handled here. We cancel the task here because
            # otherwise it'd end up as done. We don't really care
            # when the tracker task is done because torrent completion
            # will have happened and (should have) triggered cancellation
            # of this task.
            logger.error("%s received in TrackerTask" % type(exc).__name__)
            announce_task.cancel()
            receive_task.cancel()
            self.task.cancel()

    async def _recurring_announce(self, response_queue: asyncio.Queue[TrackerResponse]):
        """
        Responsible for making the recurring request for peers and placing
        the tracker's response into the given queue.

        :param response_queue: Queue to place the response into.
        :raises: All exceptions to the main task except for cancellation. This coroutine
                 doesn't need to handle itself being cancelled if it's scheduled as a
                 task.
        """
        if len(self.announce_urls) == 0:
            raise NoTrackersError

        event = EVENT_STARTED
        exc_to_raise = None

        while not self.task.cancelled():
            try:
                response_queue.put_nowait(await self.announce(event))
            except TrackerConnectionError:
                continue
            # if we're cancelled, try to make one last announce.
            except asyncio.CancelledError as exc:
                exc_to_raise = exc
                break

            event = ""  # don't reset until we've made the first successful announce
            await asyncio.sleep(self.interval)

        if self.torrent.complete:
            asyncio.create_task(self.completed())
        else:
            asyncio.create_task(self.cancel_announce())
        logger.info("Recurring announce _task ended.")
        if exc_to_raise:
            raise exc_to_raise

    async def _receive_peers(self, response_queue: asyncio.Queue[TrackerResponse]):
        """
        Listens to `TrackerResponse`s posted to the `response_queue` and populates
        the peer_queue with the peers returned.

        :param response_queue: Queue to read responses from.
        :raises: All exceptions to the main task except for cancellation. This coroutine
                 doesn't need to handle itself being cancelled if it's scheduled as a
                 task.
        """
        while not self.task.cancelled():
            response = await response_queue.get()

            logger.info("Adding more peers to queue.")

            if response:
                peers = [peer for peer in response.get_peer_list()
                         if peer != self.client_info]

                if len(peers) > self._peer_queue.qsize():
                    while not self._peer_queue.empty():
                        self._peer_queue.get_nowait()

                    for peer in peers:
                        self._peer_queue.put_nowait(peer)

            response_queue.task_done()
