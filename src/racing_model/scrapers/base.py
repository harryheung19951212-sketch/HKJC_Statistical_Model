from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from pathlib import Path


@dataclass(frozen=True)
class FetchResult:
    url: str
    fetched_at: str
    body: str


class PoliteHttpClient:
    def __init__(
        self,
        user_agent: str,
        delay_seconds: float = 2.0,
        raw_dir: Path | str = "data/raw",
    ) -> None:
        self.user_agent = user_agent
        self.delay_seconds = delay_seconds
        self.raw_dir = Path(raw_dir)
        self._last_request_at = 0.0

    def fetch(self, url: str) -> FetchResult:
        return self.fetch_with_encoding(url, "utf-8")

    def fetch_with_encoding(self, url: str, encoding: str) -> FetchResult:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        request = Request(url, headers={"User-Agent": self.user_agent})
        with urlopen(request, timeout=30) as response:
            raw = response.read()
        self._last_request_at = time.monotonic()
        result = FetchResult(
            url=url,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            body=raw.decode(encoding, errors="replace"),
        )
        self.save_raw(result)
        return result

    def save_raw(self, result: FetchResult) -> Path:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(char if char.isalnum() else "_" for char in result.url)[-160:]
        path = self.raw_dir / f"{result.fetched_at.replace(':', '-')}_{safe_name}.html"
        path.write_text(result.body, encoding="utf-8")
        return path
