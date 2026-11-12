from django.db import models 
from django.utils import timezone
 
class IntegrationSettings(models.Model): 
    base_url = models.CharField(max_length=255, default='https://online.bnovo.ru') 
    sid = models.TextField(blank=True) 
    api_login = models.CharField(max_length=255, blank=True) 
    api_password = models.TextField(blank=True) 
    last_auth_at = models.DateTimeField(null=True, blank=True)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    auto_sync_enabled = models.BooleanField(default=False)
    auto_sync_interval_minutes = models.PositiveIntegerField(default=30)
    last_auto_sync_at = models.DateTimeField(null=True, blank=True)
    sync_window_days = models.PositiveIntegerField(default=30)
    aggregation_enabled = models.BooleanField(default=False)
    aggregation_interval_minutes = models.PositiveIntegerField(default=10)
    last_aggregation_sync_at = models.DateTimeField(null=True, blank=True)
    rate_limit_per_min = models.PositiveIntegerField(default=9) 
    timeout_seconds = models.PositiveIntegerField(default=30) 
    verify_ssl = models.BooleanField(default=True) 
    created_at = models.DateTimeField(auto_now_add=True) 
    updated_at = models.DateTimeField(auto_now=True) 
 
    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs) 
 
    @classmethod 
    def get_solo(cls): 
        obj, _ = cls.objects.get_or_create(pk=1) 
        return obj
 
class SyncRun(models.Model): 
    STATUS_CHOICES = [ 
        ('queued', 'Queued'), 
        ('running', 'Running'), 
        ('success', 'Success'), 
        ('failed', 'Failed'), 
        ('cancelled', 'Cancelled'),
    ] 
    JOB_CHOICES = [
        ('raw_sync', 'Raw sync'),
        ('arrival_aggregation', 'Arrival aggregation'),
    ]
    TRIGGER_CHOICES = [ 
        ('manual', 'Manual'), 
        ('schedule', 'Schedule'), 
        ('api', 'API'), 
    ] 
    job_code = models.CharField(max_length=32, choices=JOB_CHOICES, default='raw_sync')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='queued') 
    triggered_by = models.CharField(max_length=16, choices=TRIGGER_CHOICES, default='manual') 
    created_at = models.DateTimeField(auto_now_add=True) 
    started_at = models.DateTimeField(null=True, blank=True) 
    finished_at = models.DateTimeField(null=True, blank=True) 
    total_requests = models.PositiveIntegerField(default=0) 
    successful_requests = models.PositiveIntegerField(default=0) 
    failed_requests = models.PositiveIntegerField(default=0) 
    note = models.TextField(blank=True) 
    cancel_requested = models.BooleanField(default=False)

    @property
    def duration_seconds(self):
        if not self.started_at:
            return None
        end = self.finished_at or timezone.now()
        return int((end - self.started_at).total_seconds())


class ApiToken(models.Model):
    token = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_active(self):
        if self.revoked_at:
            return False
        if self.expires_at and timezone.now() >= self.expires_at:
            return False
        return True
 
class SyncLog(models.Model): 
    LEVEL_CHOICES = [ 
        ('info', 'Info'), 
        ('warning', 'Warning'), 
        ('error', 'Error'), 
    ] 
    run = models.ForeignKey(SyncRun, on_delete=models.CASCADE, related_name='logs') 
    created_at = models.DateTimeField(auto_now_add=True) 
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='info') 
    endpoint = models.CharField(max_length=100, blank=True) 
    message = models.TextField()

class EndpointSetting(models.Model):
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class WorkerLease(models.Model):
    code = models.CharField(max_length=50, unique=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_finished_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    def __str__(self):
        return self.code

class BnovoEntityBase(models.Model): 
    bnovo_id = models.CharField(max_length=100, db_index=True) 
    account_id = models.CharField(max_length=100, blank=True, null=True) 
    property_id = models.CharField(max_length=100, blank=True, null=True) 
    payload = models.JSONField(default=dict, blank=True) 
    source_updated_at = models.DateTimeField(null=True, blank=True) 
    fetched_at = models.DateTimeField(auto_now_add=True) 
 
    class Meta: 
        abstract = True 
        indexes = [ 
            models.Index(fields=['bnovo_id']), 
            models.Index(fields=['property_id']), 
        ] 
 
class Booking(BnovoEntityBase): 
    arrival_at = models.DateTimeField(null=True, blank=True)
    departure_at = models.DateTimeField(null=True, blank=True)
    created_at_source = models.DateTimeField(null=True, blank=True)


class BookingDetail(BnovoEntityBase):
    arrival_at = models.DateTimeField(null=True, blank=True)
    departure_at = models.DateTimeField(null=True, blank=True)
    created_at_source = models.DateTimeField(null=True, blank=True)


class Arrival(models.Model):
    booking_bnovo_id = models.CharField(max_length=100, unique=True)
    account_id = models.CharField(max_length=100, blank=True, null=True)
    property_id = models.CharField(max_length=100, blank=True, null=True)
    booking_number = models.CharField(max_length=100, blank=True)
    room_bnovo_id = models.CharField(max_length=100, blank=True)
    room_type_bnovo_id = models.CharField(max_length=100, blank=True)
    customer_id = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=100, blank=True)
    source_name = models.CharField(max_length=255, blank=True)
    status_id = models.CharField(max_length=100, blank=True)
    status_name = models.CharField(max_length=255, blank=True)
    customer_full_name = models.CharField(max_length=255, blank=True)
    customer_phone = models.CharField(max_length=100, blank=True)
    customer_email = models.CharField(max_length=255, blank=True)
    visitor_names = models.JSONField(default=list, blank=True)
    visitor_details = models.JSONField(default=list, blank=True)
    services = models.JSONField(default=list, blank=True)
    payments = models.JSONField(default=list, blank=True)
    payload_excerpt = models.JSONField(default=dict, blank=True)
    adults = models.PositiveIntegerField(default=0)
    children = models.PositiveIntegerField(default=0)
    nights = models.PositiveIntegerField(null=True, blank=True)
    arrival_at = models.DateTimeField(null=True, blank=True)
    departure_at = models.DateTimeField(null=True, blank=True)
    room_type_name = models.CharField(max_length=255, blank=True)
    room_name = models.CharField(max_length=255, blank=True)
    plan_name = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    balance_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    created_at_source = models.DateTimeField(null=True, blank=True)
    raw_updated_at = models.DateTimeField(null=True, blank=True)
    raw_fetched_at = models.DateTimeField(null=True, blank=True)
    aggregated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['arrival_at']),
            models.Index(fields=['status_name']),
            models.Index(fields=['source_name']),
            models.Index(fields=['room_bnovo_id']),
        ]

    @property
    def visitor_count(self):
        return len(self.visitor_names or [])
 
class Service(BnovoEntityBase): 
    pass 
 
class RoomType(BnovoEntityBase): 
    pass 
 
class Room(BnovoEntityBase): 
    pass 
 
class TariffPlan(BnovoEntityBase): 
    pass 
 
class TariffPrice(BnovoEntityBase): 
    pass 
 
class Guest(BnovoEntityBase): 
    pass 
 
class Invoice(BnovoEntityBase): 
    pass 
 
class Payment(BnovoEntityBase): 
    pass 
 
class FinanceItem(BnovoEntityBase): 
    pass 
 
class LegalEntity(BnovoEntityBase): 
    pass 
 
class DashboardSnapshot(BnovoEntityBase): 
    pass
