"""Kontentni "o'ralgan" holda yuborish — oddiy scraping'ga qarshi to'siq.

## Bu NIMA emas

Bu **shifrlash emas, obfuskatsiya**. Kalit mijozga (sayt bundle'i va mobil
ilova) baribir yetib boradi — aks holda ular ochib ko'rsata olmasdi. Ya'ni
qat'iy qaror qilgan odam bundle'dan kalitni topib, hamma narsani ocha oladi.

Nima beradi: `curl https://.../api/shorts/` deb JSON'ni olib, transkript va
savollarni **tayyor holda** ko'chirib ketish endi ishlamaydi. Skript yozgan
odam avval bizning kodimizni o'qib, algoritmni qayta yozishi kerak bo'ladi.
Ko'p hollarda shu yetadi.

Shu bois bu yerda "haqiqiy" kripto (AES-GCM, kalit almashinuvi) qilinmagan:
u ham xuddi shu darajada ochiq bo'lardi, lekin sekinroq va og'irroq.
Foydalanuvchi so'ragan narsa aynan shu — **tez va yengil** to'siq.

## Format

    "v1:" + base64( offset(2 bayt, big-endian) + xor(json_bytes) )

- JSON `ensure_ascii=True` bilan yoziladi (json standarti) — ya'ni bayt oqimi
  sof ASCII bo'ladi va mijozda UTF-8 dekoderi kerak emas (tezroq, kamroq kod).
- Kalit oqimi (`keystream`) parol'dan RC4 (drop-256) bilan bir marta olinadi
  va keshlanadi; har javob undan tasodifiy `offset` dan boshlab foydalanadi,
  shu bois bir xil kontent har safar boshqacha ko'rinadi.
- XOR katta butun son ustida bajariladi (`int.from_bytes`) — bu C tezligida,
  Python siklidan ~100x tez. 50 KB payload uchun ~0.2 ms.

Mijoz tomondagi aynan shu algoritm:
`frontend/src/utils/protect.ts` va `mobile/src/utils/protect.ts`.
**Uchalasini birga o'zgartiring**, aks holda kontent ochilmay qoladi.
"""
import base64
import json
import secrets
from functools import lru_cache

from django.conf import settings

#: Format belgisi — kelajakda algoritm o'zgarsa mijoz eskisini ham tushunadi.
PREFIX = "v1:"

#: Kalit oqimi jadvalining uzunligi. Kichik bo'lsa kesh arzon, uzun payload
#: uchun esa jadval takrorlanadi (obfuskatsiya uchun bu yetarli).
TABLE_LEN = 4096


def passphrase() -> str:
    """Parol — `settings.CONTENT_SECRET`. Mijozlarda ham AYNAN shu bo'lishi shart."""
    return getattr(settings, "CONTENT_SECRET", "") or "sodiq2005.py"


def enabled() -> bool:
    return bool(getattr(settings, "PROTECT_CONTENT", True))


@lru_cache(maxsize=4)
def _keystream(secret: str) -> bytes:
    """RC4 (drop-256) kalit oqimi — parol uchun bir marta hisoblanadi."""
    key = secret.encode("utf-8") or b"x"
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]

    out = bytearray(TABLE_LEN)
    i = j = 0
    # RC4 ning birinchi baytlari qiya taqsimlangan — 256 tasini tashlaymiz.
    for _ in range(256):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
    for n in range(TABLE_LEN):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[n] = s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def _xor(data: bytes, secret: str, offset: int) -> bytes:
    if not data:
        return b""
    table = _keystream(secret)
    need = offset + len(data)
    reps = need // TABLE_LEN + 2
    stream = (table * reps)[offset:offset + len(data)]
    mixed = int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")
    return mixed.to_bytes(len(data), "big")


def protect(payload) -> str:
    """Istalgan JSON-ga aylantiriladigan qiymatni `v1:...` satriga o'raydi."""
    secret = passphrase()
    raw = json.dumps(payload, separators=(",", ":")).encode("ascii", "backslashreplace")
    offset = secrets.randbelow(TABLE_LEN)
    blob = offset.to_bytes(2, "big") + _xor(raw, secret, offset)
    return PREFIX + base64.b64encode(blob).decode("ascii")


def unprotect(blob: str):
    """Teskarisi — asosan testlar va `manage.py` skriptlari uchun."""
    if not isinstance(blob, str) or not blob.startswith(PREFIX):
        raise ValueError("Noto'g'ri format")
    data = base64.b64decode(blob[len(PREFIX):])
    offset = int.from_bytes(data[:2], "big")
    return json.loads(_xor(data[2:], passphrase(), offset).decode("ascii"))


class ProtectedFieldsMixin:
    """Serializer aralashmasi: sanab o'tilgan maydonlarni bitta `enc` ga yig'adi.

    Nega bitta blob: har maydonni alohida o'rasak, kelajakda bittasini
    unutish oson bo'lardi va mijozda ham har biri uchun alohida ochish kerak
    bo'lardi. Bitta blob — bitta joy, bitta qoida.

    `PROTECT_CONTENT=False` bo'lsa hech narsa o'zgarmaydi (admin/debug uchun).
    """

    #: Qaysi maydonlar javobdan olinib, `enc` ichiga kiradi.
    PROTECTED_FIELDS: tuple[str, ...] = ()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.PROTECTED_FIELDS or not enabled():
            return data
        hidden = {name: data.pop(name) for name in self.PROTECTED_FIELDS if name in data}
        if hidden:
            data["enc"] = protect(hidden)
        return data
