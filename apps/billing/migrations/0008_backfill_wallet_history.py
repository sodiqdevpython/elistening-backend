"""Hamyon tarixini MAVJUD to'lovlardan tiklaydi.

`WalletTransaction` paydo bo'lishidan oldin tushgan pul ham profildagi
"To'lovlar tarixi" da ko'rinishi kerak. Manba — provayder jurnallari:

* Paynet: har tranzaksiya → "pul tushdi"; bekor qilingani → yana "bekor qilindi"
* Click: tasdiqlangan (`confirmed`) buyurtma → "pul tushdi"

Tarifga sarflangan pul tiklanmaydi — o'sha paytda summa yozilmagan edi
(tarifning o'zi "Tarif tarixi" da bor). `balance_after_tiyin` ham noma'lum
(bo'sh qoladi). Qayta ishga tushirilsa takror yozmaydi.
"""
from decimal import Decimal

from django.db import migrations


def backfill(apps, schema_editor):
    WalletTransaction = apps.get_model("billing", "WalletTransaction")
    PaynetTransaction = apps.get_model("paynet", "PaynetTransaction")
    ClickOrder = apps.get_model("click", "ClickOrder")

    done = set(WalletTransaction.objects.values_list("provider", "kind", "external_id"))
    rows = []

    def add(user_id, kind, provider, amount, external_id, occurred_at):
        key = (provider, kind, external_id)
        if key in done:
            return
        done.add(key)
        rows.append(WalletTransaction(
            user_id=user_id, kind=kind, provider=provider, amount_tiyin=amount,
            external_id=external_id, occurred_at=occurred_at,
        ))

    for txn in PaynetTransaction.objects.all().iterator():
        trn = str(txn.transaction_id)
        add(txn.user_id, "deposit", "paynet", txn.amount_tiyin, trn, txn.performed_at)
        if txn.state == 2:  # bekor qilingan
            add(txn.user_id, "refund", "paynet", -txn.amount_tiyin, trn,
                txn.cancelled_at or txn.performed_at)

    for order in ClickOrder.objects.filter(status="confirmed").iterator():
        add(order.user_id, "deposit", "click", int(Decimal(order.amount_uzs) * 100),
            str(order.pk), order.paid_at or order.updated_at)

    WalletTransaction.objects.bulk_create(rows, batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0007_wallettransaction"),
        ("paynet", "0001_initial"),
        ("click", "0001_initial"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
