"""Thin Dhan v2 REST client: credentials, rate-limit throttling, data calls.

Credentials come from set_credentials() when the dashboard supplies them (cloud),
otherwise from config/.env (local).
"""
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
ENV_FILE = ROOT / "config" / ".env"
load_dotenv(ENV_FILE)

IST = timezone(timedelta(hours=5, minutes=30))
BASE = "https://api.dhan.co/v2"
# Minimum seconds between calls, per Dhan's published limits (with a small margin).
_GAP = {"quote": 1.1, "chain": 3.1, "hist": 0.25, "intraday": 0.25}
_last = {}
# One lock per call type, so two browser sessions cannot break the rate limit together.
_locks = {k: threading.Lock() for k in _GAP}
# Credentials set while the app runs. Shared by every session, lost when the app restarts.
_runtime = {}
_TOKEN_KEYS = ("DHAN_ACCESS_TOKEN", "ACCESS_TOKEN", "DHAN_TOKEN")
_CLIENT_KEYS = ("DHAN_CLIENT_ID", "CLIENT_ID", "DHAN_CLIENT")
_renew_lock = threading.Lock()
_renew_state = {"failed_at": 0.0, "error": None, "renewed_at": None}


class DhanError(RuntimeError):
    pass


def _env(*names):
    for n in names:
        v = os.getenv(n)
        if v:
            return v.strip().strip('"').strip("'")
    raise DhanError(f"None of {names} found in config/.env")


def set_credentials(client_id=None, token=None):
    if client_id:
        _runtime["client_id"] = str(client_id).strip()
    if token:
        _runtime["token"] = str(token).strip()


def current_token():
    """The token in use, or None if there is none yet."""
    try:
        return _runtime.get("token") or _env(*_TOKEN_KEYS)
    except DhanError:
        return None


def _client_id():
    return _runtime.get("client_id") or _env(*_CLIENT_KEYS)


