"""Where the task API is, and the one SigV4-signed HTTP call to it.

Configuration comes from files first. A power's `mcp.json` gets no `${VAR}` expansion from
the operator's shell (the Agent Plugins spec allows only `${PLUGIN_ROOT}` and
`${PLUGIN_DATA}`), and an IDE started from the Dock inherits no shell environment, so an
environment variable is the least reliable channel and is consulted last.

Only `GET` and `POST` are reachable from this module. Everything the four tools need fits in
those two, so a prompt-injected model can read tasks or start one the operator can see, and
nothing more.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError, ClientError

LOG = logging.getLogger("kiro_rca_power.client")

# A Lambda Function URL with `AWS_IAM` auth is signed for the `lambda` service, not
# `execute-api`.
SERVICE = "lambda"
ALLOWED_METHODS = frozenset({"GET", "POST"})
# The router's own timeout is 40 seconds; this outlasts it so the router's answer arrives.
_TIMEOUT_SECONDS = 60.0
_LAMBDA_URL_HOST = re.compile(r"^[a-z0-9]+\.lambda-url\.([a-z0-9-]+)\.on\.aws$")


class PowerError(RuntimeError):
    """Something the operator can fix, worded so the model can relay it verbatim."""


class ConfigError(PowerError):
    """No usable endpoint, profile or credentials."""


class ApiError(PowerError):
    """An error response from the task API, or from the Function URL's IAM check."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Settings:
    api_url: str
    region: str
    profile: str | None
    source: str


def rca_home() -> Path:
    return Path.home() / ".rca"


