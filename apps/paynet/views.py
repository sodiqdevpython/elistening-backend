"""Paynet uchun YAGONA endpoint: `POST /api/paynet/`.

Django/DRF'ning odatiy qatlamlari ATAYLAB chetlab o'tilgan:

* **DRF emas, oddiy `HttpResponse`** — DRF content negotiation, throttle va
  autentikatsiya klasslari bu yerda ortiqcha ish va ortiqcha kechikish
  (4.5-band: ≤500 ms).
* **CSRF yo'q** — bu server-server chaqiruvi, cookie ishlatilmaydi.
* **Sessiya yo'q** — har so'rov Basic auth bilan mustaqil.

Javob HAR DOIM `200 OK` bo'ladi (yagona istisno — 401). JSON-RPC'da xato
javob tanasining ichida ketadi; HTTP 4xx/5xx qaytarsak Paynet uni "servis
ishlamayapti" deb o'qib, tranzaksiyani osiltirib qo'yardi.
"""
import json

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from . import errors, rpc, security


def _json(payload: dict, status: int = 200) -> HttpResponse:
    return HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
        status=status,
    )


@csrf_exempt
def endpoint(request):
    # 1) IP — eng birinchi. Notanish manzilga parolning to'g'ri-noto'g'riligi
    #    haqida ham ma'lumot bermaymiz (Приложение №2, 4.2–4.3).
    if not security.ip_allowed(request):
        return _json(rpc.error_response(errors.ACCESS_DENIED))

    # 2) Basic auth — UWS_JSON 2.2: "система должна вернуть HTTP статус 401".
    #    Bu yagona joy bo'lib, JSON-RPC xatosi emas, HTTP statusi talab qilinadi.
    if not security.basic_auth_ok(request):
        response = _json(rpc.error_response(errors.BAD_LOGIN), status=401)
        response["WWW-Authenticate"] = 'Basic realm="paynet"'
        return response

    # 3) Metod POST bo'lishi shart. `require_POST` dekoratori ATAYLAB
    #    ishlatilmagan — u HTTP 405 qaytaradi, spetsifikatsiya esa aynan
    #    -32300 kodli JSON-RPC javobini talab qiladi.
    if request.method != "POST":
        return _json(rpc.error_response(errors.NOT_POST))

    return _json(rpc.dispatch(request.body))
