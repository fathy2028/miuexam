from django.urls import path
from . import views

urlpatterns = [
    path("convert/", views.convert_docx, name="convert_docx"),
    path("health/", views.health, name="health"),
]
