"""DRF autentifikatsiya yordamchilari.

`CsrfExemptSessionAuthentication` — SPA (React) JWT bearer token bilan ishlaydi,
lekin `DEFAULT_AUTHENTICATION_CLASSES` ichida oddiy `SessionAuthentication` ham
turadi (browsable API / Swagger "try it out" qulayligi uchun). DRF'ning
`SessionAuthentication` esa brauzerda Django SESSION cookie bo'lsa (masalan
foydalanuvchi admin panelga kirgan) har POST/PUT so'roviga CSRF tokenini
majburlaydi va SPA'ni bloklaydi:

    "CSRF Failed: CSRF token missing."

Bu loyihada API STATELESS — autentifikatsiya cookie orqali emas, JWT `Bearer`
token orqali. Shu bois API'da CSRF himoyasi kerak emas (CSRF faqat cookie'ga
tayangan autentifikatsiyani himoya qiladi). Bu klass CSRF tekshiruvini
o'chiradi — session bo'lsa ham SPA so'rovlari o'tadi. Django ADMIN o'zining
alohida CSRF himoyasiga ega, unga bu ta'sir qilmaydi.
"""
from rest_framework.authentication import SessionAuthentication


class CsrfExemptSessionAuthentication(SessionAuthentication):
    def enforce_csrf(self, request):  # noqa: D401 - CSRF ni ataylab o'tkazamiz
        return  # CSRF tekshiruvi yo'q (JWT bearer ishlatiladi)
