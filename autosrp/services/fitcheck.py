from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Optional

from django.db.models import QuerySet
from django.conf import settings
from eveuniverse.models import EveType
from fittings.models import Fitting, FittingItem

FITTED_FLAGS: set[int] = set(range(11, 35)) | set(range(92, 99)) | set(range(125, 133))

IGNORE_CATEGORY_IDS: set[int] = set(
    getattr(settings, "AUTOSRP_FITCHECK_IGNORE_CATEGORY_IDS", {8, 18, 20, 5})
)

@dataclass
class FitCompareResult:
    doctrine_fit_id: Optional[int]
    mode: str
    passed: bool
    missing: list[int]
    extra: list[int]
    substitutions: dict[int, int]
    notes: str


def _remove_ignored_by_group(type_ids: list[int]) -> list[int]:
    if not type_ids:
        return type_ids
    rows = list(
        EveType.objects.filter(id__in=set(type_ids)).values(
            "id", "eve_group__name", "eve_group__eve_category_id"
        )
    )
    ignore_by_name = set(getattr(settings, "AUTOSRP_FITCHECK_IGNORE_GROUP_NAMES", {"Festival Launcher"}))
    subsystem_category_id = 32
    found = {int(r["id"]) for r in rows}
    out: list[int] = []

    for r in rows:
        tid = int(r["id"])
        gname = (r.get("eve_group__name") or "")
        cat = int(r.get("eve_group__eve_category_id") or 0)
        if (gname in ignore_by_name) or (cat in IGNORE_CATEGORY_IDS and cat != subsystem_category_id):
            continue
        out.append(tid)

    out.extend([int(t) for t in type_ids if int(t) not in found])
    return out


def _expand_items(type_ids: Iterable[int]) -> Counter:
    if not type_ids:
        return Counter()
    ids = _remove_ignored_by_group([int(x) for x in type_ids if x])
    return Counter(ids)


def _expand_items_grouped(type_ids: Iterable[int]) -> Counter:
    ids = _remove_ignored_by_group([int(x) for x in type_ids if x])
    if not ids:
        return Counter()
    groups = dict(EveType.objects.filter(id__in=set(ids)).values_list("id", "eve_group_id"))
    group_list = [int(groups.get(tid, 0) or 0) for tid in ids]
    return Counter(g for g in group_list if g)


def _flatten_km_ids(val) -> list[int]:
    if not val:
        return []
    out: list[int] = []

    if isinstance(val, dict):
        has_bands = any(k in val for k in ("high", "mid", "low", "rig", "sub"))
        if has_bands:
            for k in ("high", "mid", "low", "rig", "sub"):
                try:
                    for x in (val.get(k) or []):
                        out.append(int(x))
                except Exception:
                    continue
            return out
        try:
            for k, q in val.items():
                tid = int(k)
                qty = int(q or 0) or 1
                if qty > 0:
                    out.extend([tid] * qty)
            return out
        except Exception:
            pass

    if isinstance(val, (list, tuple, set)):
        for x in val:
            try:
                out.append(int(x))
            except Exception:
                continue
        return out

    try:
        return [int(val)]
    except Exception:
        return []


def _fitted_from_km_ids(km_fitted_type_ids: Iterable[int] | dict, *, strict: bool) -> Counter:
    ids = _flatten_km_ids(km_fitted_type_ids)
    return _expand_items(ids) if strict else _expand_items_grouped(ids)


def _fitted_from_fit(fit: Fitting, *, strict: bool) -> Counter:
    try:
        rows = fit._items_by_type()
        ids = []
        for r in rows:
            tid = int(r.get("type_id") or 0)
            qty = int(r.get("qty") or 0) or 1
            ids.extend([tid] * qty)
    except Exception:
        q: QuerySet[FittingItem] = FittingItem.objects.filter(fit=fit).values("flag", "type_id", "type_fk_id", "quantity")
        ids = []
        for it in q:
            flag_val = it.get("flag")
            include = False

            if isinstance(flag_val, int):
                include = flag_val in FITTED_FLAGS
            else:
                flag_str = str(flag_val or "").lower()
                if flag_str.startswith(("hislot", "medslot", "loslot", "rigslot", "subsystem")):
                    include = True

            if not include:
                continue

            tid_raw = it.get("type_id") or it.get("type_fk_id") or 0
            try:
                tid = int(tid_raw)
            except (TypeError, ValueError):
                tid = 0
            qty = int(it.get("quantity") or 0) or 1
            if tid:
                ids.extend([tid] * qty)

    return _expand_items(ids) if strict else _expand_items_grouped(ids)


