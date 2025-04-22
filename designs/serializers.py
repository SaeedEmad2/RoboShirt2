# designs/serializers.py
from rest_framework import serializers
from .models import Design
from .models import Template
from .models import Mockup

class DesignSerializer(serializers.ModelSerializer):
    class Meta:
        model = Design
        fields = ['id', 'design_description', 'customer', 'created_at', 'design_file', 'file_type']
        read_only_fields = ['created_at']

class TemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Template
        fields = ['id', 'category', 'image', 'description']

class MockupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mockup
        fields = ['id', 'design', 'color', 'size', 'mockup_image', 'created_at']
        read_only_fields = ['mockup_image', 'created_at']

class MockupPreviewSerializer(serializers.Serializer):
    design_id = serializers.IntegerField()
    color = serializers.ChoiceField(choices=Mockup.COLOR_CHOICES)
    size = serializers.ChoiceField(choices=Mockup.SIZE_CHOICES)