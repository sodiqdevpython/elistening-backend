"""Admin "📊 To'lovlar dashboard" — hisob-kitob (HTTP'siz, testlanadi).

Manba — `WalletTransaction`: Paynet/Click orqali TUSHGAN pul (`deposit`) va
Paynet bekor qilganlari (`refund`, manfiy). Tarifga sarflangan pul (`purchase`)
bu yerda yo'q — u "qancha to'landi" emas, hamyon ichidagi harakat.

Hamma sana mahalliy vaqtda (`TIME_ZONE = Asia/Tashkent`): "12-oktabr" deganda
admin Toshkent bo'yicha kunni nazarda tutadi, UTC'ni emas.
"""
from datetime import date, datetime, time, timedelta

from django.db.models import Count, DateField, Q, Sum
from django.db.models.functions import Trunc
from django.urls import reverse
from django.utils import timezone

from .models import WalletTransaction

Kind = WalletTransaction.Kind
Provider = WalletTransaction.Provider

PROVIDERS = (Provider.PAYNET, Provider.CLICK)
GRANULARITIES = ("day", "week", "month")
DEFAULT_DAYS = 30
TABLE_LIMIT = 500


def uzs(tiyin) -> int:
    """Tiyin → so'm (nolga qarab kesiladi; manfiy ham to'g'ri)."""
    return int((tiyin or 0) / 100)


def _date(raw) -> date | None:
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def bucket_start(day: date, granularity: str) -> date:
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    return day


def bucket_next(start: date, granularity: str) -> date:
    if granularity == "week":
        return start + timedelta(days=7)
    if granularity == "month":
        return (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start + timedelta(days=1)


def bucket_label(start: date, granularity: str) -> str:
    if granularity == "month":
        return start.strftime("%m.%Y")
    return start.strftime("%d.%m.%Y")


def parse_filters(params) -> dict:
    """So'rov parametrlari → toza filtr. Noto'g'ri qiymat — standartga qaytadi."""
    today = timezone.localdate()
    granularity = params.get("granularity") if params.get("granularity") in GRANULARITIES else "day"
    date_to = _date(params.get("to")) or today
    if params.get("from") == "all":
        first = (WalletTransaction.objects.filter(provider__in=PROVIDERS)
                 .order_by("occurred_at").values_list("occurred_at", flat=True).first())
        date_from = timezone.localtime(first).date() if first else date_to
    else:
        date_from = _date(params.get("from")) or date_to - timedelta(days=DEFAULT_DAYS - 1)
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    provider = params.get("provider") if params.get("provider") in PROVIDERS else ""
    bucket = _date(params.get("bucket"))
    return {
        "from": date_from,
        "to": date_to,
        "granularity": granularity,
        "provider": provider,
        "q": (params.get("q") or "").strip()[:100],
        "bucket": bucket_start(bucket, granularity) if bucket else None,
    }


def _aware(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min), timezone.get_current_timezone())


def payments(filters: dict, *, with_provider: bool = True):
    """Tanlangan davr, provayder va qidiruv bo'yicha to'lovlar (pul kirimi)."""
    qs = WalletTransaction.objects.filter(
        kind__in=(Kind.DEPOSIT, Kind.REFUND), provider__in=PROVIDERS,
        occurred_at__gte=_aware(filters["from"]),
        occurred_at__lt=_aware(filters["to"] + timedelta(days=1)),
    )
    if with_provider and filters["provider"]:
        qs = qs.filter(provider=filters["provider"])
    q = filters["q"]
    if q:
        cond = (Q(user__display_name__icontains=q) | Q(user__username__icontains=q)
                | Q(external_id__icontains=q))
        if q.lstrip("@").isdigit():
            cond |= Q(user__telegram_id=int(q.lstrip("@")))
        qs = qs.filter(cond)
    return qs


def kpis(qs) -> dict:
    agg = qs.aggregate(
        deposits=Sum("amount_tiyin", filter=Q(kind=Kind.DEPOSIT)),
        deposits_count=Count("id", filter=Q(kind=Kind.DEPOSIT)),
        refunds=Sum("amount_tiyin", filter=Q(kind=Kind.REFUND)),
        refunds_count=Count("id", filter=Q(kind=Kind.REFUND)),
        payers=Count("user", filter=Q(kind=Kind.DEPOSIT), distinct=True),
    )
    deposits = agg["deposits"] or 0
    count = agg["deposits_count"] or 0
    return {
        "net_uzs": uzs(deposits + (agg["refunds"] or 0)),
        "deposits_uzs": uzs(deposits),
        "deposits_count": count,
        "payers": agg["payers"] or 0,
        "avg_uzs": uzs(deposits / count) if count else 0,
        "refunds_uzs": uzs(-(agg["refunds"] or 0)),
        "refunds_count": agg["refunds_count"] or 0,
    }


