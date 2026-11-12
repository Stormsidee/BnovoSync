from django import forms
from .models import IntegrationSettings

class IntegrationSettingsForm(forms.ModelForm):
    api_password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True))

    class Meta:
        model = IntegrationSettings
        fields = [
            'base_url',
            'api_login',
            'api_password',
            'date_from',
            'date_to',
            'sync_window_days',
            'auto_sync_interval_minutes',
            'aggregation_interval_minutes',
            'rate_limit_per_min',
            'timeout_seconds',
            'verify_ssl',
        ]
        widgets = {
            'base_url': forms.TextInput(attrs={'placeholder': 'https://online.bnovo.ru'}),
            'date_from': forms.DateInput(attrs={'type': 'date'}),
            'date_to': forms.DateInput(attrs={'type': 'date'}),
            'sync_window_days': forms.NumberInput(attrs={'min': 1, 'step': 1}),
            'auto_sync_interval_minutes': forms.NumberInput(attrs={'min': 1, 'step': 1}),
            'aggregation_interval_minutes': forms.NumberInput(attrs={'min': 1, 'step': 1}),
        }

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not self.cleaned_data.get('api_password') and self.instance and self.instance.pk:
            obj.api_password = IntegrationSettings.objects.get(pk=self.instance.pk).api_password
        if commit:
            obj.save()
        return obj
