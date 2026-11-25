"""Test-only source probe, executed in the API container with its private settings.

Application commands use the existing registry resolver and A1 adapter. Raw reads
and the scan stimulus belong only to this fixture, never to a product API/worker.
No connection settings, keys or upstream exception bodies are printed.
"""

import json
import sys
import urllib.request
from urllib.parse import urlencode
from uuid import UUID


def main():
    sys.path.insert(0, "/opt/deploy")
    from app.contracts.interfaces import AdapterError
    from app.db import Database
    from app.registry.resolver import NodeResolver
    from app.syncthing_adapter import HttpAdapterFactory
    from app.syncthing_adapter.transport import HttpTransport
    from entrypoint import ownership, settings

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    if not ownership()["project"].startswith("scc-a4-test-"):
        raise ValueError("Probe requires a disposable project")
    args = json.loads(sys.stdin.buffer.read(16385))
    ids = [UUID(value) for value in args["ids"]]
    if len(ids) != 2 or ids[0] == ids[1]:
        raise ValueError("Probe requires two distinct registered nodes")
    configuration = settings()
    database = Database(configuration)
    resolver = NodeResolver(configuration, database)
    factory = HttpAdapterFactory(resolver)

    def fixture(index, path):
        transport = HttpTransport(resolver(ids[index]).connection)
        try:
            return json.loads(transport.request("GET", path).body)
        finally:
            transport.close()

    try:
        for index, letter in enumerate("ab"):
            if (
                resolver(ids[index]).connection.base_url.get_secret_value()
                != f"http://syncthing-{letter}:8384"
            ):
                raise ValueError("Registered UUID does not address the expected demo node")
        clients = [factory.for_node(identifier) for identifier in ids]
        systems = [client.read_system() for client in clients]
        op = args["op"]
        folder = args.get("folder", "scc-a4-shared")
        if folder not in {"scc-a4-shared", "scc-a4-control"}:
            raise ValueError("Probe requires a demo folder")
        if op == "snapshot":
            result = [fixture(index, "/rest/config/folders") for index in range(2)]
        elif op == "pause":
            clients[0].set_paused(folder, args["paused"])
            result = {"acknowledged": True}  # Observation is a separate operation.
        elif op == "state":
            try:
                runtime = clients[0].read_runtime(folder).model_dump(mode="json")
            except AdapterError as error:
                if error.code.value not in {"invalid_response", "not_found"}:
                    raise
                runtime = {"available": False, "error_code": error.code.value}
            result = {
                "paused": clients[0].read_folder(folder).paused,
                "requires_restart": clients[0].restart_required(),
                "runtime": runtime,
                "identities": [(s.device_id, s.start_time_raw) for s in systems],
            }
        elif op == "scan":
            # Syncthing CLI 2.1.7 has no scan command. This bounded fixture-only
            # POST indexes the newly written file; it does not implement control.
            connection = resolver(ids[1]).connection
            request = urllib.request.Request(
                connection.base_url.get_secret_value()
                + "/rest/db/scan?"
                + urlencode({"folder": folder}),
                data=b"",
                headers={"X-API-Key": connection.api_key.get_secret_value()},
                method="POST",
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=10) as response:
                if response.code != 200 or len(response.read(4097)) > 4096:
                    raise ValueError("Fixture scan did not complete")
            local = fixture(
                1, "/rest/db/file?" + urlencode({"folder": folder, "file": args["name"]})
            )["local"]
            result = {key: local[key] for key in ("name", "size", "deleted")}
        else:
            raise ValueError("Unsupported probe operation")
        print(json.dumps(result), flush=True)
    finally:
        factory.close()
        database.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Disposable source probe failed", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
