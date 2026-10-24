"""The memory service's place in the ecosystem: its health door, its claim on a port and its registration.

It is for the three things every Sovereign service does at start: claim its address through the shared
service supervisor, answer GET /health on it, and register in the shared agent registry with a heartbeat.
It takes the settings it is given by the environment (the address, the Redis address and the Sovereign
root) and reads the shared libraries from the Sovereign shared folder; it never copies them and never
writes into the Sovereign tree except through the registry's own registration. It never invents an
address or a default: a missing setting switches that part off and the health report says so in words.
"""

import asyncio
import importlib
import json
import logging
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AGENT_ID = "agent_sovereign_memory"
HTTP_HOST_VARIABLE = "SOVEREIGN_MEMORY_HTTP_HOST"
HTTP_PORT_VARIABLE = "SOVEREIGN_MEMORY_HTTP_PORT"
REDIS_HOST_VARIABLE = "REDIS_HOST"
REDIS_PORT_VARIABLE = "REDIS_PORT"
SOVEREIGN_ROOT_VARIABLE = "SOVEREIGN_ROOT"
AGENT_ROOT_VARIABLE = "AGENT_ROOT"
ENTRYPOINT_NAME = "sovereign_memory_main.py"
MACHINE_DECLARATION = "machine.json"
CAPABILITIES = (
    "memory.capture",
    "memory.retrieve",
    "memory.context",
    "memory.forget",
    "memory.model_steps",
)
ENDPOINTS = (
    {
        "path": "/health",
        "method": "GET",
        "description": "200 with the service's state in words once it answers; the memory itself is on its socket",
    },
)


class EcosystemError(ValueError):
    """An ecosystem setting is wrong or half given; the message names the setting."""


@dataclass(frozen=True)
class Address:
    """Where the health door listens: a host and a port, both given by the environment."""

    host: str
    port: int


def address_from(environment: Mapping[str, str]) -> Address | None:
    """Read the health door's address from the environment, or None when neither part is given.

    It takes the environment mapping. It returns the address when both the host and the port are
    set, None when neither is, and raises EcosystemError naming the missing or malformed one when
    only one is given or the port is not a whole number from 1 to 65535. It never supplies a default.
    """
    host, port = environment.get(HTTP_HOST_VARIABLE), environment.get(HTTP_PORT_VARIABLE)
    if not host and not port:
        return None
    if not host or not port:
        raise EcosystemError(
            f"{HTTP_HOST_VARIABLE} and {HTTP_PORT_VARIABLE} must be given together"
        )
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise EcosystemError(f"{HTTP_PORT_VARIABLE} must be a whole number from 1 to 65535")
    return Address(host, int(port))


def shared_module(name: str, sovereign_root: str) -> Any:
    """Import one module of the Sovereign shared folder, from the folder SOVEREIGN_ROOT names.

    It takes the module name and the Sovereign root. It puts the shared folder on the import path
    once and returns the module. It raises EcosystemError saying the folder or module is missing;
    it never copies the module.
    """
    shared = Path(sovereign_root) / "shared"
    if not shared.is_dir():
        raise EcosystemError(f"the Sovereign shared folder is not at {shared}")
    if str(shared) not in sys.path:
        sys.path.append(str(shared))
    try:
        return importlib.import_module(name)
    except ImportError as error:
        raise EcosystemError(f"the shared module {name} cannot be imported: {error}") from error