def _real(value: object) -> str | None:
    """A configured value, or `None` when it is absent, blank or an unexpanded `${...}`.

    A client that passes `mcp.json` env values through unexpanded hands the server the
    literal text `${RCA_API_URL}`, which would otherwise become the endpoint.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.startswith("${"):
        return None
    return value


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{path} is not readable JSON ({exc}); fix or remove it") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return data


def region_from_url(url: str) -> str | None:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    match = _LAMBDA_URL_HOST.match(host)
    return match.group(1) if match else None


def load_settings(
    *, home: Path | None = None, environ: Mapping[str, str] | None = None
) -> Settings:
    """Resolve the endpoint, signing region and profile.

    Each key takes the first value found in `power.json`, then `endpoint.json`, then the
    environment. The region is the exception: an explicit pin (`power.json` or
    `RCA_REGION`) wins, then the region the URL names, and only then `endpoint.json`'s,
    because `power.json` may point at a different deployment from the last `make deploy`.
    """
    home = home or rca_home()
    env = os.environ if environ is None else environ
    power = _read_json(home / "power.json")
    endpoint = _read_json(home / "endpoint.json")

    candidates = (
        (_real(power.get("apiUrl")), "~/.rca/power.json"),
        (_real(endpoint.get("apiUrl")), "~/.rca/endpoint.json"),
        (_real(env.get("RCA_API_URL")), "RCA_API_URL"),
    )
    api_url, source = next(((url, src) for url, src in candidates if url), (None, ""))
    if not api_url:
        raise ConfigError(
            "no API URL configured: run `make deploy` in the sample-remote-coding-agent "
            "repository (it writes ~/.rca/endpoint.json), or write "
            '~/.rca/power.json with {"apiUrl": "https://<id>.lambda-url.<region>.on.aws/"}'
        )
    if not api_url.startswith("https://"):
        raise ConfigError(f"the API URL from {source} must start with https://, got {api_url!r}")

    profile = _real(power.get("profile")) or _real(env.get("RCA_AWS_PROFILE"))
    region = (
        _real(power.get("region"))
        or _real(env.get("RCA_REGION"))
        or region_from_url(api_url)
        or _real(endpoint.get("region"))
        or ""
    )
    return Settings(api_url=api_url.rstrip("/"), region=region, profile=profile, source=source)


class ApiClient:
    """Signs and sends requests to the task API.

    Settings are resolved on the first call rather than at start-up, so the server starts
    (and lists its tools) on a machine with no deployment yet, and the missing-config
    message arrives as tool output the model can act on.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._session: boto3.Session | None = None
        self._http = httpx.Client(timeout=_TIMEOUT_SECONDS, transport=transport)

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = load_settings()
            LOG.info(
                "endpoint %s from %s region=%s profile=%s",
                self._settings.api_url,
                self._settings.source,
                self._settings.region or "(profile default)",
                self._settings.profile or "(default chain)",
            )
        return self._settings

    def _boto_session(self) -> boto3.Session:
        if self._session is None:
            try:
                self._session = boto3.Session(profile_name=self.settings.profile)
            except BotoCoreError as exc:
                raise ConfigError(f"AWS profile problem: {exc}") from exc
        return self._session

    def _signing_region(self) -> str:
        region = self.settings.region or self._boto_session().region_name
        if not region:
            raise ConfigError(
                'no signing region: add "region" to ~/.rca/power.json or set a default '
                "region on the AWS profile"
            )
        return region

    def _sign(self, method: str, url: str, body: bytes | None) -> dict[str, str]:
        headers = {"host": urllib.parse.urlsplit(url).netloc}
        if body is not None:
            headers["content-type"] = "application/json"
        request = AWSRequest(method=method, url=url, data=body, headers=headers)
        region = self._signing_region()
        try:
            credentials = self._boto_session().get_credentials()
            frozen = credentials.get_frozen_credentials() if credentials else None
        except (BotoCoreError, ClientError) as exc:
            raise ConfigError(
                f"could not load AWS credentials ({type(exc).__name__}: {exc}); refresh them "
                "(for example `aws sso login --profile <name>` or `aws login`)"
            ) from exc
        if frozen is None:
            raise ConfigError(
                "no AWS credentials found: configure a profile allowed "
                'lambda:InvokeFunctionUrl on the task API, and name it as "profile" in '
                "~/.rca/power.json if it is not your default"
            )
        SigV4Auth(frozen, SERVICE, region).add_auth(request)
        return dict(request.headers.items())

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, object] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        if method not in ALLOWED_METHODS:
            raise ValueError(f"{method} is not allowed; this client sends GET and POST only")
        url = f"{self.settings.api_url}{path}"
        params = {key: str(value) for key, value in (query or {}).items() if value is not None}
        if params:
            url = f"{url}?{urllib.parse.urlencode(sorted(params.items()))}"
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = self._sign(method, url, payload)

        started = time.monotonic()
        try:
            response = self._http.request(method, url, content=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise PowerError(f"could not reach the task API at {url}: {exc}") from exc
        LOG.info(
            "%s %s -> %s (%.2fs)", method, path, response.status_code, time.monotonic() - started
        )
        return _decode(response)

    def get(self, path: str, **query: object) -> Any:
        return self.request("GET", path, query=query)

    def post(self, path: str, body: Mapping[str, Any]) -> Any:
        return self.request("POST", path, body=body)


def _decode(response: httpx.Response) -> Any:
    try:
        data: Any = response.json() if response.content else {}
    except ValueError:
        data = None
    if response.status_code < 400:
        if data is None:
            raise PowerError(f"the task API answered {response.status_code} with a non-JSON body")
        return data

    if response.status_code == 403 and not (isinstance(data, dict) and "error" in data):
        raise ApiError(
            403,
            "Forbidden",
            "the Function URL refused the signed request. Check that the credentials are "
            "allowed lambda:InvokeFunctionUrl on the task API's router function, that the "
            "intended profile is in use, and that the URL is from the current deployment",
        )
    if isinstance(data, dict):
        code = str(data.get("error") or data.get("Message") or "HttpError")
        message = str(data.get("message") or data.get("Message") or response.reason_phrase)
    else:
        code, message = "HttpError", response.text[:500] or response.reason_phrase
    raise ApiError(response.status_code, code, message)
