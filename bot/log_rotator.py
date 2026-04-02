"""
log_rotator.py
Roterer trades.csv og trades.log månedlig.
Arkiverer til logs/archive/ med måned-suffix.
Beholder siste 3 måneder av arkiver.
Kalles ved oppstart fra main.py.
"""

import logging
import os
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)

LOG_DIR         = os.path.join(os.path.dirname(__file__), "..", "logs")
ARCHIVE_DIR     = os.path.join(LOG_DIR, "archive")
ROTATION_MARKER = os.path.join(LOG_DIR, ".rotation_month")


def _last_rotation_month() -> str:
    if not os.path.exists(ROTATION_MARKER):
        return ""
    with open(ROTATION_MARKER, encoding="utf-8") as f:
        return f.read().strip()


def _set_rotation_month(month_str: str) -> None:
    with open(ROTATION_MARKER, "w", encoding="utf-8") as f:
        f.write(month_str)


def _archive_logs(month_str: str) -> None:
    """Kopier gjeldende loggfiler til archive/ med måneds-suffix og nullstill dem."""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    for fname in ["trades.csv", "trades.log"]:
        src = os.path.join(LOG_DIR, fname)
        if not os.path.exists(src):
            continue
        stem, ext = fname.rsplit(".", 1)
        dst = os.path.join(ARCHIVE_DIR, f"{stem}_{month_str}.{ext}")
        shutil.copy2(src, dst)
        # Nullstill filen — behold CSV-header
        if ext == "csv":
            with open(src, "r", encoding="utf-8") as f:
                header = f.readline()
            with open(src, "w", encoding="utf-8") as f:
                f.write(header)
        else:
            open(src, "w").close()
        logger.info(f"Logg-rotasjon: arkivert {fname} → {os.path.basename(dst)}")


def _cleanup_old_archives() -> None:
    """Slett arkivmåneder utover de siste 3."""
    if not os.path.exists(ARCHIVE_DIR):
        return
    month_set: set[str] = set()
    for fn in os.listdir(ARCHIVE_DIR):
        parts = fn.rsplit("_", 1)
        if len(parts) == 2:
            month_candidate = parts[1].rsplit(".", 1)[0]
            if len(month_candidate) == 7:   # "YYYY-MM"
                month_set.add(month_candidate)

    for old_month in sorted(month_set)[:-3]:
        for fn in os.listdir(ARCHIVE_DIR):
            if old_month in fn:
                os.remove(os.path.join(ARCHIVE_DIR, fn))
                logger.info(f"Logg-rotasjon: slettet gammelt arkiv {fn}")


def rotate_logs_if_needed() -> None:
    """Sjekk om det er ny måned og arkiver logger ved behov."""
    now = datetime.now()
    current_month = now.strftime("%Y-%m")
    last_month = _last_rotation_month()

    if last_month == current_month:
        return  # Allerede rotert denne måneden

    if not last_month:
        # Første kjøring — sett startmarkør uten å arkivere
        _set_rotation_month(current_month)
        logger.info(f"Logg-rotasjon: startmarkør satt til {current_month}")
        return

    # Ny måned — arkiver og rydd opp
    logger.info(f"Logg-rotasjon: arkiverer {last_month} → logs/archive/")
    _archive_logs(last_month)
    _cleanup_old_archives()
    _set_rotation_month(current_month)