def _band_for_flag(flag_val) -> str | None:
    if isinstance(flag_val, int):
        f = int(flag_val)
        if 27 <= f <= 34:
            return "high"
        if 19 <= f <= 26:
            return "mid"
        if 11 <= f <= 18:
            return "low"
        if 92 <= f <= 98:
            return "rig"
        if 125 <= f <= 132:
            return "sub"
        return None
    s = str(flag_val or "").lower()
    if s.startswith("hislot"):
        return "high"
    if s.startswith("medslot"):
        return "mid"
    if s.startswith("loslot"):
        return "low"
    if s.startswith("rigslot"):
        return "rig"
    if s.startswith("subsystem"):
        return "sub"
    return None


def _band_counts_from_fit(fit: Fitting) -> dict[str, int]:
    bands = {"high": 0, "mid": 0, "low": 0, "rig": 0, "sub": 0}
    q: QuerySet[FittingItem] = FittingItem.objects.filter(fit=fit).values("flag", "quantity")
    for it in q:
        band = _band_for_flag(it.get("flag"))
        if not band:
            continue
        qty = int(it.get("quantity") or 0) or 1
        bands[band] += qty
    return bands


def _band_counts_from_km(val) -> dict[str, int] | None:
    if not isinstance(val, dict):
        return None
    has_bands = any(k in val for k in ("high", "mid", "low", "rig", "sub"))
    if not has_bands:
        return None
    return {
        "high": len([x for x in (val.get("high") or [])]),
        "mid": len([x for x in (val.get("mid") or [])]),
        "low": len([x for x in (val.get("low") or [])]),
        "rig": len([x for x in (val.get("rig") or [])]),
        "sub": len([x for x in (val.get("sub") or [])]),
    }


def _diff(doctrine: Counter, actual: Counter) -> tuple[list[int], list[int]]:
    missing, extra = [], []

    for k, need in doctrine.items():
        have = actual.get(k, 0)
        category = EveType.objects.filter(id=k).values_list("eve_group__eve_category_id", flat=True).first()

        if category == 32:
            pass

        if have < need:
            missing.extend([k] * (need - have))

    for k, have in actual.items():
        need = doctrine.get(k, 0)
        category = EveType.objects.filter(id=k).values_list("eve_group__eve_category_id", flat=True).first()
        if category == 32:
            pass
        if have > need:
            extra.extend([k] * (have - need))

    return missing, extra


def _candidate_fits(doctrine_id: int, ship_type_id: int) -> list[Fitting]:
    qs: QuerySet[Fitting] = Fitting.objects.all()

    try:
        fits = list(qs.filter(doctrines__id=doctrine_id, ship_type_id=ship_type_id))
        if fits:
            return fits
    except Exception:
        pass

    try:
        fits = list(qs.filter(doctrines__id=doctrine_id))
        if fits:
            return fits
    except Exception:
        pass

    return []


def _best_fit(doctrine_id: int, ship_type_id: int, km_fitted_type_ids: Iterable[int] | dict, *, strict: bool) -> tuple[
    Optional[Fitting], dict]:
    candidates = _candidate_fits(doctrine_id, ship_type_id)
    if not candidates:
        return None, {}
    actual = _fitted_from_km_ids(km_fitted_type_ids, strict=strict)
    scores = {}
    best = None
    best_missing = None
    for f in candidates:
        ref = _fitted_from_fit(f, strict=strict)
        missing, extra = _diff(ref, actual)
        score_missing = len(missing)
        score_extra = len(extra)
        scores[f.id] = {
            ("strict_missing" if strict else "loose_missing"): score_missing,
            ("strict_extra" if strict else "loose_extra"): score_extra,
        }
        if best is None or score_missing < (best_missing or 10 ** 6):
            best = f
            best_missing = score_missing
    return best, scores


