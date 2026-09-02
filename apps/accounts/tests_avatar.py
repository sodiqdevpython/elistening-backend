"""Avatar validatori (`MeUpdateSerializer.validate_avatar`) + admin readonly fix."""
import io

from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from apps.accounts.models import User
from apps.accounts.serializers import AVATAR_MAX_BYTES, MeUpdateSerializer
from apps.common.admin import AppAdAdmin
from apps.common.models import AppAd


def png_bytes(size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 180, 120)).save(buf, format="PNG")
    return buf.getvalue()


def upload(name: str, data: bytes, content_type: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type=content_type)


class AvatarValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="u1")

    def update(self, avatar):
        return MeUpdateSerializer(instance=self.user, data={"avatar": avatar}, partial=True)

    def test_normal_png_is_accepted(self):
        s = self.update(upload("a.png", png_bytes(), "image/png"))
        self.assertTrue(s.is_valid(), s.errors)

    def test_non_image_is_rejected(self):
        """Nomi `.jpg` bo'lgan matn fayl o'tmasligi kerak."""
        s = self.update(upload("fake.jpg", b"bu rasm emas, oddiy matn", "image/jpeg"))
        self.assertFalse(s.is_valid())
        self.assertIn("avatar", s.errors)

    def test_too_large_file_is_rejected(self):
        big = png_bytes() + b"\0" * (AVATAR_MAX_BYTES + 1)
        s = self.update(upload("big.png", big, "image/png"))
        self.assertFalse(s.is_valid())
        self.assertIn("MB", str(s.errors["avatar"][0]))

    def test_mislabelled_but_real_image_is_accepted(self):
        """Django `content_type` ni RASMNING O'ZIDAN qayta aniqlaydi.

        Ya'ni allowlist mijoz bergan yorliqqa emas, haqiqiy formatga
        qaraydi — bu kuchliroq. PNG ni "application/pdf" deb yuborsa ham
        u baribir PNG, shu bois qabul qilinadi.
        """
        s = self.update(upload("doc.pdf", png_bytes(), "application/pdf"))
        self.assertTrue(s.is_valid(), s.errors)

    def test_unsupported_image_format_is_rejected(self):
        """TIFF — haqiqiy rasm, lekin allowlist'da yo'q."""
        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (1, 2, 3)).save(buf, format="TIFF")
        s = self.update(upload("a.tiff", buf.getvalue(), "image/tiff"))
        self.assertFalse(s.is_valid())

    def test_huge_dimensions_are_rejected(self):
        s = self.update(upload("wide.png", png_bytes((5000, 40)), "image/png"))
        self.assertFalse(s.is_valid())
        self.assertIn("4096", str(s.errors["avatar"][0]))

    def test_other_fields_still_work_without_avatar(self):
        s = MeUpdateSerializer(instance=self.user, data={"display_name": "Ali"}, partial=True)
        self.assertTrue(s.is_valid(), s.errors)


class AdminReadonlyFieldsTests(TestCase):
    """`BaseModelAdmin.get_readonly_fields` admin METODLARINI tashlamasin.

    Ilgari u faqat model maydonlarini qoldirar, natijada `AppAdAdmin.preview`
    formadan chiqarilmay "Unknown field(s) (preview)" `FieldError` bilan
    admin sahifasi umuman ochilmasdi.
    """

    def test_preview_survives_the_filter(self):
        admin = AppAdAdmin(AppAd, AdminSite())
        self.assertIn("preview", admin.get_readonly_fields(request=None))

    def test_form_can_be_built(self):
        """Asl xato aynan shu yerda chiqardi."""
        admin = AppAdAdmin(AppAd, AdminSite())
        form = admin.get_form(request=None, obj=None)
        self.assertNotIn("preview", form.base_fields)
        self.assertIn("title", form.base_fields)

    def test_missing_timestamp_fields_are_still_dropped(self):
        """Filtrning ASL vazifasi ham saqlanib qolsin."""
        admin = AppAdAdmin(AppAd, AdminSite())
        admin.readonly_fields = ("preview", "created_at", "bunday_maydon_yoq")
        self.assertEqual(admin.get_readonly_fields(request=None), ("preview", "created_at"))
