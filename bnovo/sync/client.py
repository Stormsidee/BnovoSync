import time
import httpx 
 
class BnovoError(Exception): 
    pass 
 
class BnovoAuthError(BnovoError): 
    pass 
 
class BnovoResponseError(BnovoError): 
    pass 
 
class BnovoClient: 
    def __init__(self, base_url, sid, rate_limiter, timeout_seconds=30, verify_ssl=True): 
        self.base_url = (base_url or '').rstrip('/') 
        self.rate_limiter = rate_limiter 
        headers = { 
            'Accept': 'application/json', 
            'Content-Type': 'application/json', 
            'X-Requested-With': 'XMLHttpRequest',
        } 
        cookies = {} 
        if sid: 
            cookies['SID'] = sid 
        self.client = httpx.Client( 
            base_url=self.base_url, 
            headers=headers, 
            cookies=cookies, 
            timeout=timeout_seconds, 
            verify=verify_ssl, 
            follow_redirects=True,
        ) 
 
    def set_sid(self, sid):
        if sid:
            self.client.cookies.set('SID', sid)
 
    def login(self, login, password):
        if not login or not password:
            raise BnovoAuthError('Login or password is missing')
        response = self.client.post(
            '/',
            json={'username': login, 'password': password},
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
            follow_redirects=False,
        )
        sid = response.cookies.get('SID') or self.client.cookies.get('SID')
        if not sid:
            raise BnovoAuthError(
                f'Login failed: HTTP {response.status_code}, body="{response.text[:200]}"'
            )
        self.set_sid(sid)
        return sid
 
    def request(self, method, path, params=None, json=None, retries=3): 
        if not self.base_url: 
            raise BnovoResponseError('Base URL is not configured') 
        last_error = None
        for attempt in range(1, max(1, retries) + 1):
            self.rate_limiter.wait()
            try:
                response = self.client.request(method, path, params=params, json=json)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(min(2 ** attempt, 10))
                continue
            if response.status_code in (401, 403):
                raise BnovoAuthError('Bnovo authentication failed (SID may be expired)')
            content_type = (response.headers.get('content-type') or '').lower()
            try:
                data = response.json()
            except Exception:
                snippet = response.text[:200].replace('\n', ' ').replace('\r', ' ')
                raise BnovoResponseError(
                    f'Non-JSON response: HTTP {response.status_code}, content-type={content_type}, body="{snippet}"'
                )
            if response.is_error:
                raise BnovoResponseError(f'HTTP {response.status_code}: {data}')
            return data
        raise BnovoResponseError(f'HTTP request failed after {retries} attempts: {last_error}')
