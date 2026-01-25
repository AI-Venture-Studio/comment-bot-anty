# app.py Documentation

## Overview

`app.py` is the core backend server for a social media comment automation bot. It provides a Flask-based REST API for managing and executing automated commenting campaigns on Instagram and X/Twitter using Playwright browser automation with Dolphin Anty anti-detect browser profiles.

**Key Features:**
- Multi-platform support (Instagram, X/Twitter)
- Queue-based campaign processing
- Real-time progress tracking via REST API
- Human-like behavior simulation
- Integration with Dolphin Anty anti-detect browser
- Supabase database integration
- Background polling for automatic campaign detection

---

## Table of Contents

1. [Imports and Dependencies](#1-imports-and-dependencies)
2. [Environment Configuration](#2-environment-configuration)
3. [Flask API Setup](#3-flask-api-setup)
4. [In-Memory Event Store](#4-in-memory-event-store)
5. [Progress Emitter](#5-progress-emitter)
6. [Flask API Endpoints](#6-flask-api-endpoints)
7. [Human-Like Behavior Functions](#7-human-like-behavior-functions)
8. [Automation Logger](#8-automation-logger)
9. [Constants and Settings](#9-constants-and-settings)
10. [Navigation with Retry](#10-navigation-with-retry)
11. [Cookie Manager](#11-cookie-manager)
12. [Dolphin Anty Client](#12-dolphin-anty-client)
13. [Instagram Selectors](#13-instagram-selectors)
14. [Instagram Helper Functions](#14-instagram-helper-functions)
15. [Post Processing Functions](#15-post-processing-functions)
16. [Supabase Database Helpers](#16-supabase-database-helpers)
17. [Campaign Pre-Flight Checks](#17-campaign-pre-flight-checks)
18. [Main Automation Function](#18-main-automation-function)
19. [Background Workers](#19-background-workers)
20. [Application Entry Point](#20-application-entry-point)

---

## 1. Imports and Dependencies

```python
import asyncio
import os
import sys
import json
import random
import math
import time
import requests
import dotenv
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from queue import Queue
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from supabase import create_client, Client
from flask import Flask, jsonify, request
from flask_cors import CORS
from flasgger import Swagger, swag_from
from twitter_support import TwitterAutomation, TwitterSelectors, TweetReplyResult, stream_type_text
```

### Key Dependencies

| Package | Purpose |
|---------|---------|
| `asyncio` | Async/await support for Playwright automation |
| `playwright` | Browser automation framework |
| `flask` | REST API server |
| `flask_cors` | Cross-Origin Resource Sharing |
| `flasgger` | Swagger API documentation |
| `supabase` | Database client for Supabase |
| `requests` | HTTP client for Dolphin Anty API |
| `threading` | Background worker management |
| `dotenv` | Environment variable loading |

---

## 2. Environment Configuration

**Lines: 25-36**

```python
IS_PRODUCTION = os.environ.get('RENDER') or os.environ.get('GUNICORN_CMD_ARGS') or 'gunicorn' in os.environ.get('SERVER_SOFTWARE', '')
PORT = int(os.environ.get('PORT', 5001))
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '*').split(',')
```

### Configuration Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `IS_PRODUCTION` | Detects if running on Render or under Gunicorn | Auto-detected |
| `PORT` | Server port number | `5001` |
| `ALLOWED_ORIGINS` | CORS allowed origins | `*` (all) |

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_ANON_KEY` | Supabase anonymous key |
| `DOLPHIN_API_TOKEN` | Dolphin Anty API token |
| `DOLPHIN_LOCAL_API_URL` | Dolphin Anty local API URL (default: `http://localhost:3001`) |

---

## 3. Flask API Setup

**Lines: 38-80**

### Flask Application Initialization

```python
app = Flask(__name__)

# CORS configured based on environment
if IS_PRODUCTION and ALLOWED_ORIGINS != ['*']:
    CORS(app, origins=ALLOWED_ORIGINS)
else:
    CORS(app)
```

### Swagger Configuration

The API documentation is available at `/api/docs` with the following configuration:

- **Title:** Instagram Comment Bot API
- **Version:** 1.0.0
- **Tags:**
  - `Progress` - Progress event management
  - `Health` - API health checks
  - `Webhooks` - Webhook endpoints for external integrations

---

## 4. In-Memory Event Store

**Lines: 82-240**

### Class: `EventStore`

Thread-safe in-memory storage for automation session events using a checkpoint-based design.

#### Design Philosophy

- Only stores **FINAL outcomes** (success or failure)
- No retries, attempts, or transient states are exposed
- Each checkpoint represents a completed decision

#### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `events` | `List[Dict]` | Legacy events list |
| `checkpoints` | `List[Dict]` | Final outcome checkpoints |
| `current_progress` | `int` | Current progress percentage (0-100) |
| `status` | `str` | Current status: `idle`, `running`, `completed`, `error` |
| `lock` | `threading.Lock` | Thread synchronization lock |
| `latest_sentence` | `str` | Most recent status message |
| `abort_signal` | `bool` | Flag to signal automation abort |
| `comment_count` | `int` | Total successful comments posted |

#### Methods

##### `add_checkpoint(event_type, status, message, target, index, total)`

Adds a final checkpoint event (success or failure only).

**Parameters:**
- `event_type`: `'campaign'` | `'target'` | `'comment'`
- `status`: `'success'` | `'failure'`
- `message`: Human-readable outcome message
- `target`: Target profile username (optional)
- `index`: Current index for counting events (optional)
- `total`: Total count for this action type (optional)

##### `add_event(sentence, category, progress, significant)`

Legacy event method for internal logging only (not shown in UI carousel).

##### `get_checkpoints(limit) -> List[Dict]`

Returns checkpoint events for UI carousel (most recent first).

##### `get_events(significant_only, limit, since_timestamp) -> List[Dict]`

Returns legacy events for backward compatibility.

##### `get_current_state() -> Dict`

Returns current automation state including status, progress, latest sentence, and comment count.

##### `set_status(status)` / `set_progress(progress)`

Update automation status or progress percentage.

##### `set_abort()` / `is_aborted() -> bool`

Signal or check abort request.

##### `clear()`

Clear all events and reset for new session.

---

## 5. Progress Emitter

**Lines: 242-510**

### Class: `ProgressEmitter`

Singleton class to emit progress checkpoints during automation.

#### Design Philosophy

- Only emits **FINAL outcomes** (success or failure)
- Retries and internal steps are hidden from UI
- Each method represents a completed action

#### Checkpoint Methods

##### Campaign Checkpoints
| Method | Description |
|--------|-------------|
| `campaign_starting()` | Campaign is starting |
| `campaign_completed()` | Campaign completed successfully |
| `campaign_failed(reason)` | Campaign failed |
| `campaign_aborted()` | Campaign was aborted by user |

##### Login Checkpoints
| Method | Description |
|--------|-------------|
| `login_success(username)` | Login successful |
| `login_failed(username, reason)` | Login failed |

##### Target Profile Checkpoints
| Method | Description |
|--------|-------------|
| `target_opened(target_user)` | Target profile opened successfully |
| `target_failed(target_user, reason)` | Failed to open target profile |
| `posts_scanned(target_user, count)` | Posts scanned successfully |
| `posts_scan_failed(target_user, reason)` | Failed to scan posts |
| `target_completed(target_user, comments_posted)` | Finished processing target |
| `target_profile_failed(target_user, reason)` | Target processing failed |

##### Comment Checkpoints
| Method | Description |
|--------|-------------|
| `comment_posted(target_user, index, total)` | Comment posted successfully |
| `comment_failed(target_user, index, total, reason)` | Comment failed |

#### Legacy Methods (Internal Logging Only)

These methods log internally but do NOT show in the UI carousel:
- `navigation(message, progress)`
- `action(message, progress, significant)`
- `success(message, progress)`
- `warning(message, progress)`
- `error(message, progress)`
- `info(message, progress, significant)`
- `browser_launching()` / `browser_connected()`
- `logging_in(username)`
- `navigating_to_profile(target_user)`
- `scanning_posts(target_user)`
- `commenting_on_post(post_num, total, target_user)`
- `post_skipped(reason)`
- `taking_break(duration)`
- `cleanup()`

---

## 6. Flask API Endpoints

**Lines: 515-1070**

### Health Check

```
GET /health
```

Returns API health status.

**Response:**
```json
{
  "status": "healthy",
  "service": "instagram-comment-bot"
}
```

---

### Emit Progress Event

```
POST /api/progress/emit
```

Emit a new progress event (internal use).

**Request Body:**
```json
{
  "sentence": "Navigating to @kimkardashian's profile",
  "category": "navigation",
  "progress": 45,
  "significant": true
}
```

**Categories:** `navigation`, `action`, `success`, `warning`, `error`

---

### Get Current Progress

```
GET /api/progress/current
```

Fetch current progress snapshot for frontend polling.

**Response:**
```json
{
  "status": "running",
  "progress": 67,
  "latest_sentence": "Submitting comment on post 3 of 10",
  "total_events": 42,
  "comment_count": 5,
  "campaign_info": {
    "campaign_id": "...",
    "platform": "instagram",
    "user_accounts": ["account1"],
    "target_profiles": ["target1"],
    "custom_comment": "Great post!"
  }
}
```

---

### Get Event Feed

```
GET /api/progress/events
```

Fetch events for vertical carousel UI.

**Query Parameters:**
- `limit` (int): Maximum number of events
- `since_timestamp` (string): Only return events after this timestamp
- `significant_only` (boolean): Only return significant events (default: true)

---

### Get Checkpoints

```
GET /api/progress/checkpoints
```

Fetch final outcome checkpoints for UI carousel.

**Query Parameters:**
- `limit` (int): Maximum number of checkpoints (default: 10)

**Response:**
```json
{
  "checkpoints": [
    {
      "type": "comment",
      "status": "success",
      "message": "Comment posted (1/2) @jakepaul",
      "target": "jakepaul",
      "index": 1,
      "total": 2,
      "timestamp": "2024-12-16T10:30:00"
    }
  ],
  "total": 5,
  "comment_count": 3,
  "status": "running"
}
```

---

### Start Automation

```
POST /api/start
```

Start the automation process in a background thread.

**Response:**
```json
{
  "status": "started",
  "message": "Automation started successfully"
}
```

**Error (400):** Automation already running

---

### Abort Automation

```
POST /api/abort
```

Abort the running automation gracefully.

**Request Body (optional):**
```json
{
  "campaign_id": "campaign-uuid"
}
```

**Response:**
```json
{
  "status": "aborting",
  "message": "Abort signal sent - resources will be cleaned up"
}
```

---

### Campaign Added Webhook

```
POST /api/webhook/campaign-added
```

Webhook called by Supabase when a new campaign is inserted.

**Request Body:**
```json
{
  "type": "INSERT",
  "table": "comment_campaigns",
  "record": { ... },
  "old_record": null
}
```

Automatically starts automation if not already running.

---

## 7. Human-Like Behavior Functions

**Lines: 1075-1325**

These functions simulate human-like interactions to avoid bot detection.

### `get_random_delay(min_val, max_val) -> float`

Returns a random delay with Gaussian distribution for natural timing.

```python
mean = (min_val + max_val) / 2
std_dev = (max_val - min_val) / 4
delay = random.gauss(mean, std_dev)
```

---

### `human_like_mouse_move(page, target_x, target_y, logger)`

Moves mouse to target position with:
- Bezier-like curved paths
- Random control points
- Occasional overshoots (30% chance)
- Variable speed (faster in middle, slower at start/end)
- Pre-click pause

---

### `_curved_mouse_move(page, start_x, start_y, end_x, end_y, steps)`

Executes curved mouse movement using quadratic Bezier interpolation.

**Formula:**
```
x = (1-t)² * start_x + 2(1-t)t * control_x + t² * end_x
y = (1-t)² * start_y + 2(1-t)t * control_y + t² * end_y
```

---

### `human_like_click(page, element, logger)`

Clicks an element with:
- Human-like mouse movement to element
- Random click position within element (30-70% of dimensions)
- Natural timing

---

### `human_like_type(page, element, text, logger)`

Types text with human-like patterns:
- Variable keystroke delays (220-320ms)
- Pauses between words (0.4-1.2s)
- Longer pauses after punctuation (0.8-2.5s)
- Pre-typing hesitation (0.8-2.0s)
- Occasional typos with corrections (7% chance)

---

### `do_review_pause(logger)`

Pause after typing to simulate reviewing comment (2.5-6.0s).

---

### `do_post_to_post_delay(base_delay, logger)`

Natural delay between posts with ±20% Gaussian jitter.

**Default:** 15 seconds (configurable 8-20 seconds)

---

### `do_profile_to_profile_delay(profile_count, logger)`

Delay between profiles with occasional long breaks:
- Normal delay: 25-60 seconds
- Long break every 6 profiles: 3-5 minutes

---

## 8. Automation Logger

**Lines: 1327-1380**

### Class: `AutomationLogger`

Simple logger for tracking automation progress.

#### Attributes
- `posts_found`: Total posts discovered
- `posts_processed`: Posts that were processed
- `posts_commented`: Successful comments
- `posts_skipped`: Skipped posts (outside date range)
- `stopped_early`: Whether stopped before completing
- `errors`: List of error messages

#### Methods
- `log_info(message)` / `log_success(message)` / `log_warning(message)` / `log_error(message)`
- `log_post_found()` / `log_post_processed(commented, skipped)`
- `print_summary(stopped_early)`: Print formatted summary

---

## 9. Constants and Settings

**Lines: 1382-1445**

### Timeout and Retry Settings

| Constant | Value | Description |
|----------|-------|-------------|
| `DEFAULT_TIMEOUT` | 30000ms | Default element detection timeout |
| `NAVIGATION_TIMEOUT` | 60000ms | Navigation timeout for slow networks |
| `ELEMENT_TIMEOUT` | 10000ms | Interactive element timeout |
| `MAX_RETRIES` | 3 | Maximum retry attempts |
| `RETRY_DELAY` | 5s | Delay between retries |
| `CONSECUTIVE_OLD_POSTS_LIMIT` | 4 | Old posts before stopping |

### Human-Like Behavior Settings (LOCKED)

These constants are locked for safety and cannot be changed by users.

#### Typing Delays
| Constant | Value | Description |
|----------|-------|-------------|
| `IG_TYPING_DELAY_MIN` | 220ms | Minimum keystroke delay |
| `IG_TYPING_DELAY_MAX` | 320ms | Maximum keystroke delay |

#### Word/Punctuation Pauses
| Constant | Value | Description |
|----------|-------|-------------|
| `WORD_PAUSE_MIN/MAX` | 0.4-1.2s | Pause between words |
| `PUNCTUATION_PAUSE_MIN/MAX` | 0.8-2.5s | Pause after punctuation |

#### Pre/Post-Typing Pauses
| Constant | Value | Description |
|----------|-------|-------------|
| `PRE_TYPING_HESITATION_MIN/MAX` | 0.8-2.0s | Hesitation before typing |
| `REVIEW_PAUSE_MIN/MAX` | 2.5-6.0s | Review before posting |

#### Typo Simulation
| Constant | Value | Description |
|----------|-------|-------------|
| `TYPO_CHANCE` | 0.07 (7%) | Chance of typo per word |
| `TYPO_CORRECTION_DELAY_MIN/MAX` | 0.3-0.8s | Delay before correction |

#### Action Delays
| Constant | Value | Description |
|----------|-------|-------------|
| `POST_TO_POST_DELAY_MIN/MAX` | 8-20s | User-configurable range |
| `POST_TO_POST_DELAY_DEFAULT` | 15s | Default post delay |
| `PROFILE_TO_PROFILE_DELAY_MIN/MAX` | 25-60s | Between profiles |
| `LONG_PAUSE_MIN/MAX` | 180-300s | Occasional break (3-5 min) |
| `LONG_PAUSE_FREQUENCY` | 6 | Long pause every N profiles |

#### Mouse Movement Settings
| Constant | Value | Description |
|----------|-------|-------------|
| `MOUSE_OVERSHOOT_CHANCE` | 0.3 (30%) | Chance of overshooting |
| `MOUSE_MOVEMENT_STEPS_MIN/MAX` | 12-30 | Steps for curved movement |
| `MOUSE_STEP_DELAY_MIN/MAX` | 0.6-1.6s | Delay between steps |
| `MOUSE_PRE_CLICK_PAUSE_MIN/MAX` | 0.1-0.4s | Pause before click |

---

## 10. Navigation with Retry

**Lines: 1448-1465**

### `navigate_with_retry(page, url, max_retries, timeout) -> bool`

Navigate to a URL with retry logic for slow networks.

**Parameters:**
- `page`: Playwright page object
- `url`: URL to navigate to
- `max_retries`: Maximum retry attempts (default: 3)
- `timeout`: Timeout in milliseconds (default: 60000)

**Returns:** `True` if navigation succeeded, `False` otherwise

**Behavior:**
- Uses `domcontentloaded` wait condition
- Waits 3 seconds after navigation
- Exponential backoff on failures
- Logs retry attempts

---

## 11. Cookie Manager

**Lines: 1470-1585**

### Class: `CookieManager`

Manages browser cookies per social account (platform + username).

#### Constructor

```python
def __init__(self, cookies_dir: str = "cookies")
```

Creates cookies directory if it doesn't exist.

#### Methods

##### `_get_cookie_file(username, platform) -> Path`

Returns cookie file path: `{platform}_{username}_cookies.json`

##### `save_cookies(username, cookies, platform) -> bool`

Saves cookies with metadata:
```json
{
  "username": "user123",
  "platform": "instagram",
  "cookies": [...],
  "saved_at": "..."
}
```

##### `load_cookies(username, platform) -> list | None`

Loads and validates cookies:
- Verifies cookies belong to correct account
- Verifies cookies belong to correct platform
- Handles corrupted JSON
- Returns `None` if invalid or not found

##### `delete_cookies(username, platform) -> bool`

Deletes saved cookies for an account.

##### `has_cookies(username, platform) -> bool`

Checks if cookies exist for an account.

---

## 12. Dolphin Anty Client

**Lines: 1590-2130**

### Class: `DolphinAntyClient`

Client for managing Dolphin Anty anti-detect browser profiles.

#### Constructor

```python
def __init__(self)
```

Initializes with:
- `token` from `DOLPHIN_API_TOKEN`
- `local_api_url` from `DOLPHIN_LOCAL_API_URL` (default: `http://localhost:3001/v1.0`)
- `public_api_url`: `https://dolphin-anty-api.com`
- `dolphin_host`: Extracted hostname for port checks

#### Methods

##### `login(show_progress) -> bool`

Authenticates with Dolphin Anty using token.

##### `get_profiles(limit) -> list`

Fetches browser profiles from public API.

##### `find_profile_by_name(profile_name) -> dict | None`

Finds a browser profile by name (case-insensitive fallback).

##### `find_profile_by_id(profile_id) -> dict | None`

Finds a browser profile by ID.

##### `_wait_for_port(port, host, timeout) -> bool`

Waits until a port is open and accepting connections.

**Critical:** Uses `dolphin_host` to check remote Dolphin Anty server, not localhost.

**Timing:**
- Poll interval: 0.75s
- Logs progress every 10 seconds

##### `_verify_cdp_ready(port, host, timeout) -> bool`

Verifies Chrome DevTools Protocol endpoint is responsive.

Checks `http://{host}:{port}/json/version` for CDP readiness.

##### `is_profile_running(profile_id) -> bool`

Checks if a browser profile is currently running.

##### `ensure_profile_stopped(profile_id) -> bool`

Ensures profile is fully stopped before starting.

##### `start_profile(profile_id, headless, max_retries, startup_timeout) -> dict | None`

Starts a browser profile with deterministic startup sequence:

1. **Pre-start:** Ensure profile is stopped
2. **Call REST API** to start profile
3. **Initial grace period** (10s) for browser process startup
4. **Check if remote:** Skip port check if remote Dolphin Anty
5. **Wait for port** to be open (local mode)
6. **Verify CDP endpoint** is responsive (local mode)
7. **Return automation info**

**Timing Configuration:**
- `startup_timeout`: 120s total
- `initial_delay`: 10s before first port check
- `port_timeout`: 90s for port availability
- `cdp_timeout`: 20s for CDP response
- `retry_cooldown`: 8s between retries

**Error Handling:**
- 401/403/404: Permanent errors, fail immediately
- 500: Try stopping profile before retry
- Windows file locks: Detected and reported with fix instructions

##### `stop_profile(profile_id) -> bool`

Stops a running browser profile.

---

## 13. Instagram Selectors

**Lines: 2135-2195**

### Class: `InstagramSelectors`

CSS/XPath selectors for Instagram elements.

#### Login Page
| Selector | Description |
|----------|-------------|
| `USERNAME_INPUT` | `input[name="username"]` |
| `PASSWORD_INPUT` | `input[name="password"]` |
| `LOGIN_BUTTON` | `button[type="submit"]` |

#### Cookie Consent
| Selector | Description |
|----------|-------------|
| `COOKIE_ACCEPT_BUTTON` | "Allow all cookies", "Accept" buttons |

#### Post-Login Prompts
| Selector | Description |
|----------|-------------|
| `SAVE_LOGIN_NOT_NOW` | "Not Now" button for save login |
| `NOTIFICATIONS_NOT_NOW` | "Not Now" button for notifications |

#### Logged In Indicators
| Selector | Description |
|----------|-------------|
| `HOME_NAV` | Home navigation icon |
| `PROFILE_ICON` | Profile picture icon |
| `SEARCH_ICON` | Search icon |

#### Profile Page
| Selector | Description |
|----------|-------------|
| `POST_LINKS` | `a[href*="/p/"], a[href*="/reel/"]` |

#### Post Page
| Selector | Description |
|----------|-------------|
| `COMMENT_INPUT` | Comment textarea with "Add a comment…" |
| `COMMENT_INPUT_FORM` | Form containing comment textarea |
| `POST_COMMENT_BUTTON` | Post button (appears after typing) |
| `POST_TIMESTAMP` | `time[datetime]` |
| `CLOSE_POST_BUTTON` | Close button for post modal |
| `NEXT_POST_BUTTON` / `PREV_POST_BUTTON` | Modal navigation |
| `POST_ROW` / `FIRST_POST` | Profile grid elements |

---

## 14. Instagram Helper Functions

**Lines: 2200-2465**

### `detect_bot_challenge(page) -> bool`

Detects if Instagram is showing a bot challenge or human verification.

**Checks for phrases:**
- "Prove that you are not a bot"
- "Confirm you're human"
- "Verify you're not a bot"
- "Suspicious activity"
- "Phone number verification"

**Checks for CAPTCHA elements:**
- `[name="captcha"]`, `#captcha`, `.captcha`

---

### `get_logged_in_username(page) -> str`

Gets username of currently logged-in Instagram account.

**Methods:**
1. Check profile link in navigation
2. Check aria-label for "profile picture"
3. Look for profile switcher username

---

### `verify_instagram_login(page) -> bool`

Verifies if user is logged into Instagram.

**Checks for:**
- Home navigation, search icon, new post button
- Absence of login form
- URL doesn't contain "login" or "accounts"

---

### `instagram_logout(page)`

Logs out from Instagram by navigating to logout page.

---

### `perform_instagram_login(page, username, password)`

Performs fresh Instagram login:
1. Navigate to login page
2. Check for bot challenge
3. Accept cookies if prompted
4. Enter username and password
5. Handle "Save Login Info" prompt
6. Handle "Turn on Notifications" prompt
7. Final bot challenge check

---

### `parse_date_threshold(date_str) -> datetime`

Parses date string (YYYY-MM-DD) to datetime.

**Fallback:** 7 days ago if parsing fails.

---

### `parse_instagram_timestamp(timestamp_str) -> datetime | None`

Parses Instagram's ISO 8601 timestamp format.

---

### `instagram_login(page, username, password, target_user)`

Main login orchestration function:
1. Navigate to Instagram
2. Check for bot challenge
3. Verify if already logged in
4. Check if logged in as correct account
5. Perform login if needed
6. Navigate to target user's profile

---

## 15. Post Processing Functions

**Lines: 2470-3005**

### `get_post_links_from_profile(page, target_user, logger, max_posts) -> list[str]`

Collects post links from a user's profile page.

**Features:**
- Scrolls to load more posts
- Limits scrolling attempts (max 10)
- Deduplicates links
- Returns newest posts first

---

### `get_post_timestamp(page) -> datetime | None`

Gets timestamp of current post from `time[datetime]` element.

---

### `comment_on_post(page, comment_text, logger) -> bool`

Adds a comment to the current post.

**Strategy for finding comment input:**
1. Form textarea with aria-label "Add a comment…"
2. `COMMENT_INPUT_FORM` selector
3. JavaScript evaluation for bottom-most textarea
4. Generic `COMMENT_INPUT` selector

**Strategy for finding Post button:**
1. Form div/button with text "Post"
2. JavaScript search for "Post" text elements
3. `POST_COMMENT_BUTTON` selector

**Comment flow:**
1. Find and click comment input
2. Clear existing text
3. Type comment with human-like patterns
4. Review pause
5. Find and click Post button
6. Verify comment was posted
7. Fallback: Try Enter key

---

### `process_posts_after_date(page, target_user, date_threshold, comment_text, logger, post_delay) -> dict`

Processes posts newer than a given date.

**Features:**
- Early termination after 4 consecutive old posts
- Respects abort signal
- Tracks comments posted, skipped, errors

**Returns:**
```python
{
    "success": True,
    "posts_found": 10,
    "posts_processed": 5,
    "posts_commented": 3,
    "posts_skipped": 2,
    "stopped_early": False,
    "errors": []
}
```

---

### `process_posts_by_count(page, target_user, post_count, comment_text, logger, post_delay) -> dict`

Processes a fixed number of posts (newest first).

Same return structure as `process_posts_after_date`.

---

## 16. Supabase Database Helpers

**Lines: 3065-3295**

### `get_supabase_client() -> Client`

Initializes and returns Supabase client using environment variables.

---

### `get_next_campaigns() -> list`

Gets pending campaigns from database ordered by `queue_position`.

**Query:** `status = 'not-started'` ordered by `queue_position`

---

### `update_campaign_status(campaign_id, status)`

Updates campaign status in database.

**Valid statuses:** `not-started`, `in-progress`, `completed`, `failed`, `aborted`

---

### `get_active_campaign_from_db() -> dict | None`

Retrieves currently active (in-progress) campaign for UI state restoration.

---

### `deactivate_account(username, platform)`

Deactivates a social account when suspended or flagged.

Sets `is_active = False` in `social_accounts` table.

---

### `get_account_credentials(username, platform) -> dict | None`

Gets account credentials from `social_accounts` table.

**Returns:**
```python
{
    "username": "...",
    "password": "...",
    "browser_profile": "..."
}
```

---

### `validate_accounts_status(usernames, platform) -> dict`

Validates that all accounts are active before starting campaign.

**Returns:**
```python
{
    "valid": True/False,
    "inactive_accounts": ["user1", "user2"]
}
```

---

### `get_platform_browser_profiles(platform) -> list`

Gets browser profiles for active accounts on a specific platform.

Ensures proper separation between platforms.

---

### `get_platform_account_count(platform) -> int`

Returns count of active accounts with browser profiles for a platform.

---

### `get_env_config() -> dict`

Loads configuration from .env file.

**Returns:**
```python
{
    "mode": "count" or "date",
    "comment_text": "...",
    "target_users": [...],
    "post_count": 10,  # if mode is 'count'
    "date_threshold": datetime  # if mode is 'date'
}
```

---

### `load_target_users() -> list`

Loads target users from environment variables.

Supports `INSTAGRAM_TARGET_USER_1` through `INSTAGRAM_TARGET_USER_5`.

---

## 17. Campaign Pre-Flight Checks

**Lines: 3335-3445**

### Class: `PreFlightCheckResult`

Result container for pre-flight validation checks.

**Attributes:**
- `success`: Boolean indicating if all checks passed
- `message`: Summary message
- `errors`: List of error messages

---

### `run_campaign_preflight_checks(campaign) -> PreFlightCheckResult`

Runs all validation checks before starting a campaign.

**Checks performed:**
1. Campaign has required fields (`campaign_id`, `user_accounts`, `target_profiles`, `custom_comment`, `platform`)
2. User accounts are specified
3. Target profiles are specified
4. Account credentials exist in database
5. Browser profile is assigned to account
6. Dolphin Anty is reachable
7. Platform-specific browser profiles exist
8. Assigned browser profile exists in Dolphin Anty

**Behavior:** If ANY check fails, returns `False` and campaign is skipped (status remains `not-started`).

---

## 18. Main Automation Function

**Lines: 3450-4055**

### `run_automation_with_dolphin_anty() -> list`

Main function to run Playwright automation using Dolphin Anty.

#### Queue Processing Flow

```
1. Get pending campaigns ordered by queue_position
2. For EACH campaign:
   a. Check for abort signal
   b. Run pre-flight checks
   c. If checks FAIL → skip campaign (status: 'not-started')
   d. If checks PASS → change status to 'in-progress'
   e. Process each user account:
      i.   Validate account is still active
      ii.  Get credentials and browser profile
      iii. Start Dolphin Anty profile
      iv.  Connect Playwright via CDP
      v.   Execute platform-specific automation
      vi.  Cleanup browser resources
   f. Update campaign status (completed/failed/aborted)
3. Return results for all campaigns
```

#### Platform-Specific Automation

**Instagram:**
- Uses `instagram_login()` for authentication
- Uses `process_posts_by_count()` or `process_posts_after_date()` for commenting

**X/Twitter:**
- Uses `TwitterAutomation` class from `twitter_support`
- Passes human-like functions dictionary
- Uses same post processing modes

#### Error Handling

- **Bot challenge/suspension:** Deactivates account, marks campaign as failed
- **Abort signal:** Updates status to aborted, stops processing
- **Other errors:** Logs and continues if possible

#### Resource Cleanup

Always executes in `finally` block:
- Close Playwright browser
- Stop Playwright
- Stop Dolphin Anty profile

---

### `run_automation_in_thread()`

Wrapper to run automation in background thread.

**Flow:**
1. Clear event store
2. Set status to 'running'
3. Emit campaign starting checkpoint
4. Run async automation
5. Set final status (completed/aborted/error)

---

## 19. Background Workers

**Lines: 4060-4100**

### `campaign_polling_worker()`

Background worker that polls for new campaigns every 10 seconds.

**Purpose:** Fallback for when Supabase webhooks can't reach localhost.

**Behavior:**
- Sleeps 10 seconds between checks
- Skips if automation already running
- Auto-starts automation when pending campaigns detected

---

### `start_background_workers()`

Starts background workers for production deployment.

**Features:**
- Prevents double-start with `_workers_started` flag
- Creates daemon thread for polling worker

**Auto-start:** Workers auto-start when `IS_PRODUCTION` is True.

---

## 20. Application Entry Point

**Lines: 4105-4139**

### Entry Point Logic

```python
if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'api':
        # Run Flask API server
    else:
        # Run automation directly
```

#### API Server Mode (`python app.py api`)

1. Prints server startup information
2. Starts background polling worker (on main Werkzeug process)
3. Checks for pending campaigns and auto-starts
4. Runs Flask with:
   - `debug=True`
   - `host='0.0.0.0'`
   - `port=PORT`
   - `threaded=True`

**Endpoints printed:**
- Documentation: `http://localhost:{PORT}/api/docs`
- Current Progress: `http://localhost:{PORT}/api/progress/current`
- Event Feed: `http://localhost:{PORT}/api/progress/events`

#### Direct Automation Mode (`python app.py`)

Runs `run_automation_with_dolphin_anty()` directly via `asyncio.run()`.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Flask API Server                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ /health  │  │ /api/    │  │ /api/    │  │ /api/webhook/    │ │
│  │          │  │ progress │  │ start    │  │ campaign-added   │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                ┌───────────────┴───────────────┐
                │          EventStore           │
                │  (Thread-safe checkpoint      │
                │   storage with abort signal)  │
                └───────────────┬───────────────┘
                                │
                ┌───────────────┴───────────────┐
                │       ProgressEmitter         │
                │  (Singleton for emitting      │
                │   checkpoints and logs)       │
                └───────────────┬───────────────┘
                                │
    ┌───────────────────────────┼───────────────────────────┐
    │                           │                           │
┌───┴───┐                 ┌─────┴─────┐                ┌────┴────┐
│Supabase│                │Dolphin    │                │Playwright│
│Database│                │Anty       │                │Browser   │
│        │                │Client     │                │Automation│
└───┬───┘                 └─────┬─────┘                └────┬────┘
    │                           │                           │
    │ get_next_campaigns()      │ start_profile()           │ connect_over_cdp()
    │ update_status()           │ stop_profile()            │ page.goto()
    │ get_credentials()         │ find_profile_by_name()    │ page.click()
    │                           │                           │ page.type()
    └───────────────────────────┴───────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │   Human-Like Funcs    │
                    │  • mouse movements    │
                    │  • typing patterns    │
                    │  • random delays      │
                    │  • typo simulation    │
                    └───────────────────────┘
```

---

## Database Schema Reference

### `comment_campaigns` Table

| Column | Type | Description |
|--------|------|-------------|
| `campaign_id` | UUID | Primary key |
| `platform` | string | `instagram` or `x` |
| `user_accounts` | array | Account usernames to use |
| `target_profiles` | array | Profiles to comment on |
| `custom_comment` | string | Comment text |
| `targeting_mode` | string | `count` or `date` |
| `number_of_posts` | int | Posts per profile (count mode) |
| `target_date` | timestamp | Date threshold (date mode) |
| `post_delay` | int | Seconds between comments |
| `status` | string | `not-started`, `in-progress`, `completed`, `failed`, `aborted` |
| `queue_position` | int | Order in processing queue |
| `created_at` | timestamp | Creation time |
| `updated_at` | timestamp | Last update time |

### `social_accounts` Table

| Column | Type | Description |
|--------|------|-------------|
| `username` | string | Account username |
| `password` | string | Account password |
| `platform` | string | `instagram` or `x` |
| `browser_profile` | string | Dolphin Anty profile name/ID |
| `is_active` | boolean | Whether account is active |
| `updated_at` | timestamp | Last update time |

---

## Error Codes and Handling

| Error Type | Handling |
|------------|----------|
| Bot challenge detected | Deactivate account, fail campaign |
| Phone verification required | Deactivate account, fail campaign |
| Account suspended | Deactivate account, fail campaign |
| Browser profile not found | Skip campaign, log error |
| Dolphin Anty unreachable | Skip campaign, log error |
| Navigation timeout | Retry up to 3 times |
| Comment submission failed | Retry up to 2 times |
| User abort | Stop gracefully, mark as aborted |

---

## Configuration Examples

### Environment Variables (.env)

```bash
# Supabase
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1...

# Dolphin Anty
DOLPHIN_API_TOKEN=your-api-token
DOLPHIN_LOCAL_API_URL=http://localhost:3001

# Server
PORT=5001
ALLOWED_ORIGINS=http://localhost:3000,https://myapp.com

# Legacy .env config (optional)
COMMENT_MODE=count
COMMENT_TEXT=Great post!
POST_COUNT=10
INSTAGRAM_TARGET_USER_1=user1
INSTAGRAM_TARGET_USER_2=user2
```

---

## Usage Examples

### Start API Server

```bash
python app.py api
```

### Run Automation Directly

```bash
python app.py
```

### API Usage

```javascript
// Start automation
fetch('/api/start', { method: 'POST' });

// Poll progress
const response = await fetch('/api/progress/current');
const { status, progress, latest_sentence } = await response.json();

// Get checkpoints for UI
const checkpoints = await fetch('/api/progress/checkpoints?limit=10');

// Abort automation
fetch('/api/abort', { 
  method: 'POST',
  body: JSON.stringify({ campaign_id: 'xxx' })
});
```
