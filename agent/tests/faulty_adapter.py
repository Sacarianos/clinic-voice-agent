"""A pass-through to the real EHR adapter that asks it to inject a fault into chosen requests.

The adapter does the faulting, through its x-inject-fault header. This only decides which requests
carry the header, so a test can fail the first Book and let the retry through.
"""

import re
from dataclasses import dataclass

import httpx
from aiohttp import web

BOOK = r"POST /appointments$"
RESCHEDULE = r"POST /appointments/[^/]+/reschedule$"
CANCEL = r"POST /appointments/[^/]+/cancel$"
LIST_APPOINTMENTS = r"GET /appointments$"
VERIFY_PATIENT = r"POST /patients/verify$"
CALLBACK_REQUEST = r"POST /callback-requests$"


@dataclass
class _Injection:
    fault: str
    request: re.Pattern
    times: int


class FaultyAdapter:
    def __init__(self, adapter_url: str):
        self._adapter = httpx.AsyncClient(base_url=adapter_url, timeout=30)
        self._injections: list[_Injection] = []
        self._runner: web.AppRunner | None = None
        self.url = ""
        self.requests: list[str] = []  # "<METHOD> <path>", every request in order
        self.injected: list[str] = []  # "<fault> <METHOD> <path>", one per request that carried a fault

    def sent(self, request: str) -> int:
        """How many requests matched `request`, a pattern for "<METHOD> <path>"."""
        return sum(1 for sent in self.requests if re.search(request, sent))

    def inject(self, fault: str, *, into: str, times: int = 1) -> None:
        """Faults the next `times` requests matching `into`, a pattern for "<METHOD> <path>"."""
        self._injections.append(_Injection(fault, re.compile(into), times))

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", self._forward)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"

    async def close(self) -> None:
        await self._runner.cleanup()
        await self._adapter.aclose()

    async def _forward(self, request: web.Request) -> web.Response:
        headers = {"content-type": request.headers.get("content-type", "application/json")}
        self.requests.append(f"{request.method} {request.path}")
        fault = self._fault_for(f"{request.method} {request.path}")
        if fault:
            headers["x-inject-fault"] = fault
            self.injected.append(f"{fault} {request.method} {request.path}")
        answer = await self._adapter.request(
            request.method, request.path, params=request.query, content=await request.read(), headers=headers
        )
        return web.Response(status=answer.status_code, body=answer.content, content_type="application/json")

    def _fault_for(self, request: str) -> str | None:
        for injection in self._injections:
            if injection.times > 0 and injection.request.search(request):
                injection.times -= 1
                return injection.fault
        return None
