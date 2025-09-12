from django.contrib.auth.mixins import PermissionRequiredMixin
from django.views.generic import TemplateView, ListView, CreateView, UpdateView
from django.urls import reverse_lazy
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_GET
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Avg, Count, F, DecimalField, Q, ExpressionWrapper, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.shortcuts import render, redirect, get_object_or_404
from django.core.exceptions import ValidationError
from django.views import View
from django.contrib import messages
from django import forms

from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
import json

from ..forms import PenaltyForm
from ..models import (
    Submission,
    KillRecord,
    PayoutSuggestion,
    PayoutRecord,
    PenaltyScheme,
    DoctrineReward,
    AppSetting,
)

"""Helpers: doctrine and hull choices"""
def _doctrine_choices():
    try:
        from fittings.models import Doctrine
        choices = list(Doctrine.objects.values_list("id", "name"))
        if choices:
            return choices
    except Exception:
        pass
    try:
        from fittings.models import Fitting
        ids = (
            Fitting.objects.exclude(doctrine_id__isnull=True)
            .values_list("doctrine_id", flat=True)
            .distinct()
            .order_by("doctrine_id")
        )
        return [(int(i), f"Doctrine {i}") for i in ids]
    except Exception:
        return []


def _hull_choices_for_doctrine(doctrine_id: int) -> list[tuple[int, str]]:
    if not doctrine_id:
        return []
    ship_ids: list[int] = []
    try:
        from fittings.models import Fitting
        ship_ids = list(
            Fitting.objects.filter(doctrines__id=int(doctrine_id))
            .values_list("ship_type_id", flat=True)
            .distinct()
        )
    except Exception:
        ship_ids = []
    if not ship_ids:
        return []

    names: dict[int, str] = {}
    try:
        from eveuniverse.models import EveType
        rows = EveType.objects.filter(id__in=ship_ids).values("id", "name")
        names = {int(r["id"]): (r["name"] or f"Type {r['id']}") for r in rows}
    except Exception:
        names = {}

    out = []
    for sid in sorted({int(x) for x in ship_ids}):
        label = names.get(int(sid), f"Type {sid}")
        out.append((int(sid), label))
    return out


