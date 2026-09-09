"""JSON-RPC 2.0 konverti — so'rovni ochish, javobni yig'ish.

Bu yerda pul mantig'i YO'Q (u `service.py` da). Bu qatlamning yagona vazifasi:
UWS_JSON 2.3–2.5 bo'limlariga qat'iy rioya qilish.

**`id` haqida.** Xato yuz bersa ham javobdagi `id` so'rovdagiga TENG bo'lishi
shart (2.4-bo'lim). JSON umuman parse bo'lmasa `id` ni bilishning iloji yo'q —
faqat o'shanda `null` qaytadi.
"""
import json
import logging

from . import errors, service

log = logging.getLogger("paynet")

#: Metod nomi → `service` funksiyasi. Ro'yxatda yo'q nom = -32601.
METHODS = {
    "GetInformation": service.get_information,
    "PerformTransaction": service.perform_transaction,
    "CheckTransaction": service.check_transaction,
    "CancelTransaction": service.cancel_transaction,
    "GetStatement": service.get_statement,
    "ChangePassword": service.change_password,
}


def error_response(code: int, request_id=None, message: str = "") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message or errors.MESSAGES.get(code, "Error")},
    }


def dispatch(body: bytes) -> dict:
    """Xom so'rov tanasi → JSON-RPC javob lug'ati.

    HECH QACHON istisno otmaydi: Paynet tomonda 500 emas, tushunarli JSON-RPC
    xatosi ko'rinishi kerak.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return error_response(errors.PARSE_ERROR)

    if not isinstance(payload, dict):
        return error_response(errors.INVALID_REQUEST)

    request_id = payload.get("id")
    method = payload.get("method")

    if payload.get("jsonrpc") != "2.0" or not isinstance(method, str) or "id" not in payload:
        return error_response(errors.INVALID_REQUEST, request_id)

    # Hujjat misollarida metod nomi ba'zan bo'sh joy bilan keladi
    # (`"method": " CancelTransaction"`) — buning uchun butun integratsiyani
    # to'xtatib qo'ymaymiz.
    handler = METHODS.get(method.strip())
    if handler is None:
        return error_response(errors.METHOD_NOT_FOUND, request_id)

    params = payload.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return error_response(errors.INVALID_PARAMS, request_id)

    try:
        result = handler(params)
    except errors.PaynetError as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": exc.as_dict()}
    except Exception:  # noqa: BLE001
        # Kutilmagan nosozlik (DB uzildi va h.k.) — to'liq stack log'ga,
        # Paynet'ga esa standart -32603.
        log.exception("Paynet method %s failed", method)
        return error_response(errors.INTERNAL_ERROR, request_id)

    return {"jsonrpc": "2.0", "id": request_id, "result": result}
