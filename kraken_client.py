import base64, hashlib, hmac, json, time, socket
from urllib.parse import urlencode
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.util.connection import create_connection
import ssl
from config_factory import CFG
from kraken_nonce import KrakenNonce
from logging import getLogger
import certifi

LOG = getLogger("kraken_client")


# Custom HTTPAdapter to fix Windows socket issues
class WindowsHTTPAdapter(HTTPAdapter):
    """HTTPAdapter with Windows-specific socket configuration."""
    
    def init_poolmanager(self, *args, **kwargs):
        # Set socket options to fix Windows [Errno 22]
        kwargs['socket_options'] = [
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        ]
        return super().init_poolmanager(*args, **kwargs)


class KrakenClient:
    def __init__(self, api_key: str = None, api_secret: str = None, base_url: str = None):
        self.api_key = api_key or CFG.API_KEY
        self.api_secret = (api_secret or CFG.API_SECRET).encode()
        self.base_url = base_url or CFG.BASE_URL
        self.nonce_mgr = KrakenNonce(self.api_key, CFG.MODE)
        
        # Create session with proper SSL context for Windows
        self.sess = requests.Session()
        self.sess.headers.update({"User-Agent": "b4kraken/2.1-k"})
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        
        # Use Windows-specific adapter
        adapter = WindowsHTTPAdapter(max_retries=retry_strategy)
        self.sess.mount("https://", adapter)
        self.sess.mount("http://", adapter)
        
        # Windows-specific SSL fix
        try:
            import platform
            if platform.system() == 'Windows':
                self.sess.verify = True
                LOG.debug("Windows SSL configuration applied")
        except Exception as e:
            LOG.debug(f"Could not apply Windows SSL fix: {e}")
        
        # Simple rate limiter to reduce EAPI:Rate limit exceeded bursts
        self._min_interval_public = 0.30  # 3.3 req/s max per session (slower to be safe)
        self._min_interval_private = 0.50  # ~2 req/s max per session (safer)
        self._last_public = 0.0
        self._last_private = 0.0

    def _sleep_to_rate_limit(self, is_private: bool):
        now = time.time()
        if is_private:
            wait = self._min_interval_private - (now - self._last_private)
            if wait > 0:
                time.sleep(wait)
            self._last_private = time.time()
        else:
            wait = self._min_interval_public - (now - self._last_public)
            if wait > 0:
                time.sleep(wait)
            self._last_public = time.time()

    # Public
    def public_get(self, path: str, params: dict | None = None):
        self._sleep_to_rate_limit(is_private=False)
        url = self.base_url + path
        r = self.sess.get(url, params=params or {}, timeout=30)
        r.raise_for_status()
        return r.json()

    # Private
    def _sign(self, path: str, data: dict):
        postdata = urlencode(data)
        message = (path.encode() + hashlib.sha256((str(data['nonce']) + postdata).encode()).digest())
        mac = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)
        sigdigest = base64.b64encode(mac.digest())
        return sigdigest.decode()

    def _do_private_post(self, path: str, data: dict):
        data = data.copy() if data else {}
        data['nonce'] = self.nonce_mgr.next()
        headers = {
            'API-Key': self.api_key,
            'API-Sign': self._sign(path, data),
            'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8'
        }
        url = self.base_url + path
        
        # Windows fix: Create a fresh session with explicit SSL verification
        try:
            import platform
            if platform.system() == 'Windows':
                # Use a new session with explicit certifi bundle
                import certifi
                with requests.Session() as temp_sess:
                    temp_sess.headers.update({"User-Agent": "b4kraken/2.1-k"})
                    temp_sess.verify = certifi.where()  # Explicit CA bundle
                    r = temp_sess.post(url, data=data, headers=headers, timeout=30)
                    r.raise_for_status()
                    return r.json()
        except ImportError:
            pass  # certifi not available, fallback
        except Exception as e:
            LOG.debug(f"Windows session creation failed: {e}")
        
        # Non-Windows or fallback
        r = self.sess.post(url, data=data, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def private_post(self, path: str, data: dict | None = None):
        """
        Private POST with automatic recovery from EAPI:Invalid nonce and light rate limiting.
        Retries up to 3 times, bumping the shared nonce between attempts.
        Enhanced error handling for Windows SSL issues.
        """
        attempts = 0
        last_err = None
        while attempts < 3:
            try:
                self._sleep_to_rate_limit(is_private=True)
                j = self._do_private_post(path, data or {})
                if j.get('error'):
                    err_list = j['error']
                    # Bubble up errors (handled by callers with backoff)
                    raise RuntimeError(f"Kraken error: {err_list}")
                return j.get('result', j)
            except requests.exceptions.SSLError as e:
                LOG.warning(f"SSL error on attempt {attempts + 1}: {e}")
                last_err = e
                time.sleep(0.5 * (attempts + 1))
                attempts += 1
                continue
            except requests.exceptions.ConnectionError as e:
                LOG.warning(f"Connection error on attempt {attempts + 1}: {e}")
                last_err = e
                time.sleep(0.5 * (attempts + 1))
                attempts += 1
                continue
            except OSError as e:
                # Windows-specific [Errno 22] Invalid argument
                if e.errno == 22:
                    LOG.warning(f"Windows socket error on attempt {attempts + 1}: {e}")
                    last_err = e
                    time.sleep(0.5 * (attempts + 1))
                    attempts += 1
                    continue
                raise
            except Exception as e:
                msg = str(e)
                last_err = e
                if 'EAPI:Invalid nonce' in msg:
                    # Bump shared nonce and retry
                    delta = 10 ** (6 + attempts)  # microseconds scale
                    try:
                        self.nonce_mgr.bump(delta)
                    except Exception:
                        pass
                    time.sleep(0.05)
                    attempts += 1
                    continue
                break
        if last_err:
            raise last_err
        raise RuntimeError("Unknown Kraken private_post error")