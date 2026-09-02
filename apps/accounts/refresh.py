"""Sessiyaga bog'liq token refresh — yangi access'ga sid/plat ni ko'chiradi va
superseded sessiyani rad etadi. (auth.py dan ALOHIDA — aylanma importni oldini oladi.)
"""
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from .auth import session_ok


class SessionTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        refresh = RefreshToken(attrs["refresh"])
        sid = refresh.get("sid")
        plat = refresh.get("plat")
        if not session_ok(refresh.get("user_id"), plat, sid):
            raise InvalidToken("Boshqa qurilmada kirildi")

        access = refresh.access_token
        if sid:
            access["sid"] = sid
        if plat:
            access["plat"] = plat
        data = {"access": str(access)}

        if api_settings.ROTATE_REFRESH_TOKENS:
            if api_settings.BLACKLIST_AFTER_ROTATION:
                try:
                    refresh.blacklist()
                except AttributeError:
                    pass
            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            if sid:
                refresh["sid"] = sid
            if plat:
                refresh["plat"] = plat
            data["refresh"] = str(refresh)
        return data


class SessionTokenRefreshView(TokenRefreshView):
    serializer_class = SessionTokenRefreshSerializer
