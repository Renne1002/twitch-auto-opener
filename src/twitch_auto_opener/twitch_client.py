from __future__ import annotations

import json
import time
from dataclasses import dataclass

import requests
from requests import RequestException, Response


class TwitchApiError(RuntimeError):
    """Raised when Twitch API calls fail."""


@dataclass
class OAuthToken:
    access_token: str
    expires_at: float


class TwitchClient:
    def __init__(self, client_id: str, client_secret: str, timeout_seconds: int = 15) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout_seconds = timeout_seconds
        self._token: OAuthToken | None = None

    def _request(
        self,
        method: str,
        url: str,
        params: list[tuple[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        try:
            return requests.request(
                method=method,
                url=url,
                params=params,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except RequestException as exc:
            raise TwitchApiError(f"request failed: {exc}") from exc

    @staticmethod
    def _parse_json(response: Response) -> tuple[int, str, dict]:
        status_code = response.status_code
        body_text = response.text
        try:
            payload = json.loads(body_text) if body_text else {}
        except json.JSONDecodeError as exc:
            raise TwitchApiError(f"invalid json response: status={status_code}, body={body_text}") from exc
        return status_code, body_text, payload

    def _ensure_token(self) -> str:
        now = time.time()
        if self._token and (self._token.expires_at - 30) > now:
            return self._token.access_token

        params = [
            ("client_id", self._client_id),
            ("client_secret", self._client_secret),
            ("grant_type", "client_credentials"),
        ]
        response = self._request(method="POST", url="https://id.twitch.tv/oauth2/token", params=params)
        status_code, body_text, payload = self._parse_json(response)
        if status_code != 200:
            raise TwitchApiError(
                f"failed to retrieve oauth token: status={status_code}, body={body_text}"
            )

        access_token = payload.get("access_token")
        expires_in = int(payload.get("expires_in", 0))
        if not access_token or expires_in <= 0:
            raise TwitchApiError(f"invalid oauth response payload: {payload}")

        self._token = OAuthToken(access_token=access_token, expires_at=now + expires_in)
        return access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Client-Id": self._client_id,
        }

    def _api_request(
        self,
        method: str,
        url: str,
        params: list[tuple[str, str]] | None = None,
    ) -> Response:
        """Helix API request with 401 token refresh and 429 rate-limit handling."""
        response = self._request(method, url, params=params, headers=self._headers())

        if response.status_code == 401:
            print("[warn] twitch 401: token may have been revoked; refreshing")
            self._token = None
            response = self._request(method, url, params=params, headers=self._headers())

        elif response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "1"))
            print(f"[warn] twitch 429: rate limited; retrying in {retry_after}s")
            time.sleep(retry_after)
            response = self._request(method, url, params=params, headers=self._headers())

        return response

    def resolve_user_ids(self, logins: list[str]) -> dict[str, str]:
        params: list[tuple[str, str]] = [("login", login) for login in logins]
        response = self._api_request(
            method="GET",
            url="https://api.twitch.tv/helix/users",
            params=params,
        )
        status_code, body_text, payload = self._parse_json(response)
        if status_code != 200:
            raise TwitchApiError(
                f"failed users lookup: status={status_code}, body={body_text}"
            )

        data = payload.get("data", [])
        by_login: dict[str, str] = {}
        for row in data:
            login = str(row.get("login", "")).lower().strip()
            user_id = str(row.get("id", "")).strip()
            if login and user_id:
                by_login[login] = user_id

        missing = [login for login in logins if login not in by_login]
        if missing:
            raise TwitchApiError(f"unknown streamer login(s): {', '.join(missing)}")
        return by_login

    def fetch_live_user_ids(self, user_ids: list[str]) -> set[str]:
        if not user_ids:
            return set()

        params: list[tuple[str, str]] = [("user_id", user_id) for user_id in user_ids]
        response = self._api_request(
            method="GET",
            url="https://api.twitch.tv/helix/streams",
            params=params,
        )
        status_code, body_text, payload = self._parse_json(response)
        if status_code != 200:
            raise TwitchApiError(
                f"failed stream lookup: status={status_code}, body={body_text}"
            )

        data = payload.get("data", [])
        live_ids: set[str] = set()
        for row in data:
            user_id = str(row.get("user_id", "")).strip()
            if user_id:
                live_ids.add(user_id)
        return live_ids
