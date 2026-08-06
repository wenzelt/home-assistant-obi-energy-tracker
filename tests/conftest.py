"""Test scaffolding for exercising HA-independent submodules in isolation.

Two problems are solved here:

1. ``custom_components/obi_energy_tracker/__init__.py`` imports Home
   Assistant, which is not installed in this lightweight test environment.
   The modules covered by these tests (``auth``, ``api``, ``const``,
   ``util``) do not import Home Assistant themselves, only each other via
   relative imports. To exercise them under real package semantics (so
   ``from .const import ...`` resolves) we register a namespace stand-in for
   the parent package in ``sys.modules`` *before* importing the submodules,
   which prevents Python from executing the real ``__init__.py``.

2. ``aioresponses`` (an aiohttp response-mocking library) is not compatible
   with the aiohttp version available in this environment (it constructs
   ``ClientResponse`` objects using a private constructor signature that
   changed). Rather than pin aiohttp down, ``FakeSession``/``FakeResponse``
   below are minimal test doubles that implement only the subset of the
   ``aiohttp.ClientSession`` / ``aiohttp.ClientResponse`` surface that
   ``auth.py`` and ``api.py`` actually use.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import types
from typing import Any

PACKAGE_NAME = "obi_energy_tracker"
PACKAGE_DIR = (
    Path(__file__).parents[1] / "custom_components" / "obi_energy_tracker"
)


def _install_namespace_package() -> None:
    if PACKAGE_NAME in sys.modules:
        return
    namespace = types.ModuleType(PACKAGE_NAME)
    namespace.__path__ = [str(PACKAGE_DIR)]
    sys.modules[PACKAGE_NAME] = namespace


def import_submodule(name: str):
    """Import ``obi_energy_tracker.<name>`` without executing its __init__.py."""
    _install_namespace_package()
    return importlib.import_module(f"{PACKAGE_NAME}.{name}")


class FakeCookie:
    def __init__(self, value: str) -> None:
        self.value = value


class FakeResponse:
    """Minimal stand-in for ``aiohttp.ClientResponse``."""

    def __init__(
        self,
        *,
        status: int = 200,
        json_payload: Any = None,
        text_body: str | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        url: str = "",
        history: list["FakeResponse"] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.cookies = {name: FakeCookie(value) for name, value in (cookies or {}).items()}
        self.url = url
        self.history = history or []
        self._json_payload = json_payload
        if text_body is not None:
            self._text_body = text_body
        elif json_payload is not None:
            self._text_body = json.dumps(json_payload)
        else:
            self._text_body = ""

    async def json(self, content_type: str | None = None) -> Any:
        if self._json_payload is None:
            raise ValueError("FakeResponse has no JSON payload configured")
        return self._json_payload

    async def text(self) -> str:
        return self._text_body

    def release(self) -> None:
        return None


class RecordedCall:
    def __init__(self, method: str, url: str, kwargs: dict[str, Any]) -> None:
        self.method = method
        self.url = url
        self.kwargs = kwargs


class FakeSession:
    """Minimal stand-in for ``aiohttp.ClientSession`` driven by a response queue."""

    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []
        self._queues: dict[tuple[str, str], list[Any]] = {}

    def queue_response(self, method: str, url: str, response: FakeResponse) -> None:
        self._queues.setdefault((method.upper(), url), []).append(response)

    def queue_error(self, method: str, url: str, error: BaseException) -> None:
        self._queues.setdefault((method.upper(), url), []).append(error)

    async def _handle(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(RecordedCall(method.upper(), str(url), kwargs))
        key = (method.upper(), str(url))
        queue = self._queues.get(key)
        if not queue:
            raise AssertionError(f"Unexpected {method} {url} (no response queued)")
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def get(self, url: str, **kwargs: Any):
        return self._handle("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any):
        return self._handle("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any):
        return self._handle(method, url, **kwargs)
