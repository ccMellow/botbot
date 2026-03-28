"""
github_pusher.py
Auto-push av logg og dashboard til GitHub hver time.
Krever at git er installert og repoet er konfigurert.
"""

import os
import subprocess
import logging

logger = logging.getLogger(__name__)

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _run(cmd: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def push_to_github() -> bool:
    """
    Legger til, committer og pusher logg + dashboard til GitHub.
    Returnerer True ved suksess, False ved feil.
    """
    # Stage logg og dashboard
    code, out, err = _run(["git", "add", "logs/", "dashboard/"])
    if code != 0:
        logger.error(f"git add feilet: {err}")
        return False

    # Sjekk om det er noe å committe
    code, out, _ = _run(["git", "status", "--porcelain"])
    if not out:
        logger.info("Ingen endringer å pushe.")
        return True

    # Commit
    code, out, err = _run(["git", "commit", "-m", "auto: oppdater logg og dashboard"])
    if code != 0:
        logger.error(f"git commit feilet: {err}")
        return False

    # Push
    code, out, err = _run(["git", "push"])
    if code != 0:
        logger.error(f"git push feilet: {err}")
        return False

    logger.info("Push til GitHub vellykket.")
    return True