def share(qs) -> list[dict]:
    """Provayderlar ulushi (sof tushum). Har doim ikkalasi ham — tartib qat'iy."""
    rows = {r["provider"]: r for r in qs.values("provider").annotate(
        total=Sum("amount_tiyin"), n=Count("id", filter=Q(kind=Kind.DEPOSIT)))}
    return [
        {"provider": p, "label": Provider(p).label,
         "uzs": uzs(rows.get(p, {}).get("total")), "count": rows.get(p, {}).get("n", 0)}
        for p in PROVIDERS
    ]


def series(qs, filters: dict) -> list[dict]:
    """Har davr (kun/hafta/oy) bo'yicha sof tushum — bo'sh davrlar 0 bilan."""
    granularity = filters["granularity"]
    grouped = (
        qs.annotate(bucket=Trunc("occurred_at", granularity, output_field=DateField(),
                                 tzinfo=timezone.get_current_timezone()))
        .values("bucket", "provider")
        .annotate(total=Sum("amount_tiyin"), n=Count("id", filter=Q(kind=Kind.DEPOSIT)))
    )
    by_bucket: dict[date, dict] = {}
    for row in grouped:
        day = row["bucket"]
        if isinstance(day, datetime):
            day = day.date()
        cell = by_bucket.setdefault(day, {"paynet": 0, "click": 0, "count": 0})
        cell[row["provider"]] += row["total"] or 0
        cell["count"] += row["n"]

    out = []
    start = bucket_start(filters["from"], granularity)
    end = filters["to"]
    while start <= end:
        cell = by_bucket.get(start, {"paynet": 0, "click": 0, "count": 0})
        out.append({
            "bucket": start.isoformat(),
            "label": bucket_label(start, granularity),
            "paynet": uzs(cell["paynet"]),
            "click": uzs(cell["click"]),
            "total": uzs(cell["paynet"] + cell["click"]),
            "count": cell["count"],
        })
        start = bucket_next(start, granularity)
    return out


def table(qs, filters: dict) -> dict:
    """Jadval — kim, qachon, qaysi tizimdan, qancha. Grafikdagi nuqta bosilsa
    (`bucket`) faqat o'sha davr."""
    bucket = filters["bucket"]
    if bucket:
        qs = qs.filter(occurred_at__gte=_aware(bucket),
                       occurred_at__lt=_aware(bucket_next(bucket, filters["granularity"])))
    totals = qs.aggregate(total=Sum("amount_tiyin"), n=Count("id"))
    rows = []
    for tx in qs.select_related("user").order_by("-occurred_at", "-id")[:TABLE_LIMIT]:
        user = tx.user
        rows.append({
            "id": tx.pk,
            "at": timezone.localtime(tx.occurred_at).strftime("%d.%m.%Y %H:%M"),
            "user": {
                "id": user.pk,
                "name": (user.display_name or "").strip(),
                "username": user.username or "",
                "telegram_id": user.telegram_id,
                "admin_url": reverse("admin:accounts_user_change", args=[user.pk]),
            },
            "provider": tx.provider,
            "provider_label": tx.get_provider_display(),
            "kind": tx.kind,
            "kind_label": tx.get_kind_display(),
            "amount_uzs": tx.amount_uzs,
            "balance_after_uzs": tx.balance_after_uzs,
            "external_id": tx.external_id,
        })
    return {
        "bucket": bucket.isoformat() if bucket else None,
        "bucket_label": bucket_label(bucket, filters["granularity"]) if bucket else "",
        "total_uzs": uzs(totals["total"]),
        "count": totals["n"] or 0,
        "shown": len(rows),
        "rows": rows,
    }


def dashboard_data(params) -> dict:
    filters = parse_filters(params)
    qs = payments(filters)
    return {
        "filters": {
            "from": filters["from"].isoformat(), "to": filters["to"].isoformat(),
            "granularity": filters["granularity"], "provider": filters["provider"],
            "q": filters["q"],
        },
        "kpis": kpis(qs),
        # Ulush provayder filtrisiz — tanlangani boshqasiga nisbatan ko'rinsin.
        "share": share(payments(filters, with_provider=False)),
        "series": series(qs, filters),
        "table": table(qs, filters),
    }
