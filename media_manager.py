"""
media_manager.py — Shared utility for campaign media attachments.

Responsibilities:
- Initialize the service-role Supabase storage client once at app startup.
- Download campaign media from Supabase Storage to a local temp directory.
- Verify that remote storage files still exist before a campaign runs.
- Clean up local temp files (Tier 1) and remote storage files (Tier 2).
- Sweep orphan temp files older than 24 hours on startup (Tier 3).

Import rules:
- This module MUST NOT import from twitter.py, threads.py, or instagram.py.
- twitter.py and threads.py import from this module.

Environment variables:
- SUPABASE_URL              — Supabase project URL (shared with anon client).
- SUPABASE_SERVICE_ROLE_KEY — Service role key used ONLY for storage operations.
- MEDIA_TEMP_DIR            — Optional override for the local temp directory.
                              Falls back to <tempfile.gettempdir()>/bot-media.
"""

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# =============================================================================
# MODULE-LEVEL STATE
# Initialized once by init_media_manager() at app startup.
# =============================================================================

_storage_client = None   # supabase.Client — service role, storage ops only
_temp_dir: Optional[Path] = None  # Resolved local temp root

BUCKET = 'campaign-media'


# =============================================================================
# PUBLIC: INITIALIZATION
# =============================================================================

def init_media_manager() -> None:
    """
    Initialize the media manager.

    Called once at app startup (after dotenv.load_dotenv()):
      1. Resolves and creates the local temp directory.
      2. Initializes the service-role Supabase client for storage operations.
         If SUPABASE_SERVICE_ROLE_KEY is missing, logs a warning and sets the
         client to None — the server continues running but media operations
         will be unavailable.
      3. Triggers the orphan temp-file cleanup sweep.
    """
    global _storage_client, _temp_dir

    # --- Resolve temp directory ---
    env_override = os.environ.get('MEDIA_TEMP_DIR', '').strip()
    if env_override:
        _temp_dir = Path(env_override)
    else:
        _temp_dir = Path(tempfile.gettempdir()) / 'bot-media'

    try:
        _temp_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f'[MEDIA] Temp directory: {_temp_dir}')
    except Exception as exc:
        logger.warning(f'[MEDIA] Could not create temp directory {_temp_dir}: {exc}')

    # --- Initialize service-role storage client ---
    supabase_url = os.environ.get('SUPABASE_URL', '').strip()
    service_role_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '').strip()

    if not service_role_key:
        logger.warning(
            '[MEDIA] SUPABASE_SERVICE_ROLE_KEY not set — '
            'storage operations will be unavailable. '
            'Image attachment campaigns will fail pre-flight checks.'
        )
        _storage_client = None
    else:
        try:
            from supabase import create_client
            _storage_client = create_client(supabase_url, service_role_key)
            logger.info('[MEDIA] Storage client initialized (service role).')
        except Exception as exc:
            logger.warning(f'[MEDIA] Failed to initialize storage client: {exc}')
            _storage_client = None

    # --- Orphan cleanup sweep ---
    _cleanup_orphan_temp_files()


# =============================================================================
# PUBLIC: STORAGE EXISTENCE VERIFICATION
# =============================================================================

