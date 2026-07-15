"""ESI providers and killmail helpers using django-esi OpenAPI clients."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

try:
    from esi.openapi_clients import ESIClientProvider
except ImportError as exc:  # pragma: no cover - enforce OpenAPI-only
    raise ImportError(
        "autosrp requires django-esi OpenAPI clients. "
        "Upgrade django-esi to a version that provides esi.openapi_clients."
    ) from exc

from .. import __app_name_ua__, __title__, __url__, __version__
from ..app_settings import ESI_COMPATIBILITY_DATE

log = logging.getLogger(__name__)

DEFAULT_ESI_OPERATIONS = [
    "GetKillmailsKillmailIdKillmailHash",
]
DEFAULT_ESI_TAGS = [
    "Killmails",
]


def _build_esi_provider() -> ESIClientProvider:
    return ESIClientProvider(
        compatibility_date=ESI_COMPATIBILITY_DATE,
        ua_appname=__app_name_ua__ or __title__,
        ua_version=__version__,
        ua_url=__url__,
        operations=DEFAULT_ESI_OPERATIONS,
        tags=DEFAULT_ESI_TAGS,
    )


esi = _build_esi_provider()


def _coerce_mapping(item):
    """Coerce a Pydantic model (or similar) into a plain dict tree."""
    if isinstance(item, dict):
        return {k: _coerce_mapping(v) for k, v in item.items()}
    if isinstance(item, list):
        return [_coerce_mapping(v) for v in item]
    for attr in ("model_dump", "dict", "to_dict"):
        converter = getattr(item, attr, None)
        if callable(converter):
            try:
                result = converter()
            except Exception:
                result = None
            if isinstance(result, dict):
                return _coerce_mapping(result)
            if isinstance(result, list):
                return _coerce_mapping(result)
    return item


"""ESI killmail accessors"""
def get_killmail(killmail_id: int, killmail_hash: str) -> Optional[dict]:
    try:
        km_id = int(killmail_id)
        km_hash = str(killmail_hash)
        operation = esi.client.Killmails.GetKillmailsKillmailIdKillmailHash(
            killmail_id=km_id,
            killmail_hash=km_hash,
        )
        payload = operation.result()
        coerced = _coerce_mapping(payload)
        if isinstance(coerced, dict):
            return coerced
        return None
    except Exception as e:
        log.debug("ESI error %s for %s", e, killmail_id)
        return None


"""Killmail field helpers"""
def km_time(km: dict) -> datetime:
    v = (km or {}).get("killmail_time")
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    if isinstance(v, str):
        s = v.strip().replace(" ", "T").replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s).astimezone(timezone.utc)
        except Exception:
            pass
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(str(v), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                continue
    raise TypeError(f"Unsupported killmail_time value: {type(v).__name__}={v!r}")


def km_system(km: dict) -> int:
    return int(km["solar_system_id"])


def km_victim(km: dict) -> dict:
    return km.get("victim") or {}


def _qty(item: dict) -> int:
    return int(item.get("quantity_destroyed", 0)) + int(item.get("quantity_dropped", 0)) or 1


def km_fitted_typeids(km: dict) -> list[int]:
    items = (km.get("victim") or {}).get("items") or []
    out: list[int] = []
    for it in items:
        tid_raw = it.get("item_type_id")
        if not tid_raw:
            continue
        try:
            tid = int(tid_raw)
        except (TypeError, ValueError):
            continue
        qty = _qty(it)
        out.extend([tid] * qty)
    return out
