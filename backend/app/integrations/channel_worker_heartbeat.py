"""独立 Worker 向 Channel Gateway 续租的轻量客户端。"""

from __future__ import annotations

import logging
from threading import Event, Lock, Thread

import httpx


logger = logging.getLogger(__name__)


class ChannelWorkerHeartbeatClient:
    def __init__(
        self,
        *,
        base_url: str,
        gateway_secret: str,
        worker_id: str,
        worker_kind: str,
        transport: str,
        interval_seconds: int,
        lease_seconds: int,
        timeout_seconds: int = 5,
        transport_client: httpx.BaseTransport | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.worker_kind = worker_kind
        self.transport = transport
        self.interval_seconds = interval_seconds
        self.lease_seconds = lease_seconds
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-Channel-Gateway-Key": gateway_secret},
            timeout=timeout_seconds,
            transport=transport_client,
        )
        self._stop = Event()
        self._lock = Lock()
        self._state = "STARTING"
        self._last_error_code: str | None = None
        self._thread: Thread | None = None

    def set_state(self, state: str, last_error_code: str | None = None) -> None:
        with self._lock:
            self._state = state
            self._last_error_code = last_error_code

    def beat(self, state: str | None = None) -> dict | None:
        with self._lock:
            current_state = state or self._state
            error_code = self._last_error_code
        try:
            response = self.client.post(
                "/api/channels/workers/heartbeat",
                json={
                    "worker_id": self.worker_id,
                    "worker_kind": self.worker_kind,
                    "transport": self.transport,
                    "state": current_state,
                    "last_error_code": error_code,
                    "lease_seconds": self.lease_seconds,
                },
            )
            response.raise_for_status()
            result = response.json()
            return result if isinstance(result, dict) else None
        except (httpx.HTTPError, ValueError):
            logger.warning("渠道 Worker 心跳上报失败。", exc_info=True)
            return None

    def start(self) -> None:
        if self._thread is not None:
            return
        self.beat("STARTING")
        self.set_state("ONLINE")
        self._thread = Thread(
            target=self._run,
            name=f"channel-heartbeat-{self.worker_kind.lower()}",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.beat()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self.interval_seconds + 1, 2))
        self.set_state("STOPPING")
        self.beat("STOPPING")
        self.client.close()
