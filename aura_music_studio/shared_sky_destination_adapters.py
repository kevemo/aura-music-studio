from __future__ import annotations
import ipaddress
import os
import socket
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse
import requests
from .esp_social_oauth import SocialOAuthVault
from .shared_sky_relay import SharedSkyRelayError, relay
from .shared_sky_streaming_studios import PLATFORM_REGISTRY

class CapabilityState(StrEnum):
    READY = 'ready'
    APPROVAL_PENDING = 'approval_pending'
    CREDENTIALS_MISSING = 'credentials_missing'
    ACCOUNT_INELIGIBLE = 'account_ineligible'
    SCOPE_INSUFFICIENT = 'scope_insufficient'
    UNSUPPORTED = 'unsupported'
    RUNTIME_UNAVAILABLE = 'runtime_unavailable'

@dataclass(frozen=True)
class AdapterCapability:
    state: CapabilityState
    reason_code: str
    message: str
    output_protocol: str | None = None
    documentation: str | None = None

class ProviderOperationError(RuntimeError):

    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable

class DestinationAdapter(Protocol):
    provider_id: str

    def capability(self, *, user_id: str, destination: dict) -> AdapterCapability:
        ...

    def prepare(self, *, user_id: str, destination: dict, broadcast: dict, profile: dict) -> dict:
        ...

    def stop(self, *, user_id: str, destination: dict, run: dict) -> None:
        ...

def _host_public(host: str, resolve_dns: bool) -> bool:
    name = host.strip().strip('[]').rstrip('.').lower()
    if not name or name in {'localhost', 'localhost.localdomain'} or name.endswith(('.localhost', '.local', '.internal')):
        return False
    try:
        return ipaddress.ip_address(name).is_global
    except ValueError:
        if not resolve_dns:
            return True
    try:
        addresses = {row[4][0] for row in socket.getaddrinfo(name, None, type=socket.SOCK_STREAM) if row[4]}
    except socket.gaierror as exc:
        raise ValueError('Destination host could not be resolved') from exc
    if not addresses:
        raise ValueError('Destination host could not be resolved')
    try:
        return all((ipaddress.ip_address(value).is_global for value in addresses))
    except ValueError:
        return False

def validate_destination_url(value: str, *, resolve_dns: bool=True) -> str:
    clean = (value or '').strip()
    if len(clean) > 2000 or any((ch in clean for ch in ('\n', '\r', '\x00'))):
        raise ValueError('Destination URL is invalid')
    parsed = urlparse(clean)
    if parsed.scheme.lower() not in {'rtmp', 'rtmps', 'srt', 'rist'} or not parsed.hostname:
        raise ValueError('Destination must use RTMP, RTMPS, SRT or RIST')
    if parsed.username or parsed.password:
        raise ValueError('Credentials must not be embedded in the destination URL')
    if not _host_public(parsed.hostname, resolve_dns):
        raise ValueError('Destination resolves to a local, private, reserved or unsafe network address')
    return clean

class CustomEndpointAdapter:
    provider_id = 'custom-endpoint'

    def capability(self, *, user_id: str, destination: dict) -> AdapterCapability:
        try:
            validate_destination_url(str(destination.get('endpoint') or ''), resolve_dns=False)
        except ValueError as exc:
            return AdapterCapability(CapabilityState.CREDENTIALS_MISSING, 'endpoint_invalid', str(exc))
        if destination.get('auth_mode') in {'stream_key', 'custom_rtmp'} and (not destination.get('credential_stored')):
            return AdapterCapability(CapabilityState.CREDENTIALS_MISSING, 'stream_key_missing', 'A creator-supplied stream credential is required')
        health = relay.health()
        if not health.enabled or not health.ffmpeg_available:
            return AdapterCapability(CapabilityState.RUNTIME_UNAVAILABLE, 'relay_runtime_unavailable', 'The relay runtime is not enabled and healthy')
        return AdapterCapability(CapabilityState.READY, 'ready', 'Creator-supplied destination is relay-ready', urlparse(destination['endpoint']).scheme.lower())

    def prepare(self, *, user_id: str, destination: dict, broadcast: dict, profile: dict) -> dict:
        return {'output_protocol': urlparse(destination['endpoint']).scheme.lower()}

    def stop(self, *, user_id: str, destination: dict, run: dict) -> None:
        return None

