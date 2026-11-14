"""Whether the API can reach Supabase, as ``GET /v1/health`` reports it."""

import time
from collections.abc import Callable

from app.constants import HEALTH_CHECK_CACHE_SECONDS
from app.errors import ApiError
from app.supabase import SupabaseGateway


class SupabaseProbe:
    """Looks at Supabase at most once every ``HEALTH_CHECK_CACHE_SECONDS``.

    It asks for the project's public signing keys with the public key: the
    same request every token check makes, so it needs no secret, reads no
    data and says nothing a visitor could not learn. The answer is reused
    for a while, so a public endpoint polled often costs Supabase little.
    """

    def __init__(self, gateway: SupabaseGateway, clock: Callable[[], float] = time.monotonic) -> None:
        self._gateway = gateway
        self._clock = clock
        self._last: tuple[float, bool] | None = None

    async def reachable(self) -> bool:
        now = self._clock()
        if self._last is not None and now - self._last[0] < HEALTH_CHECK_CACHE_SECONDS:
            return self._last[1]
        try:
            await self._gateway.fetch_signing_keys()
            reached = True
        except ApiError:
            reached = False
        self._last = (now, reached)
        return reached
