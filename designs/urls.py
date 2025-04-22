from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DesignViewSet, TemplateViewSet, MockupViewSet,GenerateImageView

# Create a single router
router = DefaultRouter()
router.register('uploads', DesignViewSet, basename='design')
router.register('templates', TemplateViewSet, basename='template')
router.register('mockups', MockupViewSet, basename='mockup')

urlpatterns = [
    path('', include(router.urls)),
    path('generate-image/', GenerateImageView.as_view(), name='generate-image'),
]