class CapabilityOnlyAdapter:

    def __init__(self, provider_id: str):
        self.provider_id = provider_id

    def capability(self, *, user_id: str, destination: dict) -> AdapterCapability:
        platform = next((p for p in PLATFORM_REGISTRY if p['id'] == self.provider_id), None)
        if not platform:
            return AdapterCapability(CapabilityState.UNSUPPORTED, 'provider_unknown', 'Provider is not registered')
        return AdapterCapability(CapabilityState.APPROVAL_PENDING, 'provider_authorisation_required', f"{platform['name']} requires verified provider authorisation or a creator-supplied permitted ingest endpoint")

    def prepare(self, *, user_id: str, destination: dict, broadcast: dict, profile: dict) -> dict:
        raise SharedSkyRelayError(f'{self.provider_id} provider API publish is not enabled')

    def stop(self, *, user_id: str, destination: dict, run: dict) -> None:
        return None

class YouTubeLiveAdapter:
    provider_id = 'youtube'
    api_base = 'https://www.googleapis.com/youtube/v3'
    docs = 'https://developers.google.com/youtube/v3/live/docs'
    live_scopes = {'https://www.googleapis.com/auth/youtube', 'https://www.googleapis.com/auth/youtube.force-ssl'}

    def __init__(self, db_path: str | Path, http: Any=requests):
        self.vault = SocialOAuthVault(Path(db_path))
        self.http = http

    def _account(self, user_id: str, destination: dict) -> tuple[dict | None, AdapterCapability | None]:
        credential_id = str((destination.get('metadata') or {}).get('oauth_credential_id') or '').strip()
        if not credential_id:
            return (None, AdapterCapability(CapabilityState.CREDENTIALS_MISSING, 'youtube_oauth_missing', 'Connect an authorised YouTube account', documentation=self.docs))
        try:
            record = self.vault.load(user_id, credential_id)
        except RuntimeError:
            return (None, AdapterCapability(CapabilityState.CREDENTIALS_MISSING, 'oauth_vault_unavailable', 'The encrypted OAuth vault is unavailable', documentation=self.docs))
        if not record or record.get('provider') != 'youtube':
            return (None, AdapterCapability(CapabilityState.CREDENTIALS_MISSING, 'youtube_oauth_invalid', 'The linked YouTube OAuth account is unavailable', documentation=self.docs))
        if not set(record.get('scopes') or []).intersection(self.live_scopes):
            return (record, AdapterCapability(CapabilityState.SCOPE_INSUFFICIENT, 'youtube_live_scope_missing', 'Reconnect YouTube with a Live Streaming API scope', documentation=self.docs))
        if os.getenv('SHARED_SKY_YOUTUBE_LIVE_ENABLED', '0').strip().lower() not in {'1', 'true', 'yes', 'on'}:
            return (record, AdapterCapability(CapabilityState.APPROVAL_PENDING, 'youtube_live_not_enabled', 'YouTube Live remains disabled until app verification/account eligibility are validated', documentation=self.docs))
        return (record, None)

    def capability(self, *, user_id: str, destination: dict) -> AdapterCapability:
        _record, blocker = self._account(user_id, destination)
        return blocker or AdapterCapability(CapabilityState.READY, 'ready', 'YouTube Live API is authorised', 'rtmps', self.docs)

    def _request(self, method: str, path: str, token: str, *, params: dict | None=None, body: dict | None=None) -> dict:
        response = self.http.request(method, f'{self.api_base}/{path}', params=params, json=body, headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'}, timeout=25)
        if not response.ok:
            status = int(response.status_code)
            code = 'rate_limited' if status == 429 else 'account_ineligible' if status in {401, 403} else 'provider_error'
            raise ProviderOperationError(code, f'YouTube Live API returned HTTP {status}', retryable=status == 429 or status >= 500)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def prepare(self, *, user_id: str, destination: dict, broadcast: dict, profile: dict) -> dict:
        record, blocker = self._account(user_id, destination)
        if blocker or not record:
            raise ProviderOperationError(blocker.reason_code if blocker else 'youtube_oauth_missing', blocker.message if blocker else 'YouTube OAuth unavailable', retryable=False)
        token = self.vault.access_token(user_id, str(record['credential_id']))
        from datetime import datetime, timedelta, timezone
        scheduled = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
        privacy = str((destination.get('metadata') or {}).get('privacy') or 'unlisted')
        if privacy not in {'private', 'unlisted', 'public'}:
            privacy = 'unlisted'
        live = self._request('POST', 'liveBroadcasts', token, params={'part': 'snippet,status,contentDetails'}, body={'snippet': {'title': broadcast['title'][:100], 'description': str(broadcast.get('description') or '')[:5000], 'scheduledStartTime': scheduled}, 'status': {'privacyStatus': privacy}, 'contentDetails': {'enableAutoStart': True, 'enableAutoStop': True, 'recordFromStart': True}})
        resolution = str(profile.get('youtube_resolution') or '1080p')
        frame_rate = str(profile.get('youtube_frame_rate') or '30fps')
        if resolution not in {'2160p', '1440p', '1080p', '720p', '480p', '360p', '240p'}:
            resolution = '1080p'
        if frame_rate not in {'30fps', '60fps'}:
            frame_rate = '30fps'
        stream = self._request('POST', 'liveStreams', token, params={'part': 'snippet,cdn,contentDetails'}, body={'snippet': {'title': broadcast['title'][:100]}, 'cdn': {'frameRate': frame_rate, 'ingestionType': 'rtmp', 'resolution': resolution}, 'contentDetails': {'isReusable': False}})
        broadcast_id, stream_id = (str(live.get('id') or ''), str(stream.get('id') or ''))
        info = (stream.get('cdn') or {}).get('ingestionInfo') or {}
        address = str(info.get('rtmpsIngestionAddress') or info.get('ingestionAddress') or '').strip()
        stream_name = str(info.get('streamName') or '').strip()
        if not all((broadcast_id, stream_id, address, stream_name)):
            raise ProviderOperationError('provider_response_incomplete', 'YouTube returned incomplete ingest data', retryable=False)
        self._request('POST', 'liveBroadcasts/bind', token, params={'part': 'id,contentDetails,status', 'id': broadcast_id, 'streamId': stream_id})
        return {'provider_broadcast_id': broadcast_id, 'provider_stream_id': stream_id, 'output_url': address.rstrip('/') + '/' + stream_name.lstrip('/')}

    def stop(self, *, user_id: str, destination: dict, run: dict) -> None:
        record, blocker = self._account(user_id, destination)
        if blocker or not record or (not run.get('provider_external_id')):
            return
        try:
            token = self.vault.access_token(user_id, str(record['credential_id']))
            self._request('POST', 'liveBroadcasts/transition', token, params={'part': 'id,status', 'id': run['provider_external_id'], 'broadcastStatus': 'complete'})
        except (ProviderOperationError, RuntimeError):
            return

def build_adapter_registry(db_path: str | Path) -> dict[str, DestinationAdapter]:
    registry: dict[str, DestinationAdapter] = {'youtube': YouTubeLiveAdapter(db_path), 'custom-rtmp': CustomEndpointAdapter(), 'custom-srt': CustomEndpointAdapter()}
    for provider in {str(item['id']) for item in PLATFORM_REGISTRY} - set(registry):
        registry[provider] = CapabilityOnlyAdapter(provider)
    return registry
__all__ = ['AdapterCapability', 'CapabilityState', 'DestinationAdapter', 'ProviderOperationError', 'build_adapter_registry', 'validate_destination_url']
