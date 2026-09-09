"""Paynet xato kodlari (UWS_JSON spetsifikatsiyasi, 2.5-bo'lim).

Ikki oila bor va ular ARALASHTIRILMAYDI:

* **Protokol xatolari** (`-327xx`) — JSON-RPC standarti: so'rov umuman
  tushunarsiz (POST emas, JSON buzuq, `method` yo'q, ...).
* **Biznes xatolari** (musbat: 0, 77, 302, 413, ...) — so'rov to'g'ri, lekin
  amalni bajarib bo'lmadi (mijoz topilmadi, summa noto'g'ri, ...).

> **Diqqat — spetsifikatsiyadagi ziddiyat.** 2.5-bo'lim ro'yxati ikkala
> oilani bir joyda beradi, lekin 2.4-dagi MISOL `"code": -253` ni ko'rsatadi
> (ro'yxatda umuman yo'q kod). Biz biznes kodini MUSBAT yozamiz — sohada
> shunday qabul qilingan. Paynet integratsiya testida manfiy talab qilinsa,
> `SIGN` ni -1 ga o'zgartirish YETARLI, boshqa hech narsaga tegilmaydi.
"""

#: Biznes kodining ishorasi. Paynet manfiy talab qilsa: `SIGN = -1`.
SIGN = 1

# ── Protokol (JSON-RPC) ───────────────────────────────────────────────────
NOT_POST = -32300          # So'rov metodi POST emas
PARSE_ERROR = -32700       # JSON parse xatosi
INVALID_REQUEST = -32600   # Majburiy maydon yo'q yoki turi noto'g'ri
METHOD_NOT_FOUND = -32601  # Bunday metod yo'q
INVALID_PARAMS = -32602    # `params` ichida majburiy maydon yo'q
INTERNAL_ERROR = -32603    # Ichki nosozlik (DB, kutilmagan istisno)

# ── Biznes ────────────────────────────────────────────────────────────────
OK = 0
NOT_ENOUGH_TO_CANCEL = 77   # Bekor qilish uchun mijoz hisobida mablag' yetmaydi
SERVICE_UNAVAILABLE = 100   # Xizmat vaqtincha qo'llab-quvvatlanmaydi
QUOTA_EXCEEDED = 101
SYSTEM_ERROR = 102
UNKNOWN_ERROR = 103
TRN_EXISTS = 201            # Tranzaksiya allaqachon mavjud (boshqa parametrlar bilan)
TRN_CANCELLED = 202         # Tranzaksiya allaqachon bekor qilingan
TRN_NOT_FOUND = 203
NUMBER_NOT_EXIST = 301
CLIENT_NOT_FOUND = 302      # Mijoz topilmadi — bizda: bunday telegram_id yo'q
PRODUCT_NOT_FOUND = 304
SERVICE_NOT_FOUND = 305     # `serviceId` bizniki emas
INVALID_PARAM_1 = 401       # `fields.client_id` validatsiyasi
MISSING_PARAMS = 411
BAD_LOGIN = 412
BAD_AMOUNT = 413
BAD_DATETIME = 414
AMOUNT_TOO_BIG = 415
TRN_FORBIDDEN = 501         # Bu to'lovchi uchun tranzaksiyalar taqiqlangan
ACCESS_DENIED = 601         # IP ro'yxatda yo'q
BAD_COMMAND = 603

#: Kod → matn. Paynet loglarida shu matn ko'rinadi, shu bois hujjatdagi
#: rus tilidagi iboralar AYNAN saqlangan (o'zbekchaga tarjima qilinmaydi).
MESSAGES = {
    NOT_POST: "Method not allowed, POST expected",
    PARSE_ERROR: "Parse error",
    INVALID_REQUEST: "Invalid request",
    METHOD_NOT_FOUND: "Method not found",
    INVALID_PARAMS: "Invalid params",
    INTERNAL_ERROR: "Internal error",
    OK: "Проведено успешно",
    NOT_ENOUGH_TO_CANCEL: "Недостаточно средств на счету клиента для отмены платежа",
    SERVICE_UNAVAILABLE: "Услуга временно не поддерживается",
    QUOTA_EXCEEDED: "Квота исчерпана",
    SYSTEM_ERROR: "Системная ошибка",
    UNKNOWN_ERROR: "Неизвестная ошибка",
    TRN_EXISTS: "Транзакция уже существует",
    TRN_CANCELLED: "Транзакция уже отменена",
    TRN_NOT_FOUND: "Транзакция не найдена",
    NUMBER_NOT_EXIST: "Номер не существует",
    CLIENT_NOT_FOUND: "Клиент не найден",
    PRODUCT_NOT_FOUND: "Товар не найден",
    SERVICE_NOT_FOUND: "Услуга не найдена",
    INVALID_PARAM_1: "Ошибка валидации параметра 1",
    MISSING_PARAMS: "Не заданы один или несколько обязательных параметров",
    BAD_LOGIN: "Неверный логин или пароль",
    BAD_AMOUNT: "Неверная сумма",
    BAD_DATETIME: "Неверный формат даты и времени",
    AMOUNT_TOO_BIG: "Сумма превышает максимальный лимит",
    TRN_FORBIDDEN: "Транзакции запрещены для данного плательщика",
    ACCESS_DENIED: "Доступ запрещен",
    BAD_COMMAND: "Неправильный код команды",
}


class PaynetError(Exception):
    """Metod ichidan otiladi — `rpc.dispatch` uni JSON-RPC `error` ga aylantiradi.

    Biznes kodiga `SIGN` shu yerda BIR MARTA qo'llanadi, protokol kodlari
    (manfiy `-327xx`) esa o'zgarishsiz qoladi.
    """

    def __init__(self, code: int, message: str = ""):
        self.code = code if code < 0 else code * SIGN
        self.message = message or MESSAGES.get(code, "Error")
        super().__init__(f"{self.code}: {self.message}")

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message}
