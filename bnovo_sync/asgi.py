import os 
from django.core.asgi import get_asgi_application 
from starlette.applications import Starlette 
from starlette.routing import Mount 
 
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bnovo_sync.settings') 
 
django_app = get_asgi_application() 
from bnovo_sync.fastapi_app import app as fastapi_app 

application = Starlette(routes=[ 
    Mount('/api', app=fastapi_app), 
    Mount('/', app=django_app), 
])
