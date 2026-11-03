"""Response headers every answer of the API carries."""

from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_SECURITY_HEADERS: Final = {
    # Answers hold health data and account data: no browser or proxy keeps a copy.
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


class SecurityHeadersMiddleware:
    """Add ``Cache-Control: no-store`` and ``nosniff`` to every HTTP response, errors included."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
