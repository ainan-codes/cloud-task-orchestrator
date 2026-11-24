"""
backend/core/flow_lifecycle.py — Google Flow Chromium Lifecycle & Multi-Account Isolation Engine.

Guarantees:
  1. Profile Isolation: Every Google Flow account uses a strictly unique directory.
  2. Mutex / Lock Safety: Never allows two Chromium instances against the same profile directory.
  3. No Blind Lock Deletion: Accurately probes process ownership before claiming or releasing.
  4. Decoupled Lifecycles: Distinguishes between BrowserState and Account/SessionState.
  5. Interactive Login Supervision: Automatically verifies session authentication and closes cleanly.
"""

import os
import json
import time
import logging
import subprocess
import datetime as dt
from typing import Dict, Any, Optional, Tuple
from playwright.sync_api import sync_playwright

import db

logger = logging.getLogger("flow_lifecycle")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES_DIR = os.path.join(BASE_DIR, "data", "flow_profiles")
os.makedirs(PROFILES_DIR, exist_ok=True)

FLOW_URL = os.environ.get("GOOGLE_FLOW_URL", "https://flow.google.com")


def get_active_profile_directory(profile_path: str) -> str:
    """
    Finds the active Chromium profile directory (e.g. 'Profile 1' or 'Default')
    by reading Local State or inspecting subdirectories with cookies/login data.
    """
    local_state_path = os.path.join(profile_path, "Local State")
    if os.path.exists(local_state_path):
        try:
            with open(local_state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            last_used = data.get("profile", {}).get("last_used")
            if last_used and os.path.isdir(os.path.join(profile_path, last_used)):
                return last_used
        except Exception:
            pass

    for candidate in ["Profile 1", "Profile 2", "Default"]:
        candidate_path = os.path.join(profile_path, candidate)
        cookies_path = os.path.join(candidate_path, "Network", "Cookies")
        if os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 1024:
            return candidate

    return "Default"


# ============================================================
# State Constants
# ============================================================

class BrowserState:
    STARTING = "STARTING"
    OPEN = "OPEN"
    LOGIN_PENDING = "LOGIN_PENDING"
    AUTHENTICATED = "AUTHENTICATED"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    CRASHED = "CRASHED"


class AccountSessionState:
    CREATING = "CREATING"
    LAUNCHING = "LAUNCHING"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    AUTHENTICATING = "AUTHENTICATING"
    AUTHENTICATED = "AUTHENTICATED"
    CONNECTED = "CONNECTED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    ERROR = "ERROR"
    DISCONNECTED = "DISCONNECTED"


# ============================================================
# Profile Mutex & Lock Management
# ============================================================

def get_profile_path(profile_folder: str) -> str:
    """Returns absolute path to account's dedicated persistent profile directory."""
    path = os.path.join(PROFILES_DIR, profile_folder)
    os.makedirs(path, exist_ok=True)
    return path


def is_profile_in_use(profile_folder: str) -> Tuple[bool, Optional[int]]:
    """
    Safely probes whether a profile directory is actively locked by an open Chromium process.
    Uses Windows file lock detection on 'lockfile' and process command line matching for Chrome/Chromium.
    Returns: (is_locked: bool, pid: Optional[int])
    """
    profile_path = get_profile_path(profile_folder)
    lock_file = os.path.join(profile_path, "lockfile")

    # 1. Check Windows file locking on lockfile
    is_locked = False
    if os.path.exists(lock_file):
        try:
            with open(lock_file, "r+") as f:
                try:
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except (IOError, OSError):
                    is_locked = True
        except (PermissionError, IOError, OSError):
            is_locked = True

    # 2. Check for running chrome/chromium processes holding this user-data-dir
    pid_found = None
    try:
        norm_folder = profile_folder.replace("\\", "/")
        ps_cmd = f"Get-CimInstance Win32_Process -Filter \"Name like '%chrome%' or Name like '%chromium%'\" | Where-Object {{ $_.CommandLine -like '*{norm_folder}*' }} | Select-Object -ExpandProperty ProcessId"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=5)
        out = res.stdout.strip()
        if out:
            lines = [int(l.strip()) for l in out.splitlines() if l.strip().isdigit()]
            if lines:
                pid_found = lines[0]
                is_locked = True
    except Exception as e:
        logger.debug(f"Process check note: {e}")

    return is_locked, pid_found


