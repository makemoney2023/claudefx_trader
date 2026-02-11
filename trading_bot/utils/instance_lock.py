"""
Instance Lock - Prevent Multiple Bot Instances.

Ensures only one trading bot can run at a time to prevent
duplicate trades and conflicts.
"""

import os
import sys
import time
import atexit
from pathlib import Path
from typing import Optional

from .logging import get_logger

logger = get_logger(__name__)

LOCK_FILE = "data/bot.lock"


class InstanceLock:
    """
    File-based instance lock to prevent multiple bot instances.
    
    Uses a lock file with PID to detect if another instance is running.
    """
    
    def __init__(self, lock_file: str = LOCK_FILE):
        self.lock_file = Path(lock_file)
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self._locked = False
    
    def acquire(self) -> bool:
        """
        Attempt to acquire the lock.
        
        Returns:
            True if lock acquired, False if another instance is running
        """
        if self._locked:
            return True
        
        # Check if lock file exists
        if self.lock_file.exists():
            try:
                # Read the PID from lock file
                content = self.lock_file.read_text().strip()
                parts = content.split(':')
                old_pid = int(parts[0])
                
                # Check if that process is still running
                if self._is_process_running(old_pid):
                    logger.error(f"Another bot instance is running (PID: {old_pid})")
                    return False
                else:
                    # Stale lock file - process no longer running
                    logger.warning(f"Removing stale lock file from PID {old_pid}")
                    self.lock_file.unlink()
                    
            except (ValueError, IndexError, OSError) as e:
                logger.warning(f"Invalid lock file, removing: {e}")
                try:
                    self.lock_file.unlink()
                except:
                    pass
        
        # Create lock file with our PID
        try:
            pid = os.getpid()
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            self.lock_file.write_text(f"{pid}:{timestamp}")
            self._locked = True
            
            # Register cleanup on exit
            atexit.register(self.release)
            
            logger.info(f"Instance lock acquired (PID: {pid})")
            return True
            
        except OSError as e:
            logger.error(f"Failed to create lock file: {e}")
            return False
    
    def release(self):
        """Release the lock."""
        if self._locked and self.lock_file.exists():
            try:
                # Only remove if it's our lock
                content = self.lock_file.read_text().strip()
                parts = content.split(':')
                if int(parts[0]) == os.getpid():
                    self.lock_file.unlink()
                    logger.info("Instance lock released")
            except:
                pass
            self._locked = False
    
    def _is_process_running(self, pid: int) -> bool:
        """Check if a process with given PID is running."""
        try:
            # Windows
            if sys.platform == 'win32':
                import ctypes
                kernel32 = ctypes.windll.kernel32
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            else:
                # Unix - send signal 0 to check if process exists
                os.kill(pid, 0)
                return True
        except (OSError, ProcessLookupError):
            return False
        except Exception:
            return False
    
    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("Failed to acquire instance lock - another bot may be running")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


# Global instance
_lock: Optional[InstanceLock] = None


def get_instance_lock() -> InstanceLock:
    """Get the global instance lock."""
    global _lock
    if _lock is None:
        _lock = InstanceLock()
    return _lock


def ensure_single_instance() -> bool:
    """
    Ensure only one bot instance is running.
    
    Returns:
        True if this is the only instance, False if another is running
    """
    return get_instance_lock().acquire()


def release_instance_lock():
    """Release the instance lock (force-deletes lock file even if not owned by this process)."""
    if _lock:
        _lock.release()
    # Also force-delete the lock file if it exists (handles stale locks from killed processes)
    lock_path = Path(LOCK_FILE)
    if lock_path.exists():
        try:
            lock_path.unlink()
        except OSError:
            pass