def compare(doctrine_id: int, ship_type_id: int, km_fitted_type_ids: Iterable[int] | dict, strict_mode: bool) -> dict:
    km_ids_flat = _flatten_km_ids(km_fitted_type_ids)

    fit, _scores = _best_fit(doctrine_id, ship_type_id, km_ids_flat, strict=strict_mode)
    if not fit:
        return FitCompareResult(
            doctrine_fit_id=None,
            mode="strict" if strict_mode else "loose",
            passed=False,
            missing=[],
            extra=[],
            substitutions={},
            notes="No matching doctrine fit found for this hull.",
        ).__dict__

    if strict_mode:
        ref = _fitted_from_fit(fit, strict=True)
        act = _fitted_from_km_ids(km_ids_flat, strict=True)
        missing, extra = _diff(ref, act)

        d_bands = _band_counts_from_fit(fit)
        a_bands = _band_counts_from_km(km_fitted_type_ids)
        if a_bands is not None:
            bands_to_check = ("high", "mid", "low", "rig")
            has_shortfall = any(d_bands.get(b, 0) > a_bands.get(b, 0) for b in bands_to_check)
            if not has_shortfall:
                missing = []
        passed = (len(missing) == 0 and len(extra) == 0)
        mode = "strict"
        if not passed:
            ref_loose = _fitted_from_fit(fit, strict=False)
            act_loose = _fitted_from_km_ids(km_ids_flat, strict=False)
            m2, e2 = _diff(ref_loose, act_loose)
            note = "Strict comparison failed on exact types; group-level (meta) match would pass." if len(m2) == 0 else ""
        else:
            note = ""
    else:
        ref = _fitted_from_fit(fit, strict=False)
        act = _fitted_from_km_ids(km_ids_flat, strict=False)
        missing, extra = _diff(ref, act)

        d_bands = _band_counts_from_fit(fit)
        a_bands = _band_counts_from_km(km_fitted_type_ids)
        if a_bands is not None:
            bands_to_check = ("high", "mid", "low", "rig")
            has_shortfall = any(d_bands.get(b, 0) > a_bands.get(b, 0) for b in bands_to_check)
            if not has_shortfall:
                missing = []
        passed = (len(missing) == 0)
        mode = "loose"
        note = ""

    return FitCompareResult(
        doctrine_fit_id=fit.id,
        mode=mode,
        passed=passed,
        missing=missing,
        extra=extra,
        substitutions={},
        notes=note,
    ).__dict__


def compare_with_fit(doctrine_fit_id: int, ship_type_id: int, km_fitted_type_ids: Iterable[int] | dict,
                     strict_mode: bool) -> dict:
    try:
        fit = Fitting.objects.get(id=doctrine_fit_id)
    except Fitting.DoesNotExist:
        return FitCompareResult(
            doctrine_fit_id=None,
            mode="strict" if strict_mode else "loose",
            passed=False,
            missing=[],
            extra=[],
            substitutions={},
            notes=f"Selected doctrine fit {doctrine_fit_id} not found.",
        ).__dict__

    km_ids_flat = _flatten_km_ids(km_fitted_type_ids)

    if strict_mode:
        ref = _fitted_from_fit(fit, strict=True)
        act = _fitted_from_km_ids(km_ids_flat, strict=True)
        missing, extra = _diff(ref, act)
        d_bands = _band_counts_from_fit(fit)
        a_bands = _band_counts_from_km(km_fitted_type_ids)
        if a_bands is not None:
            bands_to_check = ("high", "mid", "low", "rig")
            has_shortfall = any(d_bands.get(b, 0) > a_bands.get(b, 0) for b in bands_to_check)
            if not has_shortfall:
                missing = []
        passed = (len(missing) == 0 and len(extra) == 0)
        mode = "strict"
        note = ""
        if not passed:
            ref_loose = _fitted_from_fit(fit, strict=False)
            act_loose = _fitted_from_km_ids(km_ids_flat, strict=False)
            m2, _e2 = _diff(ref_loose, act_loose)
            if len(m2) == 0:
                note = "Strict comparison failed on exact types; group-level (meta) match would pass."
    else:
        ref = _fitted_from_fit(fit, strict=False)
        act = _fitted_from_km_ids(km_ids_flat, strict=False)
        missing, extra = _diff(ref, act)
        d_bands = _band_counts_from_fit(fit)
        a_bands = _band_counts_from_km(km_fitted_type_ids)
        if a_bands is not None:
            bands_to_check = ("high", "mid", "low", "rig")
            has_shortfall = any(d_bands.get(b, 0) > a_bands.get(b, 0) for b in bands_to_check)
            if not has_shortfall:
                missing = []
        passed = (len(missing) == 0)
        mode = "loose"
        note = ""

    return FitCompareResult(
        doctrine_fit_id=fit.id,
        mode=mode,
        passed=passed,
        missing=missing,
        extra=extra,
        substitutions={},
        notes=note,
    ).__dict__


def km_fitted_typeids(km: dict) -> list[int]:
    items = (km.get("victim") or {}).get("items") or []
    candidates = [
        it for it in items
        if int(it.get("flag", -1)) in FITTED_FLAGS
        and it.get("item_type_id")
    ]

    out: list[int] = []
    for it in candidates:
        try:
            tid = int(it["item_type_id"])
        except Exception:
            continue
        out.append(tid)

    return out