def verify_media_exists_in_storage(
    media_attachments: Optional[List],
) -> Tuple[bool, List[str]]:
    """
    Check whether each file listed in media_attachments still exists in the bucket.

    Args:
        media_attachments: List of attachments from the campaign row.
                           Each item can be either:
                             - A string (the storage path directly)
                             - A dict with a 'storage_path' key

    Returns:
        (all_exist, missing_paths)
          all_exist     — True if every path was found in storage.
          missing_paths — List of storage_path strings that were not found.
    """
    if not media_attachments:
        return True, []

    if _storage_client is None:
        return False, ['storage_client not initialized']

    missing: List[str] = []

    for item in media_attachments:
        # Handle both string and dict formats for backwards compatibility
        if isinstance(item, str):
            storage_path = item
        else:
            storage_path = item.get('storage_path', '')
        if not storage_path:
            missing.append('<empty storage_path>')
            continue

        # Derive folder prefix (everything before the last path component).
        folder_prefix = storage_path.rsplit('/', 1)[0] if '/' in storage_path else ''
        file_name = storage_path.rsplit('/', 1)[-1]

        try:
            listed = _storage_client.storage.from_(BUCKET).list(
                path=folder_prefix,
                options={'limit': 1000}
            )
            # listed is a list of dicts with a 'name' key
            found_names = {entry.get('name', '') for entry in (listed or [])}
            if file_name not in found_names:
                missing.append(storage_path)
        except Exception as exc:
            logger.warning(
                f'[MEDIA] Could not verify storage path "{storage_path}": {exc}'
            )
            missing.append(storage_path)

    return len(missing) == 0, missing


# =============================================================================
# PUBLIC: DOWNLOAD
# =============================================================================

def download_campaign_media(
    campaign_id: str,
    media_attachments: Optional[List],
) -> List[str]:
    """
    Download all media attachments for a campaign to a local temp subdirectory.

    Args:
        campaign_id:       The text business identifier (e.g. 'campaign_abc123').
                           Used as the subdirectory name — NOT the uuid id column.
        media_attachments: List of attachments from the campaign row.
                           Each item can be either:
                             - A string (the storage path directly)
                             - A dict with 'storage_path' and optional 'file_name'

    Returns:
        Ordered list of absolute local file path strings, matching the order
        of media_attachments.

    Raises:
        RuntimeError: If any download fails or the storage client is unavailable.
    """
    if not media_attachments:
        return []

    if _storage_client is None:
        raise RuntimeError(
            f'[MEDIA] Cannot download media for campaign {campaign_id}: '
            'storage client not initialized (SUPABASE_SERVICE_ROLE_KEY missing).'
        )

    # Create campaign subdirectory inside the temp root
    campaign_dir = _temp_dir / campaign_id
    try:
        campaign_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise RuntimeError(
            f'[MEDIA] Could not create temp directory for campaign {campaign_id}: {exc}'
        ) from exc

    local_paths: List[str] = []

    for idx, item in enumerate(media_attachments):
        # Handle both string and dict formats for backwards compatibility
        if isinstance(item, str):
            storage_path = item
            file_name = os.path.basename(storage_path)
        else:
            storage_path = item.get('storage_path', '')
            file_name = item.get('file_name', '') or os.path.basename(storage_path)

        if not storage_path:
            raise RuntimeError(
                f'[MEDIA] Campaign {campaign_id}: item {idx} has no storage_path.'
            )
        if not file_name:
            raise RuntimeError(
                f'[MEDIA] Campaign {campaign_id}: item {idx} has no resolvable file_name.'
            )

        local_path = campaign_dir / file_name

        try:
            logger.info(
                f'[MEDIA] Campaign {campaign_id}: downloading '
                f'"{storage_path}" -> "{local_path}"'
            )
            file_bytes: bytes = _storage_client.storage.from_(BUCKET).download(storage_path)
            local_path.write_bytes(file_bytes)
            local_paths.append(str(local_path))
            logger.info(
                f'[MEDIA] Campaign {campaign_id}: downloaded '
                f'"{file_name}" ({len(file_bytes):,} bytes)'
            )
        except Exception as exc:
            raise RuntimeError(
                f'[MEDIA] Campaign {campaign_id}: failed to download '
                f'"{storage_path}": {exc}'
            ) from exc

    return local_paths


# =============================================================================
# PUBLIC: LOCAL CLEANUP (TIER 1 — per file)
# =============================================================================

