"""The gateway daemon: poll enabled platforms, route each inbound message to a
per-(surface, chat_id) Assistant, send the reply back. Single-threaded; one bad
message never kills the loop."""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class GatewayDaemon:
    def __init__(self, *, factory, platforms, home, poll_timeout: int = 30,
                 sleep_fn=None, stop_event=None) -> None:
        self._factory = factory
        self._platforms = list(platforms)
        self._home = home
        self._poll_timeout = poll_timeout
        self._sleep = sleep_fn or (lambda s: threading.Event().wait(s))
        self._stop = stop_event or threading.Event()
        self._cache: dict[tuple[str, int], object] = {}

    def _handle(self, platform, inbound) -> None:
        key = (inbound.surface, inbound.chat_id)
        asst = self._cache.get(key)
        if asst is None:
            asst = self._factory.for_chat(inbound.surface, str(inbound.chat_id))
            self._cache[key] = asst
        result = asst.respond(inbound.text)
        platform.reply(inbound.chat_id, result.final_text)

    def run(self, max_iterations: int | None = None) -> None:
        i = 0
        while not self._stop.is_set():
            for platform in self._platforms:
                try:
                    batch = platform.poll(self._poll_timeout)
                except Exception:  # noqa: BLE001 - a poll error must not kill the daemon
                    logger.exception("gateway: poll failed for %s", getattr(platform, "name", "?"))
                    batch = []
                for inbound in batch:
                    try:
                        self._handle(platform, inbound)
                    except Exception:  # noqa: BLE001 - one bad message must not kill the loop
                        logger.exception("gateway: error handling message from %s", inbound.chat_id)
            i += 1
            if max_iterations is not None and i >= max_iterations:
                break
            if not self._stop.is_set():
                self._sleep(0)  # long-poll already blocks; no extra wait by default