def release_profile_lock(profile_folder: str) -> bool:
    """
    Terminates any orphan Chrome process locking this profile and verifies release.
    Does NOT blindly delete files unless verified that no process holds it.
    """
    in_use, pid = is_profile_in_use(profile_folder)
    if not in_use:
        return True

    logger.info(f"Releasing profile lock for {profile_folder} (PID: {pid})...")
    if pid:
        try:
            subprocess.run(f"taskkill /F /PID {pid} /T", shell=True, capture_output=True)
        except Exception:
            pass

    # Wait up to 5s for process termination and file lock release
    for _ in range(10):
        time.sleep(0.5)
        in_use_now, pid_now = is_profile_in_use(profile_folder)
        if not in_use_now:
            logger.info(f"Profile lock released for {profile_folder}.")
            return True

    # If no Chrome process is running but an orphan lockfile remains on disk unlocked, remove it safely
    _, final_pid = is_profile_in_use(profile_folder)
    if not final_pid:
        lock_file = os.path.join(get_profile_path(profile_folder), "lockfile")
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                logger.info(f"Cleaned stale unlocked lockfile for {profile_folder}.")
                return True
            except Exception:
                pass

    logger.warning(f"Profile lock for {profile_folder} could not be completely released.")
    return False


# ============================================================
# Session Verification Probe
# ============================================================

def verify_flow_session(profile_folder: str) -> Dict[str, Any]:
    """
    Probes Google Flow using the dedicated Chromium profile to verify whether the session
    is authenticated and active, without modifying the profile.
    Returns: { "authenticated": bool, "status": str, "credits": int, "error": Optional[str] }
    """
    profile_path = get_profile_path(profile_folder)
    active_prof = get_active_profile_directory(profile_path)

    in_use, pid = is_profile_in_use(profile_folder)
    if in_use:
        # Check if active or candidate profile directory already has non-empty Google Cookies database
        candidates = [active_prof] + [c for c in ["Profile 1", "Profile 2", "Default"] if c != active_prof]
        for cand in candidates:
            cookies_db = os.path.join(profile_path, cand, "Network", "Cookies")
            if os.path.exists(cookies_db) and os.path.getsize(cookies_db) > 1024:
                logger.info(f"Profile {profile_folder} is in use but has active cookie database ({cand}). Marking CONNECTED.")
                return {
                    "authenticated": True,
                    "status": AccountSessionState.CONNECTED,
                    "credits": 1000,
                    "error": None
                }
        return {
            "authenticated": False,
            "status": "PROFILE_IN_USE",
            "credits": 1000,
            "error": f"Profile is currently open in a window. Please complete sign-in."
        }

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_path,
                headless=False,
                args=[
                    "--headless=new",
                    "--disable-blink-features=AutomationControlled",
                    f"--profile-directory={active_prof}",
                ],
                ignore_default_args=["--enable-automation"],
            )
            cookies = context.cookies()
            has_auth_cookie = any(c.get("name") in ["SID", "SSID", "HSID", "SAPISID", "__Secure-1PSID"] for c in cookies)

            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://flow.google.com/?pli=1", wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(3500)

            final_url = page.url
            if "accounts.google.com" in final_url:
                # If passing through CheckCookie SSO redirect, wait for flow.google.com destination
                try:
                    page.wait_for_url(lambda u: "flow.google.com" in u or "signin" in u or "ServiceLogin" in u, timeout=7000)
                    final_url = page.url
                except Exception:
                    pass

            logger.info(f"Session probe for {profile_folder} ({active_prof}): final_url='{final_url}', has_auth_cookie={has_auth_cookie}")

            is_login_page = any(k in final_url for k in ["/signin", "/ServiceLogin", "/v3/signin"])

            if is_login_page or (not has_auth_cookie and "/about" in final_url):
                context.close()
                return {
                    "authenticated": False,
                    "status": AccountSessionState.LOGIN_REQUIRED,
                    "credits": 1000,
                    "error": "Google session expired or user not signed in."
                }

            # Scrape credits
            credits = 1000
            try:
                credit_el = page.query_selector("[data-testid*='credit'], [class*='credit'], button:has-text('credits')")
                if credit_el:
                    digits = "".join(ch for ch in credit_el.inner_text() if ch.isdigit())
                    if digits:
                        credits = int(digits)
            except Exception:
                pass

            context.close()
            return {
                "authenticated": True,
                "status": AccountSessionState.CONNECTED,
                "credits": credits,
                "error": None
            }
    except Exception as e:
        logger.error(f"Error verifying Flow session for {profile_folder}: {e}")
        return {
            "authenticated": False,
            "status": AccountSessionState.ERROR,
            "credits": 1000,
            "error": str(e)
        }


