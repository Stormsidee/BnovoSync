from django.contrib import admin

from .models import (
    ApiToken,
    Arrival,
    Booking,
    BookingDetail,
    DashboardSnapshot,
    EndpointSetting,
    FinanceItem,
    Guest,
    IntegrationSettings,
    Invoice,
    LegalEntity,
    Payment,
    Room,
    RoomType,
    Service,
    SyncLog,
    SyncRun,
    TariffPlan,
    TariffPrice,
    WorkerLease,
)


@admin.register(IntegrationSettings)
class IntegrationSettingsAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'base_url',
        'auto_sync_enabled',
        'auto_sync_interval_minutes',
        'aggregation_enabled',
        'aggregation_interval_minutes',
        'sync_window_days',
    )


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'job_code',
        'status',
        'triggered_by',
        'total_requests',
        'successful_requests',
        'failed_requests',
        'created_at',
    )
    list_filter = ('job_code', 'status', 'triggered_by')
    search_fields = ('note',)


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'run', 'level', 'endpoint', 'created_at')
    list_filter = ('level', 'endpoint')
    search_fields = ('message',)


@admin.register(EndpointSetting)
class EndpointSettingAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'enabled', 'updated_at')
    list_filter = ('enabled',)
    search_fields = ('code', 'name')


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ('id', 'token', 'created_at', 'expires_at', 'revoked_at')
    search_fields = ('token',)


@admin.register(WorkerLease)
class WorkerLeaseAdmin(admin.ModelAdmin):
    list_display = ('code', 'locked_until', 'last_started_at', 'last_success_at')


@admin.register(Arrival)
class ArrivalAdmin(admin.ModelAdmin):
    list_display = (
        'booking_bnovo_id',
        'customer_full_name',
        'arrival_at',
        'departure_at',
        'status_name',
        'amount',
        'paid_amount',
        'balance_amount',
    )
    list_filter = ('status_name', 'source_name')
    search_fields = (
        'booking_bnovo_id',
        'booking_number',
        'customer_full_name',
        'customer_phone',
        'customer_email',
    )


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ('bnovo_id', 'arrival_at', 'departure_at', 'property_id', 'fetched_at')
    search_fields = ('bnovo_id', 'property_id')


@admin.register(BookingDetail)
class BookingDetailAdmin(admin.ModelAdmin):
    list_display = ('bnovo_id', 'arrival_at', 'departure_at', 'property_id', 'fetched_at')
    search_fields = ('bnovo_id', 'property_id')


admin.site.register(Service)
admin.site.register(RoomType)
admin.site.register(Room)
admin.site.register(TariffPlan)
admin.site.register(TariffPrice)
admin.site.register(Guest)
admin.site.register(Invoice)
admin.site.register(Payment)
admin.site.register(FinanceItem)
admin.site.register(LegalEntity)
admin.site.register(DashboardSnapshot)
