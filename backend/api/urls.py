from django.urls import path
from . import grade_views, views

urlpatterns = [
    path("convert/", views.convert_docx, name="convert_docx"),
    path("health/", views.health, name="health"),
    path("grades/inspect/", grade_views.inspect, name="grades_inspect"),
    path("grades/add-to-classlist/", grade_views.add_to_classlist, name="grades_add"),
    path("grades/announce/", grade_views.announce, name="grades_announce"),
]
