"""Click SHOP-API xato kodlari.

Manba — Click'ning RASMIY PHP kutubxonasi
(`github.com/click-llc/click-integration-php`, `BasicPaymentsErrors.php`).
Hujjat sayti (docs.click.uz) to'liq client-rendered bo'lgani uchun kodlar
o'sha kutubxonadan olingan: u Click'ning o'zi chiqargan referens
implementatsiya, ya'ni testlash dasturi aynan shu kodlarni kutadi.

Har javobda `error` va `error_note` bo'lishi SHART — Click `error` ni
o'qib to'lovni davom ettiradi yoki bekor qiladi.
"""

SUCCESS = 0
SIGN_CHECK_FAILED = -1
INCORRECT_AMOUNT = -2
ACTION_NOT_FOUND = -3
ALREADY_PAID = -4
USER_NOT_FOUND = -5
TRANSACTION_NOT_FOUND = -6
FAILED_TO_UPDATE = -7
BAD_REQUEST = -8
TRANSACTION_CANCELLED = -9

#: Kod → matn. Click loglarida va testlash dasturida shu matn ko'rinadi,
#: shu bois kutubxonadagi INGLIZCHA iboralar aynan saqlangan.
NOTES = {
    SUCCESS: "Success",
    SIGN_CHECK_FAILED: "SIGN CHECK FAILED!",
    INCORRECT_AMOUNT: "Incorrect parameter amount",
    ACTION_NOT_FOUND: "Action not found",
    ALREADY_PAID: "Already paid",
    USER_NOT_FOUND: "User does not exist",
    TRANSACTION_NOT_FOUND: "Transaction does not exist",
    FAILED_TO_UPDATE: "Failed to update user",
    BAD_REQUEST: "Error in request from click",
    TRANSACTION_CANCELLED: "Transaction cancelled",
}


def note(code: int) -> str:
    return NOTES.get(code, "Unknown error")