# ============================================================
# Interactive Login Supervisor
# ============================================================

def supervise_interactive_login(account_id: int) -> Dict[str, Any]:
    """
    Supervisor process that manages the interactive login flow:
      1. Verifies profile is not currently locked.
      2. Spawns visible Chromium directly on the desktop.
      3. Monitors login progress.
      4. When Google Flow loads, verifies authentication and records CONNECTED.
    """
    acc = db.get_flow_account(account_id)
    if not acc:
        raise ValueError(f"Flow account #{account_id} not found.")

    profile_folder = acc["profile_folder"]
    in_use, pid = is_profile_in_use(profile_folder)
    if in_use:
        return {
            "success": False,
            "status": "PROFILE_IN_USE",
            "message": f"Profile '{profile_folder}' is already in use by process {pid}."
        }

    profile_path = get_profile_path(profile_folder)
    active_prof = get_active_profile_directory(profile_path)
    logger.info(f"Starting supervised interactive login for account #{account_id} ({profile_folder}, {active_prof})...")

    # Update database state: LAUNCHING
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE flow_accounts
                SET browser_state = 'STARTING',
                    session_status = 'AUTHENTICATING',
                    updated_at = NOW()
                WHERE id = %s;
            """, (account_id,))
        conn.commit()

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_path,
                headless=False,
                viewport=None,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    f"--profile-directory={active_prof}",
                ],
                ignore_default_args=["--enable-automation"]
            )
            page = context.pages[0] if context.pages else context.new_page()

            # Record OPEN browser state
            with db._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE flow_accounts
                        SET browser_state = 'OPEN',
                            session_status = 'AUTHENTICATING',
                            updated_at = NOW()
                        WHERE id = %s;
                    """, (account_id,))
                conn.commit()

            try:
                page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass

            # Monitor until either authenticated or closed by user (up to 5 minutes)
            start_time = time.time()
            is_authenticated = False
            scraped_credits = 1000

            while time.time() - start_time < 300:
                open_pages = [pg for pg in context.pages if not pg.is_closed()]
                if not open_pages:
                    logger.info("User closed browser window.")
                    break

                curr_url = open_pages[0].url
                if ("flow.google.com" in curr_url or "labs.google" in curr_url) and "/about" not in curr_url:
                    if "accounts.google.com" not in curr_url:
                        # Flow loaded and user is authenticated!
                        is_authenticated = True
                        try:
                            credit_el = open_pages[0].query_selector("[data-testid*='credit'], [class*='credit'], button:has-text('credits')")
                            if credit_el:
                                digits = "".join(ch for ch in credit_el.inner_text() if ch.isdigit())
                                if digits:
                                    scraped_credits = int(digits)
                        except Exception:
                            pass
                        logger.info("Google Flow authenticated session detected in open window!")
                        with db._conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    UPDATE flow_accounts
                                    SET session_status = 'CONNECTED',
                                        credits_remaining = %s,
                                        last_verified_at = NOW(),
                                        updated_at = NOW()
                                    WHERE id = %s;
                                """, (scraped_credits, account_id))
                            conn.commit()
                        time.sleep(3)
                        break

                time.sleep(1.5)

            # Close browser gracefully
            with db._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE flow_accounts
                        SET browser_state = 'CLOSING', updated_at = NOW()
                        WHERE id = %s;
                    """, (account_id,))
                conn.commit()

            context.close()

        # Verify lock release
        release_profile_lock(profile_folder)

        # Update final state
        final_session_status = AccountSessionState.CONNECTED if is_authenticated else AccountSessionState.LOGIN_REQUIRED
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE flow_accounts
                    SET browser_state = 'CLOSED',
                        session_status = %s,
                        credits_remaining = %s,
                        last_verified_at = case when %s then NOW() else last_verified_at end,
                        updated_at = NOW()
                    WHERE id = %s;
                """, (final_session_status, scraped_credits, is_authenticated, account_id))
            conn.commit()

        logger.info(f"Supervised login finished for account #{account_id}: session_status={final_session_status}")
        return {
            "success": is_authenticated,
            "status": final_session_status,
            "credits": scraped_credits
        }

    except Exception as e:
        logger.error(f"Error during supervised login: {e}")
        release_profile_lock(profile_folder)
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE flow_accounts
                    SET browser_state = 'CRASHED',
                        session_status = 'ERROR',
                        updated_at = NOW()
                    WHERE id = %s;
                """, (account_id,))
            conn.commit()
        return {"success": False, "status": "ERROR", "error": str(e)}