def _headers():
    token = current_token()
    if not token:
        raise DhanError("No Dhan access token set")
    return {
        "access-token": token,
        "client-id": _client_id(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _post(path, body, kind, attempts=3):
    """POST with rate-limit spacing. Network drops are retried, then reported as DhanError
    so one failed call cannot crash the dashboard."""
    with _locks[kind]:
        for attempt in range(1, attempts + 1):
            wait = _GAP[kind] - (time.monotonic() - _last.get(kind, 0.0))
            if wait > 0:
                time.sleep(wait)
            try:
                r = requests.post(BASE + path, json=body, headers=_headers(), timeout=20)
                break
            except requests.RequestException as e:
                if attempt == attempts:
                    raise DhanError(f"{path} -> network error after {attempts} tries: "
                                    f"{type(e).__name__}") from e
                time.sleep(2 * attempt)
            finally:
                _last[kind] = time.monotonic()
    if r.status_code != 200:
        raise DhanError(f"{path} -> HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def profile():
    """Account profile; 'tokenValidity' says when the current token expires."""
    try:
        r = requests.get(BASE + "/profile", headers={"access-token": current_token() or ""}, timeout=15)
    except requests.RequestException as e:
        raise DhanError(f"/profile -> network error: {type(e).__name__}") from e
    if r.status_code != 200:
        raise DhanError(f"/profile -> HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def save_token_locally(token):
    """Write the token into config/.env so a restart and other scripts pick it up.
    Returns False when there is no config/.env (for example on a cloud server)."""
    if not ENV_FILE.exists():
        return False
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    names = [ln.split("=", 1)[0].strip() if "=" in ln else None for ln in lines]
    key = next((k for k in _TOKEN_KEYS if k in names), _TOKEN_KEYS[0])
    if key in names:
        lines[names.index(key)] = f"{key}={token}"
    else:
        lines.append(f"{key}={token}")
    tmp = ENV_FILE.with_name(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, ENV_FILE)          # swap in one step, so the file is never half-written
    os.environ[key] = token
    return True


def _find_jwt(obj):
    """Find the token inside the renew reply, whatever field Dhan puts it in."""
    if isinstance(obj, str):
        return obj if re.fullmatch(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", obj.strip()) else None
    values = obj.values() if isinstance(obj, dict) else obj if isinstance(obj, list) else []
    for v in values:
        found = _find_jwt(v)
        if found:
            return found.strip()
    return None


def renew_token(expected):
    """Swap the active token for a fresh 24-hour one (Dhan's RenewToken). The old token stops
    working. `expected` is the token the caller saw: if another browser session has already
    renewed it, nothing more is done. Returns the token now in use."""
    with _renew_lock:
        current = current_token()
        if not current or current != expected:
            return current
        headers = {"access-token": current, "dhanClientId": _client_id()}
        try:
            r = requests.get(BASE + "/RenewToken", headers=headers, timeout=20)
            if r.status_code == 405:   # in case Dhan expects POST here
                r = requests.post(BASE + "/RenewToken", headers=headers, timeout=20)
            if r.status_code != 200:
                raise DhanError(f"/RenewToken -> HTTP {r.status_code}: {r.text[:200]}")
            try:
                new = _find_jwt(r.json())
            except ValueError:
                new = _find_jwt(r.text)
            if not new:
                raise DhanError("/RenewToken replied without a token. The old token may no "
                                "longer work: generate a new one on web.dhan.co.")
        except requests.RequestException as e:
            _renew_state.update(failed_at=time.time(), error=f"network error: {type(e).__name__}")
            raise DhanError(f"/RenewToken -> {_renew_state['error']}") from e
        except DhanError as e:
            _renew_state.update(failed_at=time.time(), error=str(e))
            raise
        set_credentials(token=new)
        save_token_locally(new)
        _renew_state.update(failed_at=0.0, error=None, renewed_at=now_ist().strftime("%d/%m %H:%M"))
        return new


def renew_status():
    """(seconds since the last failed renew or None, its error, time of the last success)."""
    failed = _renew_state["failed_at"]
    return (time.time() - failed if failed else None), _renew_state["error"], _renew_state["renewed_at"]


def now_ist():
    return datetime.now(IST)


def quotes(seg_ids):
    """seg_ids = {"NSE_FNO": [ids], "NSE_EQ": [ids]} (max 1000 ids total).
    Returns {segment: {security_id(str): quote}}."""
    body = {seg: [int(i) for i in ids] for seg, ids in seg_ids.items() if len(ids)}
    return _post("/marketfeed/quote", body, "quote").get("data", {})


def option_chain(underlying_id, underlying_seg, expiry):
    """Returns {"last_price": spot, "oc": {strike: {"ce": {...}, "pe": {...}}}}."""
    body = {"UnderlyingScrip": int(underlying_id), "UnderlyingSeg": underlying_seg, "Expiry": expiry}
    return _post("/optionchain", body, "chain").get("data", {})


def daily(security_id, segment, instrument, days=30, oi=False):
    """Daily candles up to yesterday. Returns dict of lists (close, volume, open_interest...)."""
    today = now_ist().date()
    body = {
        "securityId": str(security_id),
        "exchangeSegment": segment,
        "instrument": instrument,
        "expiryCode": 0,
        "oi": oi,
        "fromDate": str(today - timedelta(days=days)),
        "toDate": str(today),
    }
    return _post("/charts/historical", body, "hist")


def intraday(security_id, days=10, segment="NSE_FNO", instrument="OPTSTK", interval="5"):
    """Intraday candles for the last `days` calendar days, up to now.
    Returns dict of lists (open, high, low, close, volume, timestamp in epoch seconds)."""
    now = now_ist()
    body = {
        "securityId": str(security_id),
        "exchangeSegment": segment,
        "instrument": instrument,
        "interval": interval,
        "oi": False,
        "fromDate": (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"),
        "toDate": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    reply = _post("/charts/intraday", body, "intraday")
    return reply["data"] if isinstance(reply.get("data"), dict) else reply