def _http_reply(code: int, reason: str, body: dict[str, Any]) -> bytes:
    """Build one complete HTTP/1.1 reply carrying a JSON body and closing the connection."""
    payload = json.dumps(body).encode("utf-8")
    head = (
        f"HTTP/1.1 {code} {reason}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode("ascii") + payload


class Presence:
    """The service's address, health door and registration, started and stopped as one."""

    def __init__(
        self,
        environment: Mapping[str, str],
        report: Callable[[], dict[str, Any]],
    ) -> None:
        """Keep the environment and the function that says how the service is, and read the address.

        report is called on every health request and returns a plain dictionary describing the
        service (it must not block). Raises EcosystemError for a half-given or malformed address.
        """
        self.environment = environment
        self.report = report
        self.address = address_from(environment)
        self.server: asyncio.Server | None = None
        self.registry: Any = None
        self.registration_note = "registration has not been tried"
        self.heartbeat_task: asyncio.Task[None] | None = None
        self.errors: tuple[type[BaseException], ...] = (EcosystemError, OSError, ValueError)

    def _root(self) -> str | None:
        """Return the Sovereign tree: SOVEREIGN_ROOT, else the one machine.json declares, else None.

        machine.json sits at the protocol root, two folders above this project's root (the agent
        root the environment gives). It never guesses a path: when neither source answers it is None.
        """
        given = self.environment.get(SOVEREIGN_ROOT_VARIABLE)
        if given:
            return given
        agent = self.environment.get(AGENT_ROOT_VARIABLE)
        if not agent:
            return None
        declaration = Path(agent).resolve().parents[1] / MACHINE_DECLARATION
        try:
            return json.loads(declaration.read_text(encoding="utf-8"))["sovereign_root"]["value"]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    async def start(self) -> None:
        """Claim the port, begin answering /health and register, as far as the settings allow.

        With no address given nothing is listened on and the reply of report says so. A port held by
        a stranger raises EcosystemError naming the holder, and nothing is stopped; a previous copy
        of this same entry file is superseded by the supervisor. Registration is tried once here
        and again at every heartbeat; its failure is logged and never stops the service.
        """
        if self.address is not None:
            await self._take_port_and_listen(self.address)
        self.heartbeat_task = asyncio.create_task(self._register_and_beat())

    async def _take_port_and_listen(self, address: Address) -> None:
        """Make the address free for this service and bind the health door on it."""
        root, agent = self._root(), self.environment.get(AGENT_ROOT_VARIABLE)
        if root is None or agent is None:
            raise EcosystemError(
                f"{SOVEREIGN_ROOT_VARIABLE} and {AGENT_ROOT_VARIABLE} must be given to claim a port"
            )
        supervisor = shared_module("shared_service_supervisor", root)
        entrypoint = Path(agent) / ENTRYPOINT_NAME
        try:
            await asyncio.to_thread(supervisor.take_port, address.host, address.port, entrypoint)
        except supervisor.AddressHeldByStranger as error:
            raise EcosystemError(str(error)) from error
        self.server = await asyncio.start_server(self._answer, address.host, address.port)

    async def _answer(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Answer one request: GET /health with the state, 404 for another path, 405 for another method."""
        try:
            line = (await asyncio.wait_for(reader.readline(), timeout=5)).decode("latin-1")
            parts = line.split()
            if len(parts) < 2:
                reply = _http_reply(400, "Bad Request", {"error": "not an HTTP request"})
            elif parts[0] != "GET":
                reply = _http_reply(405, "Method Not Allowed", {"error": "only GET /health"})
            elif parts[1].split("?", 1)[0] != "/health":
                reply = _http_reply(404, "Not Found", {"error": "only GET /health"})
            else:
                reply = _http_reply(
                    200,
                    "OK",
                    {**self.report(), "agent_id": AGENT_ID, "registration": self.registration_note},
                )
            writer.write(reply)
            await writer.drain()
        except (TimeoutError, ConnectionError):
            logger.warning("A health request ended before it was answered")
        finally:
            writer.close()

    def _register(self) -> str:
        """Register or refresh this service in the shared agent registry and return what happened.

        It needs the Sovereign root and the Redis address from the environment; without them it says
        which is missing. The first call registers the identity; later calls send the heartbeat.
        It raises nothing the caller must handle except what the registry itself raises.
        """
        root = self._root()
        host, port = (
            self.environment.get(REDIS_HOST_VARIABLE),
            self.environment.get(REDIS_PORT_VARIABLE),
        )
        if root is None or not host or not port or not port.isdigit():
            return f"not registered: {SOVEREIGN_ROOT_VARIABLE}, {REDIS_HOST_VARIABLE} and {REDIS_PORT_VARIABLE} are not all given"
        if self.registry is None:
            bus_module = shared_module("shared_redis_bus", root)
            registry_module = shared_module("shared_agent_registry", root)
            self.errors = (
                EcosystemError,
                OSError,
                ValueError,
                bus_module.RedisUnavailableError,
                shared_module("shared_schema_validator", root).SchemaValidationError,
            )
            bus = bus_module.RedisBus(host, int(port))
            self.registry = registry_module.AgentRegistry(bus)
            self.registry.register(
                {
                    "agent_id": AGENT_ID,
                    "name": "sovereign_memory",
                    "version": version("sovereign_memory"),
                    "description": "One persistent DuckDB memory for every agent and every model: capture, retrieve, context, forget and the language-model memory steps.",
                    "capabilities": list(CAPABILITIES),
                    "http_endpoints": [dict(endpoint) for endpoint in ENDPOINTS],
                }
            )
            return "registered in the shared agent registry"
        self.registry.heartbeat(AGENT_ID)
        return "registered; heartbeat sent"

    def _beat_seconds(self) -> float:
        """Return the registry's own heartbeat interval, read from its module.

        It needs the Sovereign root. It raises EcosystemError when the root is not given, the module
        cannot be imported or the module names no interval; it never supplies a number of its own.
        """
        root = self._root()
        if root is None:
            raise EcosystemError(f"{SOVEREIGN_ROOT_VARIABLE} is not given")
        module = shared_module("shared_agent_registry", root)
        try:
            return float(module._HEARTBEAT_INTERVAL_S)
        except (AttributeError, ValueError) as error:
            raise EcosystemError("the registry module names no heartbeat interval") from error

    async def _register_and_beat(self) -> None:
        """Register now and send a heartbeat every interval, noting each outcome, until cancelled."""
        try:
            interval = await asyncio.to_thread(self._beat_seconds)
        except EcosystemError as error:
            self.registration_note = f"not registered: {error}"
            logger.warning(self.registration_note)
            return
        while True:
            try:
                self.registration_note = await asyncio.to_thread(self._register)
            except self.errors as error:  # the registry may be down; the service must keep serving
                self.registration_note = f"registration failed: {type(error).__name__}: {error}"
                self.registry = None
                logger.warning(self.registration_note)
            await asyncio.sleep(interval)

    async def stop(self) -> None:
        """Stop the heartbeat, mark the service stopped in the registry and close the health door."""
        if self.heartbeat_task is not None:
            self.heartbeat_task.cancel()
            await asyncio.gather(self.heartbeat_task, return_exceptions=True)
        if self.registry is not None:
            try:
                await asyncio.to_thread(self.registry.deregister, AGENT_ID)
            except self.errors as error:
                logger.warning("Deregistering failed: %s: %s", type(error).__name__, error)
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


def environment_snapshot() -> dict[str, str]:
    """Return the process environment as a plain dictionary, the one place the runtime reads it."""
    return dict(os.environ)
