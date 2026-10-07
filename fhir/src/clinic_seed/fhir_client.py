import json
import time
import urllib.error
import urllib.request

JSON = "application/fhir+json"


class FhirError(RuntimeError):
    pass


class FhirClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}/{path}", data=data, method=method, headers={"Accept": JSON, "Content-Type": JSON}
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise FhirError(f"{method} {path} failed with {error.code}: {error.read().decode()[:2000]}") from error

    def wait_until_ready(self, timeout_seconds: float = 300) -> None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                self._request("GET", "metadata?_summary=true")
                return
            except (FhirError, OSError):
                if time.monotonic() > deadline:
                    raise FhirError(f"HAPI at {self.base_url} did not become ready in {timeout_seconds}s") from None
                time.sleep(2)

    def create_if_absent(self, resources: list[tuple[dict, str]], chunk_size: int = 100) -> None:
        """Create each resource unless one with the same identifier exists.

        Each item is (resource, "system|value"). Existing resources are never touched, so a
        re-run cannot reset a Slot a caller has since booked.
        """
        for start in range(0, len(resources), chunk_size):
            entries = [
                {"resource": resource, "request": {"method": "POST", "url": resource["resourceType"], "ifNoneExist": f"identifier={token}"}}
                for resource, token in resources[start : start + chunk_size]
            ]
            result = self._request("POST", "", {"resourceType": "Bundle", "type": "transaction", "entry": entries})
            for entry in result["entry"]:
                status = entry["response"]["status"]
                if not status.startswith(("200", "201")):
                    raise FhirError(f"Transaction entry failed: {entry['response']}")

    def ids_by_identifier(self, resource_type: str, system: str) -> dict[str, str]:
        """Map identifier value to resource id for every resource carrying that identifier system."""
        found: dict[str, str] = {}
        bundle = self._request("GET", f"{resource_type}?identifier={system}|&_count=200")
        while True:
            for entry in bundle.get("entry", []):
                resource = entry["resource"]
                for identifier in resource["identifier"]:
                    if identifier["system"] == system:
                        found[identifier["value"]] = resource["id"]
            next_url = next((link["url"] for link in bundle.get("link", []) if link["relation"] == "next"), None)
            if next_url is None:
                return found
            bundle = self._request("GET", next_url.removeprefix(self.base_url + "/"))
