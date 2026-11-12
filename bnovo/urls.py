from django.urls import path 
from . import views 
 
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('endpoints/', views.endpoints, name='endpoints'),
    path('logs/', views.logs, name='logs'),
    path('tokens/', views.tokens, name='tokens'),
    path('runs/<int:run_id>/stop/', views.stop_run, name='stop_run'),
]
