from rest_framework import serializers

from .models import Plan


class PlanSerializer(serializers.ModelSerializer):
    price_label_uz = serializers.CharField(read_only=True)
    price_label_en = serializers.CharField(read_only=True)

    class Meta:
        model = Plan
        fields = ("id", "code", "name_uz", "name_en", "price_uzs", "price_usd",
                  "price_label_uz", "price_label_en", "features_uz", "features_en",
                  "is_default", "order",
                  "daily_shorts_limit", "daily_video_limit",
                  "daily_dictation_limit", "daily_ielts_limit")