"""Forms"""
class RewardForm(forms.ModelForm):
    doctrine_id = forms.ChoiceField(
        choices=[("", "— Select a doctrine —")] + _doctrine_choices(),
        label="Doctrine",
        required=True,
    )
    ship_type_id = forms.ChoiceField(
        choices=[("", "— Select a doctrine first —")],
        label="Ship (from doctrine fits)",
        required=True,
    )

    class Meta:
        model = DoctrineReward
        fields = ("doctrine_id", "ship_type_id", "base_reward_isk", "penalty_scheme", "notes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        selected_doctrine = None
        data = self.data or None
        if data and str(data.get("doctrine_id", "")).strip():
            try:
                selected_doctrine = int(data.get("doctrine_id"))
            except (TypeError, ValueError):
                selected_doctrine = None
        elif getattr(self.instance, "doctrine_id", None):
            selected_doctrine = int(self.instance.doctrine_id)

        if selected_doctrine:
            hull_choices = _hull_choices_for_doctrine(selected_doctrine) or []
            if hull_choices:
                self.fields["ship_type_id"].choices = [("", "— Select a ship —")] + [(str(a), b) for a, b in hull_choices]
            else:
                self.fields["ship_type_id"].choices = [("", "— No fits for this doctrine —")]
        else:
            self.fields["ship_type_id"].choices = [("", "— Select a doctrine first —")]

        self.fields["doctrine_id"].coerce = int if hasattr(self.fields["doctrine_id"], "coerce") else None
        self.fields["ship_type_id"].coerce = int if hasattr(self.fields["ship_type_id"], "coerce") else None

        self.fields["base_reward_isk"].label = "Base Reward (ISK)"

    def clean(self):
        cleaned = super().clean()

        try:
            did = int(cleaned.get("doctrine_id"))
        except Exception:
            did = None
        try:
            sid = int(cleaned.get("ship_type_id"))
        except Exception:
            sid = None

        if not did:
            raise ValidationError({"doctrine_id": "Please select a doctrine."})
        if not sid:
            raise ValidationError({"ship_type_id": "Please select a ship for the selected doctrine."})

        valid_hulls = {hid for hid, _ in _hull_choices_for_doctrine(did)}
        if sid not in valid_hulls:
            raise ValidationError({"ship_type_id": "Selected ship is not available in the chosen doctrine."})

        cleaned["doctrine_id"] = did
        cleaned["ship_type_id"] = sid
        return cleaned


"""AJAX endpoints"""
@require_GET
def doctrine_hulls(request):
    try:
        did = int(request.GET.get("doctrine_id", 0) or 0)
    except (TypeError, ValueError):
        did = 0

    hulls: list[dict] = []
    if did > 0:
        try:
            from fittings.models import Fitting
            ship_ids = list(
                Fitting.objects.filter(doctrines__id=did)
                .values_list("ship_type_id", flat=True)
                .distinct()
            )
            if ship_ids:
                try:
                    from eveuniverse.models import EveType
                    rows = EveType.objects.filter(id__in=ship_ids).values("id", "name")
                    hulls = [{"id": int(r["id"]), "name": (r["name"] or f"Type {r['id']}")} for r in rows]
                except Exception:
                    hulls = [{"id": int(sid), "name": f"Type {int(sid)}"} for sid in ship_ids]
        except Exception:
            hulls = []

    try:
        hulls.sort(key=lambda x: (x["name"] or str(x["id"])).lower())
    except Exception:
        pass

    return JsonResponse({"hulls": hulls})


"""Views: settings and lists"""
class SettingsHome(PermissionRequiredMixin, TemplateView):
    permission_required = "autosrp.manage"
    template_name = "autosrp/admin/home.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["app"] = AppSetting.objects.first()
        return ctx


class PenaltyList(PermissionRequiredMixin, ListView):
    permission_required = "autosrp.manage"
    model = PenaltyScheme
    template_name = "autosrp/admin/penalty_list.html"


class PenaltyDelete(PermissionRequiredMixin, View):
    permission_required = "autosrp.manage"

    def post(self, request, pk: int):
        obj = get_object_or_404(PenaltyScheme, pk=pk)
        name = str(obj.name or f"Scheme {obj.pk}")
        obj.delete()
        messages.success(request, f"Deleted penalty scheme '{name}'.")
        return redirect("autosrp:penalty-list")


class PenaltyCreate(PermissionRequiredMixin, CreateView):
    permission_required = "autosrp.manage"
    model = PenaltyScheme
    form_class = PenaltyForm
    success_url = reverse_lazy("autosrp:penalty-list")
    template_name = "autosrp/admin/penalty_form.html"


class PenaltyUpdate(PermissionRequiredMixin, UpdateView):
    permission_required = "autosrp.manage"
    model = PenaltyScheme
    form_class = PenaltyForm
    success_url = reverse_lazy("autosrp:penalty-list")
    template_name = "autosrp/admin/penalty_form.html"


class RewardList(PermissionRequiredMixin, ListView):
    permission_required = "autosrp.manage"
    model = DoctrineReward
    template_name = "autosrp/admin/reward_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        rewards = list(ctx.get("object_list", []))

        doctrine_ids = {int(r.doctrine_id) for r in rewards if getattr(r, "doctrine_id", None)}
        ship_ids = {int(r.ship_type_id) for r in rewards if getattr(r, "ship_type_id", None)}

        doctrine_map = {}
        try:
            from fittings.models import Doctrine
            rows = Doctrine.objects.filter(id__in=doctrine_ids).values("id", "name")
            doctrine_map = {int(r["id"]): (r["name"] or f"Doctrine {r['id']}") for r in rows}
        except Exception:
            doctrine_map = {}

        type_map = {}
        try:
            from eveuniverse.models import EveType
            rows = EveType.objects.filter(id__in=ship_ids).values("id", "name")
            type_map = {int(r["id"]): (r["name"] or f"Type {r['id']}") for r in rows}
        except Exception:
            type_map = {}

        for r in rewards:
            try:
                r.doctrine_name = doctrine_map.get(int(r.doctrine_id), f"Doctrine {int(r.doctrine_id)}")
            except Exception:
                r.doctrine_name = f"Doctrine {getattr(r, 'doctrine_id', '')}"
            try:
                r.ship_name = type_map.get(int(r.ship_type_id), f"Type {int(r.ship_type_id)}")
            except Exception:
                r.ship_name = f"Type {getattr(r, 'ship_type_id', '')}"

        ctx["object_list"] = rewards
        return ctx


class RewardCreate(PermissionRequiredMixin, CreateView):
    permission_required = "autosrp.manage"
    model = DoctrineReward
    form_class = RewardForm
    success_url = reverse_lazy("autosrp:reward-list")
    template_name = "autosrp/admin/reward_form.html"


class RewardUpdate(PermissionRequiredMixin, UpdateView):
    permission_required = "autosrp.manage"
    model = DoctrineReward
    form_class = RewardForm
    success_url = reverse_lazy("autosrp:reward-list")
    template_name = "autosrp/admin/reward_form.html"


class RewardDelete(PermissionRequiredMixin, View):
    permission_required = "autosrp.manage"

    def post(self, request, pk: int):
        obj = get_object_or_404(DoctrineReward, pk=pk)
        did, sid = int(obj.doctrine_id), int(obj.ship_type_id)
        obj.delete()
        messages.success(request, f"Deleted reward for Doctrine {did} / Ship {sid}.")
        return redirect("autosrp:reward-list")


"""Statistics"""
@login_required
def stats(request):
    user = request.user
    if not (user.has_perm("autosrp.review") or user.has_perm("autosrp.manage") or user.is_superuser):
        return HttpResponseForbidden("You do not have permission to view this page.")

    """Totals"""
    total_fights = Submission.objects.count()
    total_kills = KillRecord.objects.count()

    """Aggregations"""
    loss_value_expr = ExpressionWrapper(
        F("hull_price_isk") + F("fit_price_isk"),
        output_field=DecimalField(max_digits=20, decimal_places=2),
    )
    total_loss = (
        PayoutSuggestion.objects.annotate(loss_value=loss_value_expr)
        .aggregate(
            total=Coalesce(
                Sum("loss_value"),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=20, decimal_places=2)),
            )
        )
        .get("total")
    )
    total_paid = (
        PayoutRecord.objects.aggregate(
            total=Coalesce(
                Sum("actual_isk"),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=20, decimal_places=2)),
            )
        ).get("total")
    )
    avg_kills_per_fight = (total_kills / total_fights) if total_fights else 0
    avg_loss_per_fight = (total_loss / total_fights) if total_fights else 0
    avg_paid_per_fight = (total_paid / total_fights) if total_fights else 0
    avg_suggested_per_kill = (
        PayoutSuggestion.objects.aggregate(
            v=Coalesce(
                Avg("suggested_isk"),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=20, decimal_places=2)),
            )
        ).get("v")
    )
    avg_final_per_kill = (
        PayoutSuggestion.objects.annotate(
            final_isk=Coalesce(
                F("override_isk"),
                F("suggested_isk"),
                output_field=DecimalField(max_digits=20, decimal_places=2),
            )
        )
        .aggregate(
            v=Coalesce(
                Avg("final_isk"),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=20, decimal_places=2)),
            )
        )
        .get("v")
    )
    approved_cnt = KillRecord.objects.filter(Q(status="approved") | Q(status="approved_with_comment")).count()
    rejected_cnt = KillRecord.objects.filter(status="rejected").count()
    submitted_cnt = KillRecord.objects.filter(status="submitted").count()
    avg_penalty_pct = (
        PayoutSuggestion.objects.aggregate(
            v=Coalesce(
                Avg("penalty_pct"),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=5, decimal_places=2)),
            )
        ).get("v")
    )

    """Charts"""
    now = timezone.now()
    start_month = (now.replace(day=1) - timedelta(days=180)).replace(day=1)

    def ym(dt):
        return dt.strftime("%Y-%m")

    labels = []
    cursor = start_month
    for _ in range(7):
        labels.append(ym(cursor))
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)

    kills_qs = (
        KillRecord.objects.filter(occurred_at__gte=start_month)
        .annotate(m=TruncMonth("occurred_at"))
        .values("m")
        .annotate(c=Count("id"))
        .order_by("m")
    )
    kills_map = {ym(r["m"]): r["c"] for r in kills_qs}
    kills_per_month = [kills_map.get(label, 0) for label in labels]

    paid_qs = (
        PayoutRecord.objects.filter(kill__occurred_at__gte=start_month)
        .annotate(m=TruncMonth("kill__occurred_at"))
        .values("m")
        .annotate(
            s=Coalesce(
                Sum("actual_isk"),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=20, decimal_places=2)),
            )
        )
        .order_by("m")
    )
    paid_map = {ym(r["m"]): float(r["s"]) for r in paid_qs}
    paid_per_month = [paid_map.get(label, 0.0) for label in labels]

    loss_qs = (
        PayoutSuggestion.objects.filter(kill__occurred_at__gte=start_month)
        .annotate(m=TruncMonth("kill__occurred_at"), loss_value=loss_value_expr)
        .values("m")
        .annotate(
            s=Coalesce(
                Sum("loss_value"),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=20, decimal_places=2)),
            )
        )
        .order_by("m")
    )
    loss_map = {ym(r["m"]): float(r["s"]) for r in loss_qs}
    loss_per_month = [loss_map.get(label, 0.0) for label in labels]

    """Status distribution"""
    status_labels = ["submitted", "approved", "approved_with_comment", "rejected"]
    status_values = [
        KillRecord.objects.filter(status="submitted").count(),
        KillRecord.objects.filter(status="approved").count(),
        KillRecord.objects.filter(status="approved_with_comment").count(),
        KillRecord.objects.filter(status="rejected").count(),
    ]

    context = {
        "total_loss": total_loss,
        "total_paid": total_paid,
        "total_fights": total_fights,
        "total_kills": total_kills,
        "avg_kills_per_fight": avg_kills_per_fight,
        "avg_loss_per_fight": avg_loss_per_fight,
        "avg_paid_per_fight": avg_paid_per_fight,
        "avg_suggested_per_kill": avg_suggested_per_kill,
        "avg_final_per_kill": avg_final_per_kill,
        "approved_cnt": approved_cnt,
        "rejected_cnt": rejected_cnt,
        "submitted_cnt": submitted_cnt,
        "avg_penalty_pct": avg_penalty_pct,
        "chart_labels_json": json.dumps(labels),
        "kills_per_month_json": json.dumps(kills_per_month),
        "paid_per_month_json": json.dumps(paid_per_month),
        "loss_per_month_json": json.dumps(loss_per_month),
        "status_labels_json": json.dumps(status_labels),
        "status_values_json": json.dumps(status_values),
    }
    return render(request, "autosrp/admin/stats.html", context)
