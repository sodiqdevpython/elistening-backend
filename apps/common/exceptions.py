"""API xatoliklarini bir xil formatda qaytarish."""
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None

    detail = response.data
    if isinstance(detail, dict) and "detail" in detail and len(detail) == 1:
        payload = {"error": {"message": str(detail["detail"]), "fields": {}}}
    elif isinstance(detail, dict):
        payload = {"error": {"message": "Ma'lumotlar noto'g'ri", "fields": detail}}
    else:
        payload = {"error": {"message": str(detail), "fields": {}}}

    payload["error"]["status"] = response.status_code
    response.data = payload
    return response
