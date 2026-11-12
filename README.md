# BnovoSync

Сервис синхронизации бронирований с Bnovo. Сохраняет исходные данные и собирает карточку заезда с датами, номером, гостями, услугами и оплатами. Другие приложения могут получить карточку по ID бронирования или номера.

## Как устроено

- Django отвечает за хранение, настройки синхронизации и журнал запусков.
- Клиент Bnovo работает с ограничением частоты запросов. Фоновый воркер запускает синхронизацию по расписанию.
- FastAPI отдаёт карточки заездов через `/api`. Защищённые запросы принимают `X-API-Token` или Bearer-токен.
- Локальный запуск использует SQLite. PostgreSQL можно подключить через `DATABASE_URL`.

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python -m uvicorn bnovo_sync.asgi:application --host 127.0.0.1 --port 8000
curl --fail http://127.0.0.1:8000/api/health
```

В интерфейсе сервиса можно создать API-токен и настроить синхронизацию. Для подключения к Bnovo нужны доступы тестового аккаунта.

```bash
curl --fail 'http://127.0.0.1:8000/api/arrival-summary/booking-ids?arrival_from=2026-01-01'   -H "X-API-Token: ${BNOVOSYNC_API_TOKEN}"
python manage.py test
```
