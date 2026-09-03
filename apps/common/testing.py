"""Testlar uchun runner — har testdan oldin KESHNI tozalaydi.

## Nega kerak

Django testlar orasida bazani rollback qiladi, lekin **keshga tegmaydi**.
locmem kesh butun jarayon davomida yashaydi, kalitlar esa ko'pincha
`user.pk` ga bog'lanadi — pk'lar esa har testda 1 dan qayta boshlanadi.
Natijada A testidagi "1-foydalanuvchi Pro" B testiga o'tib ketadi va B
"tarif free bo'lishi kerak edi" deb **yolg'on** yiqiladi.

Bu tuzoqqa ikki marta tushildi:

  - DRF throttle hisobi keshda — ketma-ket testlar 429 olib, "taklif
    sanalmadi" degan chalg'ituvchi xato berardi;
  - tarif keshi — uchta test bir-birining tarifini ko'rib qoldi.

Har testda qo'lda `cache.clear()` yozish yechim emas: uni unutish oson va
xato **boshqa** faylda chiqadi. Shu bois tozalash markazda.

Sozlama: `config/settings/base.py` → `TEST_RUNNER`.
"""
from django.core.cache import caches
from django.test.runner import DiscoverRunner


class CacheIsolatedRunner(DiscoverRunner):
    """Standart runner + har testdan oldin barcha keshlarni tozalaydi."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)

        from django.test import SimpleTestCase

        original_pre_setup = SimpleTestCase._pre_setup

        def _pre_setup(test_self):
            for alias in caches:
                caches[alias].clear()
            original_pre_setup(test_self)

        SimpleTestCase._pre_setup = _pre_setup