def delete_local_media_file(path: str) -> None:
    """
    Delete a single local temp file.

    Silent if the file does not exist. Errors are logged as warnings, never
    raised — this is always called from a finally block and must not mask the
    original exception.

    Args:
        path: Absolute path string to the file to delete.
    """
    if not path:
        return
    try:
        target = Path(path)
        target.unlink(missing_ok=True)
        logger.debug(f'[MEDIA] Deleted local file: {path}')
    except Exception as exc:
        logger.warning(f'[MEDIA] Could not delete local file "{path}": {exc}')


# =============================================================================
# PUBLIC: LOCAL CLEANUP (TIER 1 — per campaign directory)
# =============================================================================

def delete_local_campaign_dir(campaign_id: str) -> None:
    """
    Delete the entire local temp subdirectory for a campaign.

    Silent if the directory does not exist. Errors are logged as warnings,
    never raised.

    Args:
        campaign_id: The text business identifier used as the directory name.
    """
    if not campaign_id or _temp_dir is None:
        return
    campaign_dir = _temp_dir / campaign_id
    if not campaign_dir.exists():
        return
    try:
        shutil.rmtree(str(campaign_dir))
        logger.info(f'[MEDIA] Deleted local campaign dir: {campaign_dir}')
    except Exception as exc:
        logger.warning(
            f'[MEDIA] Could not delete campaign dir '
            f'"{campaign_dir}" for campaign {campaign_id}: {exc}'
        )


# =============================================================================
# PUBLIC: SUPABASE STORAGE CLEANUP (TIER 2)
# =============================================================================

def delete_campaign_media_from_storage(
    media_attachments: Optional[List[dict]],
) -> None:
    """
    Delete all campaign media files from Supabase Storage.

    Called from app.py after a campaign finalizes, regardless of outcome.
    Errors are logged but never raised.

    Args:
        media_attachments: List of attachment dicts from the campaign row.
                           Each dict must contain a 'storage_path' key.
    """
    if not media_attachments:
        return

    if _storage_client is None:
        logger.warning(
            '[MEDIA] delete_campaign_media_from_storage: '
            'storage client not initialized — skipping remote cleanup.'
        )
        return

    paths_to_delete = [
        item.get('storage_path', '')
        for item in media_attachments
        if item.get('storage_path')
    ]

    if not paths_to_delete:
        return

    try:
        _storage_client.storage.from_(BUCKET).remove(paths_to_delete)
        logger.info(
            f'[MEDIA] Deleted {len(paths_to_delete)} file(s) from storage: '
            + ', '.join(paths_to_delete)
        )
    except Exception as exc:
        logger.warning(
            f'[MEDIA] Failed to delete storage files '
            f'{paths_to_delete}: {exc}'
        )


# =============================================================================
# PRIVATE: ORPHAN TEMP FILE CLEANUP (TIER 3)
# =============================================================================

def _cleanup_orphan_temp_files() -> None:
    """
    Scan the temp directory and delete files older than 24 hours.

    Called automatically by init_media_manager() at startup.
    Errors are logged but never raised.
    """
    if _temp_dir is None or not _temp_dir.exists():
        return

    cutoff = time.time() - 86400  # 24 hours in seconds
    deleted_count = 0

    try:
        for dirpath, _dirnames, filenames in os.walk(str(_temp_dir)):
            for filename in filenames:
                file_path = Path(os.path.join(dirpath, filename))
                try:
                    if file_path.stat().st_mtime < cutoff:
                        file_path.unlink(missing_ok=True)
                        deleted_count += 1
                        logger.debug(f'[MEDIA] Orphan cleanup: deleted {file_path}')
                except Exception as exc:
                    logger.warning(
                        f'[MEDIA] Orphan cleanup: could not process "{file_path}": {exc}'
                    )
    except Exception as exc:
        logger.warning(f'[MEDIA] Orphan cleanup sweep failed: {exc}')

    if deleted_count:
        logger.info(f'[MEDIA] Orphan cleanup: deleted {deleted_count} file(s) older than 24 hours.')
    else:
        logger.info('[MEDIA] Orphan cleanup: no stale files found.')
