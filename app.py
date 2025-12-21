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

dotenv.load_dotenv()

# ===========================================
# ENVIRONMENT CONFIGURATION
# ===========================================

# Detect production environment
IS_PRODUCTION = os.environ.get('RENDER') or os.environ.get('GUNICORN_CMD_ARGS') or 'gunicorn' in os.environ.get('SERVER_SOFTWARE', '')

# Get port from environment (Render provides PORT)
PORT = int(os.environ.get('PORT', 5001))

# CORS configuration - restrict in production
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '*').split(',')

# ===========================================
# FLASK API SETUP
# ===========================================

app = Flask(__name__)

# Configure CORS
if IS_PRODUCTION and ALLOWED_ORIGINS != ['*']:
    CORS(app, origins=ALLOWED_ORIGINS)
else:
    CORS(app)

# Swagger configuration
swagger_config = {
    "headers": [],
    "specs": [
        {
            "endpoint": 'apispec',
            "route": '/apispec.json',
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/api/docs"
}

swagger_template = {
    "swagger": "2.0",
    "info": {
        "title": "Instagram Comment Bot API",
        "description": "Real-time progress streaming API for Instagram automation campaigns",
        "version": "1.0.0",
        "contact": {
            "name": "API Support"
        }
    },
    "basePath": "/",
    "schemes": ["http", "https"],
    "tags": [
        {"name": "Progress", "description": "Progress event management"},
        {"name": "Health", "description": "API health checks"},
        {"name": "Webhooks", "description": "Webhook endpoints for external integrations"}
    ]
}

swagger = Swagger(app, config=swagger_config, template=swagger_template)


# ===========================================
# IN-MEMORY EVENT STORE
# ===========================================

class EventStore:
    """Thread-safe in-memory event storage for current automation session"""
    
    def __init__(self):
        self.events: List[Dict] = []
        self.current_progress = 0
        self.status = 'idle'  # idle, running, completed, error
        self.lock = threading.Lock()
        self.latest_sentence = "Waiting to start..."
        
    def add_event(self, sentence: str, category: str, progress: int = None, 
                  significant: bool = True):
        """
        Add a new event to the store
        
        Args:
            sentence: Human-readable description
            category: Event type (navigation, action, success, warning, error)
            progress: Optional progress percentage (0-100)
            significant: Whether to show in carousel (default True)
        """
        with self.lock:
            event = {
                'sentence': sentence,
                'category': category,
                'progress': progress if progress is not None else self.current_progress,
                'timestamp': datetime.now().isoformat(),
                'significant': significant
            }
            
            if progress is not None:
                self.current_progress = min(100, max(0, progress))
            
            self.events.append(event)
            self.latest_sentence = sentence
            
            # Also print to console for debugging
            print(f'[{category.upper()}] {sentence} ({self.current_progress}%)')
            
            return event
    
    def get_events(self, significant_only: bool = False, limit: int = None, 
                   since_timestamp: str = None) -> List[Dict]:
        """Get events with optional filtering"""
        with self.lock:
            events = self.events.copy()
            
            if significant_only:
                events = [e for e in events if e['significant']]
            
            if since_timestamp:
                events = [e for e in events if e['timestamp'] > since_timestamp]
            
            # Most recent first for carousel
            events.reverse()
            
            if limit:
                events = events[:limit]
            
            return events
    
    def get_current_state(self) -> Dict:
        """Get current automation state"""
        with self.lock:
            return {
                'status': self.status,
                'progress': self.current_progress,
                'latest_sentence': self.latest_sentence,
                'total_events': len(self.events)
            }
    
    def set_status(self, status: str):
        """Update automation status"""
        with self.lock:
            self.status = status
    
    def clear(self):
        """Clear all events (new session)"""
        with self.lock:
            self.events.clear()
            self.current_progress = 0
            self.status = 'idle'
            self.latest_sentence = "Waiting to start..."

# Global event store instance
event_store = EventStore()


# ===========================================
# PROGRESS EMITTER (EMBEDDED)
# ===========================================

class ProgressEmitter:
    """Singleton class to emit progress events during automation"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def emit(self, sentence: str, category: str = 'action', 
             progress: Optional[int] = None, significant: bool = True):
        """Emit a progress event"""
        event_store.add_event(sentence, category, progress, significant)
    
    # Convenience methods
    def navigation(self, message: str, progress: Optional[int] = None):
        self.emit(message, 'navigation', progress, significant=True)
    
    def action(self, message: str, progress: Optional[int] = None, significant: bool = True):
        self.emit(message, 'action', progress, significant)
    
    def success(self, message: str, progress: Optional[int] = None):
        self.emit(message, 'success', progress, significant=True)
    
    def warning(self, message: str, progress: Optional[int] = None):
        self.emit(message, 'warning', progress, significant=True)
    
    def error(self, message: str, progress: Optional[int] = None):
        self.emit(message, 'error', progress, significant=True)
    
    def info(self, message: str, progress: Optional[int] = None, significant: bool = False):
        self.emit(message, 'action', progress, significant)
    
    # Specific automation events
    def campaign_started(self, campaign_id: str, index: int, total: int):
        progress = int((index - 1) / total * 100)
        self.emit(f'Starting campaign {index} of {total}', 'action', progress)
    
    def campaign_completed(self, campaign_id: str, index: int, total: int):
        progress = int(index / total * 100)
        self.emit(f'Campaign {index} of {total} completed', 'success', progress)
    
    def browser_launching(self):
        self.emit('Launching anti-detect browser profile', 'action')
    
    def browser_connected(self):
        self.emit('Connected to browser successfully', 'success')
    
    def logging_in(self, username: str):
        self.emit(f'Logging in as @{username}', 'action')
    
    def login_success(self, username: str):
        self.emit(f'Successfully logged in as @{username}', 'success')
    
    def navigating_to_profile(self, target_user: str):
        self.emit(f"Navigating to @{target_user}'s profile", 'navigation')
    
    def scanning_posts(self, target_user: str):
        self.emit(f'Scanning posts from @{target_user}', 'action')
    
    def post_found(self, count: int):
        self.emit(f'Found {count} posts to process', 'action', significant=False)
    
    def commenting_on_post(self, post_num: int, total: int, target_user: str):
        self.emit(f'Commenting on post {post_num} of {total} from @{target_user}', 'action')
    
    def comment_submitted(self, post_num: int, total: int, comment: str):
        self.emit(f'Comment submitted on post {post_num}: "{comment}"', 'success')
    
    def post_skipped(self, reason: str):
        self.emit(f'Post skipped: {reason}', 'warning', significant=False)
    
    def profile_completed(self, target_user: str, commented: int):
        self.emit(f'Completed @{target_user} ({commented} comments posted)', 'success')
    
    def taking_break(self, duration: int):
        self.emit(f'Taking a {duration}s break to appear more human', 'action', significant=False)
    
    def cleanup(self):
        self.emit('Cleaning up browser resources', 'action', significant=False)

# Global singleton instance
progress = ProgressEmitter()


# ===========================================
# FLASK API ENDPOINTS
# ===========================================

@app.route('/health', methods=['GET'])
def health_check():
    """
    Health Check
    ---
    tags:
      - Health
    summary: Check API health status
    description: Returns the health status of the API
    responses:
      200:
        description: API is healthy
        schema:
          type: object
          properties:
            status:
              type: string
              example: healthy
            service:
              type: string
              example: instagram-comment-bot
    """
    return jsonify({
        'status': 'healthy',
        'service': 'instagram-comment-bot'
    })


@app.route('/api/progress/emit', methods=['POST'])
def emit_progress_event():
    """
    Emit Progress Event (Internal Use)
    ---
    tags:
      - Progress
    summary: Emit a new progress event
    description: Called internally by automation logic to record significant actions
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - sentence
            - category
          properties:
            sentence:
              type: string
              example: "Navigating to @kimkardashian's profile"
              description: Human-readable event description
            category:
              type: string
              enum: [navigation, action, success, warning, error]
              example: navigation
              description: Event category for UI styling
            progress:
              type: integer
              minimum: 0
              maximum: 100
              example: 45
              description: Progress percentage (0-100)
            significant:
              type: boolean
              example: true
              description: Whether event should appear in carousel
    responses:
      200:
        description: Event recorded successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
              example: true
            event:
              type: object
              properties:
                sentence:
                  type: string
                category:
                  type: string
                progress:
                  type: integer
                timestamp:
                  type: string
                  format: date-time
                significant:
                  type: boolean
      400:
        description: Invalid request body
    """
    data = request.get_json()
    
    if not data or 'sentence' not in data or 'category' not in data:
        return jsonify({'error': 'Missing required fields: sentence, category'}), 400
    
    event = progress.emit(
        sentence=data['sentence'],
        category=data['category'],
        progress=data.get('progress'),
        significant=data.get('significant', True)
    )
    
    return jsonify({
        'success': True,
        'event': event_store.events[-1] if event_store.events else None
    })


@app.route('/api/progress/current', methods=['GET'])
def get_current_progress():
    """
    Get Current Progress Snapshot
    ---
    tags:
      - Progress
    summary: Fetch current progress for polling
    description: |
      Returns current progress state optimized for high-frequency frontend polling.
      Use this to update progress bars, status badges, and "currently happening" indicators.
    responses:
      200:
        description: Current progress snapshot
        schema:
          type: object
          properties:
            status:
              type: string
              enum: [idle, running, completed, error]
              example: running
              description: Current execution state
            progress:
              type: integer
              minimum: 0
              maximum: 100
              example: 67
              description: Progress percentage
            latest_sentence:
              type: string
              example: "Submitting comment on post 3 of 10"
              description: Most recent activity description
            total_events:
              type: integer
              example: 42
              description: Total events recorded
    """
    state = event_store.get_current_state()
    return jsonify(state)


@app.route('/api/progress/events', methods=['GET'])
def get_event_feed():
    """
    Get Event Feed for Carousel
    ---
    tags:
      - Progress
    summary: Fetch events for vertical carousel UI
    description: |
      Returns ordered list of significant, user-friendly events for the carousel.
      Most recent events appear first. Supports filtering and pagination.
    parameters:
      - in: query
        name: limit
        type: integer
        required: false
        description: Maximum number of events to return
        example: 10
      - in: query
        name: since_timestamp
        type: string
        format: date-time
        required: false
        description: Only return events after this timestamp (ISO 8601)
        example: "2024-12-16T10:30:00"
      - in: query
        name: significant_only
        type: boolean
        required: false
        default: true
        description: Only return significant events (carousel-worthy)
        example: true
    responses:
      200:
        description: Event feed
        schema:
          type: object
          properties:
            events:
              type: array
              items:
                type: object
                properties:
                  sentence:
                    type: string
                    example: "Navigating to @hypebeastkicks's profile"
                  category:
                    type: string
                    example: navigation
                  progress:
                    type: integer
                    example: 45
                  timestamp:
                    type: string
                    format: date-time
                  significant:
                    type: boolean
            total:
              type: integer
              example: 25
              description: Total number of events returned
            status:
              type: string
              example: running
    """
    limit = request.args.get('limit', type=int)
    since_timestamp = request.args.get('since_timestamp')
    significant_only = request.args.get('significant_only', 'true').lower() == 'true'
    
    events = event_store.get_events(
        significant_only=significant_only,
        limit=limit,
        since_timestamp=since_timestamp
    )
    
    return jsonify({
        'events': events,
        'total': len(events),
        'status': event_store.status
    })


def run_automation_in_thread():
    """Run automation in background thread"""
    try:
        event_store.clear()
        event_store.set_status('running')
        progress.emit('Starting automation system', 'action', 0)
        
        # Run the async automation
        asyncio.run(run_automation_with_dolphin_anty())
        
        event_store.set_status('completed')
        progress.emit('All campaigns completed successfully', 'success', 100)
        
    except Exception as e:
        event_store.set_status('error')
        progress.emit(f'Automation failed: {str(e)}', 'error')


@app.route('/api/start', methods=['POST'])
def start_automation():
    """
    Start Automation
    ---
    tags:
      - Progress
    summary: Start the automation process
    description: Initiates the Instagram comment bot automation in a background thread
    responses:
      200:
        description: Automation started successfully
        schema:
          type: object
          properties:
            status:
              type: string
              example: started
            message:
              type: string
              example: Automation started successfully
      400:
        description: Automation already running
    """
    if event_store.status == 'running':
        return jsonify({'error': 'Automation already running'}), 400
    
    # Start automation in background thread
    thread = threading.Thread(target=run_automation_in_thread, daemon=True)
    thread.start()
    
    return jsonify({
        'status': 'started',
        'message': 'Automation started successfully'
    })


@app.route('/api/webhook/campaign-added', methods=['POST'])
def campaign_added_webhook():
    """
    Campaign Added Webhook
    ---
    tags:
      - Webhooks
    summary: Receive notification when a new campaign is added
    description: |
      Called by Supabase when a new campaign is inserted into the comment_campaigns table.
      Automatically starts the automation if not already running.
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            type:
              type: string
              example: INSERT
            table:
              type: string
              example: comment_campaigns
            record:
              type: object
              description: The inserted campaign record
            old_record:
              type: object
              nullable: true
    responses:
      200:
        description: Webhook received and processed
        schema:
          type: object
          properties:
            status:
              type: string
              example: received
            automation_started:
              type: boolean
              example: true
      400:
        description: Invalid webhook payload
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No data provided'
            }), 400
        
        # Log webhook received
        webhook_type = data.get('type')
        table = data.get('table')
        record = data.get('record', {})
        
        print(f'\n[WEBHOOK] Received: {webhook_type} on {table}')
        print(f'[WEBHOOK] Campaign ID: {record.get("campaign_id")}')
        print(f'[WEBHOOK] Status: {record.get("status")}')
        
        # Only process INSERT events for not-started campaigns
        if webhook_type == 'INSERT' and record.get('status') == 'not-started':
            # Check if automation is already running
            if event_store.status == 'running':
                print('[WEBHOOK] Automation already running, new campaign will be picked up in queue')
                return jsonify({
                    'status': 'received',
                    'automation_started': False,
                    'message': 'Campaign queued, automation already running'
                })
            
            # Start automation for new campaign
            print('[WEBHOOK] Starting automation for new campaign...')
            thread = threading.Thread(target=run_automation_in_thread, daemon=True)
            thread.start()
            
            return jsonify({
                'status': 'received',
                'automation_started': True,
                'message': 'Automation started for new campaign'
            })
        
        # Acknowledge other webhook types
        return jsonify({
            'status': 'received',
            'automation_started': False,
            'message': f'Webhook processed: {webhook_type}'
        })
        
    except Exception as e:
        print(f'[WEBHOOK ERROR] {str(e)}')
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# ===========================================
# HUMAN-LIKE BEHAVIOR HELPER FUNCTIONS
# ===========================================

def get_random_delay(min_val: float, max_val: float) -> float:
    """Get a random delay with slight gaussian distribution for more natural timing."""
    # Use gaussian distribution centered between min and max for more natural variation
    mean = (min_val + max_val) / 2
    std_dev = (max_val - min_val) / 4
    delay = random.gauss(mean, std_dev)
    # Clamp to min/max range
    return max(min_val, min(max_val, delay))


async def human_like_mouse_move(page: Page, target_x: int, target_y: int, logger=None):
    """
    Move mouse to target position with human-like curved path and occasional overshoots.
    Uses bezier-like curves with random control points for natural movement.
    """
    try:
        # Get current mouse position (approximate from viewport center if unknown)
        viewport = page.viewport_size
        if viewport:
            # Start from a random position near center
            start_x = viewport['width'] // 2 + random.randint(-100, 100)
            start_y = viewport['height'] // 2 + random.randint(-100, 100)
        else:
            start_x, start_y = 400, 300
        
        # Decide if we should overshoot
        should_overshoot = random.random() < MOUSE_OVERSHOOT_CHANCE
        
        if should_overshoot:
            # Calculate overshoot position (go past target then back)
            overshoot_distance = random.randint(10, 40)
            direction_x = 1 if target_x > start_x else -1
            direction_y = 1 if target_y > start_y else -1
            overshoot_x = target_x + (direction_x * overshoot_distance)
            overshoot_y = target_y + (direction_y * overshoot_distance)
            
            # Move to overshoot position first
            await _curved_mouse_move(page, start_x, start_y, overshoot_x, overshoot_y)
            
            # Small pause as if realizing overshoot
            await asyncio.sleep(random.uniform(0.05, 0.15))
            
            # Then correct to actual target
            await _curved_mouse_move(page, overshoot_x, overshoot_y, target_x, target_y, steps=5)
        else:
            # Direct curved movement to target
            await _curved_mouse_move(page, start_x, start_y, target_x, target_y)
        
        # Pre-click pause (human hesitation before clicking)
        await asyncio.sleep(get_random_delay(MOUSE_PRE_CLICK_PAUSE_MIN, MOUSE_PRE_CLICK_PAUSE_MAX))
        
    except Exception as e:
        if logger:
            progress.warning('Using direct hover instead of smooth movement')


async def _curved_mouse_move(page: Page, start_x: int, start_y: int, end_x: int, end_y: int, steps: int = None):
    """
    Execute curved mouse movement using bezier-like interpolation.
    """
    if steps is None:
        steps = random.randint(MOUSE_MOVEMENT_STEPS_MIN, MOUSE_MOVEMENT_STEPS_MAX)
    
    # Create random control point for curve (bezier-like)
    # Control point is offset from the midpoint
    mid_x = (start_x + end_x) / 2
    mid_y = (start_y + end_y) / 2
    
    # Add random offset to create curve
    curve_offset_x = random.randint(-50, 50)
    curve_offset_y = random.randint(-30, 30)
    control_x = mid_x + curve_offset_x
    control_y = mid_y + curve_offset_y
    
    for i in range(steps + 1):
        t = i / steps
        
        # Quadratic bezier curve formula
        x = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * control_x + t ** 2 * end_x
        y = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * control_y + t ** 2 * end_y
        
        # Add tiny random jitter for more natural movement
        jitter_x = random.uniform(-2, 2)
        jitter_y = random.uniform(-2, 2)
        
        await page.mouse.move(x + jitter_x, y + jitter_y)
        
        # Variable delay between steps (slower at start and end, faster in middle)
        # This mimics human acceleration/deceleration
        speed_factor = 1 - abs(t - 0.5) * 0.6  # Faster in middle
        delay = get_random_delay(MOUSE_STEP_DELAY_MIN, MOUSE_STEP_DELAY_MAX) * speed_factor
        await asyncio.sleep(delay)


async def human_like_click(page: Page, element, logger=None):
    """
    Click an element with human-like mouse movement and natural timing.
    """
    try:
        # Get element bounding box
        box = await element.bounding_box()
        if box:
            # Click at a random position within the element (not always center)
            target_x = box['x'] + box['width'] * random.uniform(0.3, 0.7)
            target_y = box['y'] + box['height'] * random.uniform(0.3, 0.7)
            
            # Move mouse naturally
            await human_like_mouse_move(page, int(target_x), int(target_y), logger)
            
            # Click
            await page.mouse.click(target_x, target_y)
        else:
            # Fallback to regular click
            await element.click()
    except Exception as e:
        if logger:
            progress.warning('Using direct click instead of human-like interaction')
        await element.click()


async def human_like_type(page: Page, element, text: str, logger=None):
    """
    Type text with human-like patterns including:
    - Variable keystroke delays
    - Pauses between words
    - Longer pauses after punctuation
    - Pre-typing hesitation
    - Occasional typos and corrections
    """
    # Pre-typing hesitation (thinking before typing)
    hesitation = get_random_delay(PRE_TYPING_HESITATION_MIN, PRE_TYPING_HESITATION_MAX)
    await asyncio.sleep(hesitation)
    
    # Split text into words for word-level pauses
    words = text.split(' ')
    punctuation_chars = '.!?,;:'
    
    for word_idx, word in enumerate(words):
        # Decide if we should simulate a typo for this word
        should_typo = random.random() < TYPO_CHANCE and len(word) > 3
        
        if should_typo:
            # Type the word with a typo
            typo_position = random.randint(1, len(word) - 1)
            typo_char = random.choice('abcdefghijklmnopqrstuvwxyz')
            
            # Type up to typo position
            for char in word[:typo_position]:
                await _type_single_char(element, char)
            
            # Type the wrong character
            await _type_single_char(element, typo_char)
            
            # Pause as if noticing the mistake
            await asyncio.sleep(get_random_delay(TYPO_CORRECTION_DELAY_MIN, TYPO_CORRECTION_DELAY_MAX))
            
            # Backspace to fix
            await element.press('Backspace')
            await asyncio.sleep(random.uniform(0.05, 0.15))
            
            # Continue typing correctly
            for char in word[typo_position:]:
                await _type_single_char(element, char)
        else:
            # Type the word normally
            for char in word:
                await _type_single_char(element, char)
        
        # Check if word ends with punctuation
        ends_with_punctuation = word and word[-1] in punctuation_chars
        
        # Add space after word (except for last word)
        if word_idx < len(words) - 1:
            await _type_single_char(element, ' ')
            
            # Pause between words
            if ends_with_punctuation:
                # Longer pause after punctuation (end of sentence/clause)
                pause = get_random_delay(PUNCTUATION_PAUSE_MIN, PUNCTUATION_PAUSE_MAX)
            else:
                # Normal word pause
                pause = get_random_delay(WORD_PAUSE_MIN, WORD_PAUSE_MAX)
            
            await asyncio.sleep(pause)


async def _type_single_char(element, char: str):
    """Type a single character with natural delay."""
    delay_ms = random.randint(IG_TYPING_DELAY_MIN, IG_TYPING_DELAY_MAX)
    await element.type(char, delay=delay_ms)


async def do_review_pause(logger=None):
    """
    Pause after typing to simulate reviewing the comment before posting.
    """
    pause = get_random_delay(REVIEW_PAUSE_MIN, REVIEW_PAUSE_MAX)
    progress.info(f'Reviewing comment before posting', significant=False)
    await asyncio.sleep(pause)


async def do_post_to_post_delay(logger=None):
    """
    Natural delay between processing posts.
    """
    delay = get_random_delay(POST_TO_POST_DELAY_MIN, POST_TO_POST_DELAY_MAX)
    progress.info('Moving to next post', significant=False)
    await asyncio.sleep(delay)


async def do_profile_to_profile_delay(profile_count: int, logger=None):
    """
    Natural delay between profiles with occasional long breaks.
    """
    # Check if we should take a long break
    if profile_count > 0 and profile_count % LONG_PAUSE_FREQUENCY == 0:
        long_pause = get_random_delay(LONG_PAUSE_MIN, LONG_PAUSE_MAX)
        progress.taking_break(int(long_pause))
        await asyncio.sleep(long_pause)
    else:
        delay = get_random_delay(PROFILE_TO_PROFILE_DELAY_MIN, PROFILE_TO_PROFILE_DELAY_MAX)
        progress.info('Moving to next profile', significant=False)
        await asyncio.sleep(delay)


# Logging helper
class AutomationLogger:
    """Simple logger for tracking automation progress"""
    
    def __init__(self):
        self.posts_found = 0
        self.posts_processed = 0
        self.posts_commented = 0
        self.posts_skipped = 0
        self.stopped_early = False
        self.errors = []
    
    def log_info(self, message: str):
        print(f'[INFO]  {message}')
    
    def log_success(self, message: str):
        print(f'[OK] {message}')
    
    def log_warning(self, message: str):
        print(f'[WARN]  {message}')
    
    def log_error(self, message: str):
        print(f'[ERR] {message}')
        self.errors.append(message)
    
    def log_post_found(self):
        self.posts_found += 1
    
    def log_post_processed(self, commented: bool = False, skipped: bool = False):
        self.posts_processed += 1
        if commented:
            self.posts_commented += 1
        if skipped:
            self.posts_skipped += 1
    
    def print_summary(self, stopped_early: bool = False):
        print('\n' + '='*50)
        print('[SUMMARY] AUTOMATION SUMMARY')
        print('='*50)
        print(f'   Posts found: {self.posts_found}')
        print(f'   Posts processed: {self.posts_processed}')
        print(f'   Posts commented: {self.posts_commented}')
        print(f'   Posts skipped (outside date range): {self.posts_skipped}')
        if stopped_early:
            print(f'   [STOP] Stopped early (consecutive old posts reached)')
        if self.errors:
            print(f'   Errors encountered: {len(self.errors)}')
            for error in self.errors:
                print(f'      - {error}')
        print('='*50)


# Constants for timeouts and retries
DEFAULT_TIMEOUT = 30000  # 30 seconds - more reasonable for element detection
NAVIGATION_TIMEOUT = 60000  # 1 minute for slow networks
ELEMENT_TIMEOUT = 10000  # 10 seconds for interactive elements
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds
# Number of consecutive old posts before stopping (for efficiency)
CONSECUTIVE_OLD_POSTS_LIMIT = 4

# ===========================================
# HUMAN-LIKE BEHAVIOR SETTINGS
# ===========================================

# Typing delays (milliseconds) - realistic human typing speed
IG_TYPING_DELAY_MIN = 140  # minimum delay between keystrokes
IG_TYPING_DELAY_MAX = 300  # maximum delay between keystrokes

# Word and punctuation pauses (seconds)
WORD_PAUSE_MIN = 0.25  # pause between words
WORD_PAUSE_MAX = 0.8
PUNCTUATION_PAUSE_MIN = 0.5  # pause after punctuation/sentences
PUNCTUATION_PAUSE_MAX = 1.5

# Pre-typing and post-typing pauses (seconds)
PRE_TYPING_HESITATION_MIN = 0.4  # hesitation before starting to type
PRE_TYPING_HESITATION_MAX = 0.9
REVIEW_PAUSE_MIN = 1.0  # pause after typing to "review" before posting
REVIEW_PAUSE_MAX = 3.0

# Typo simulation
TYPO_CHANCE = 0.07  # 7% chance of making a typo per word
TYPO_CORRECTION_DELAY_MIN = 0.3  # delay before noticing and correcting typo
TYPO_CORRECTION_DELAY_MAX = 0.8

# Action delays (seconds)
POST_TO_POST_DELAY_MIN = 3  # delay between processing posts
POST_TO_POST_DELAY_MAX = 7
PROFILE_TO_PROFILE_DELAY_MIN = 10  # delay between profiles
PROFILE_TO_PROFILE_DELAY_MAX = 25
LONG_PAUSE_MIN = 30  # occasional long break
LONG_PAUSE_MAX = 60
LONG_PAUSE_FREQUENCY = 7  # long pause every N profiles (5-10 range)

# Mouse movement settings
MOUSE_OVERSHOOT_CHANCE = 0.3  # 30% chance of overshooting target
MOUSE_MOVEMENT_STEPS_MIN = 8  # minimum steps for curved movement
MOUSE_MOVEMENT_STEPS_MAX = 20  # maximum steps for curved movement
MOUSE_STEP_DELAY_MIN = 0.01  # delay between movement steps (seconds)
MOUSE_STEP_DELAY_MAX = 0.03
MOUSE_PRE_CLICK_PAUSE_MIN = 0.1  # pause before clicking
MOUSE_PRE_CLICK_PAUSE_MAX = 0.4

# Comment retry settings
IG_MAX_COMMENT_RETRIES = 2


async def navigate_with_retry(page: Page, url: str, max_retries: int = MAX_RETRIES, timeout: int = NAVIGATION_TIMEOUT) -> bool:
    """
    Navigate to a URL with retry logic for slow networks.
    
    Args:
        page: Playwright page object
        url: URL to navigate to
        max_retries: Maximum number of retry attempts
        timeout: Timeout in milliseconds
        
    Returns:
        True if navigation succeeded, False otherwise
    """
    for attempt in range(max_retries):
        try:
            if attempt == 0:
                progress.navigation(url)
            else:
                progress.info(f'Retrying navigation (attempt {attempt + 1})', significant=False)
            await page.goto(url, wait_until='domcontentloaded', timeout=timeout)
            await asyncio.sleep(3)
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                progress.warning(f'Navigation timed out, retrying in {RETRY_DELAY}s')
                await asyncio.sleep(RETRY_DELAY)
            else:
                progress.error(f'Navigation failed after {max_retries} attempts')
                raise e
    return False


class CookieManager:
    """Manages browser cookies per Instagram account"""
    
    def __init__(self, cookies_dir: str = "cookies"):
        self.cookies_dir = Path(cookies_dir)
        self.cookies_dir.mkdir(exist_ok=True)
    
    def _get_cookie_file(self, username: str) -> Path:
        """Get the cookie file path for a specific username"""
        # Sanitize username for filename
        safe_username = "".join(c for c in username if c.isalnum() or c in "_-")
        return self.cookies_dir / f"{safe_username}_cookies.json"
    
    def save_cookies(self, username: str, cookies: list) -> bool:
        """
        Save cookies for a specific Instagram account.
        
        Args:
            username: Instagram username
            cookies: List of cookie dictionaries from browser
        """
        try:
            cookie_file = self._get_cookie_file(username)
            cookie_data = {
                "username": username,
                "cookies": cookies,
                "saved_at": str(asyncio.get_event_loop().time()) if asyncio.get_event_loop().is_running() else "0"
            }
            with open(cookie_file, 'w') as f:
                json.dump(cookie_data, f, indent=2)
            # Cookie save is silent - no progress event needed for internal operation
            return True
        except Exception as e:
            progress.error(f'Failed to save session cookies')
            return False
    
    def load_cookies(self, username: str) -> list | None:
        """
        Load cookies for a specific Instagram account.
        Verifies the cookies belong to the requested username.
        
        Args:
            username: Instagram username to load cookies for
            
        Returns:
            List of cookies if found and valid, None otherwise
        """
        try:
            cookie_file = self._get_cookie_file(username)
            if not cookie_file.exists():
                # No cookies - silent, this is expected for first login
                return None
            
            with open(cookie_file, 'r') as f:
                cookie_data = json.load(f)
            
            # Verify the cookies belong to the correct account
            stored_username = cookie_data.get("username", "")
            if stored_username.lower() != username.lower():
                progress.warning(f'Session cookies belong to different account, clearing')
                self.delete_cookies(username)
                return None
            
            cookies = cookie_data.get("cookies", [])
            if not cookies:
                # Empty cookies - silent cleanup
                return None
            
            # Cookie load successful - silent
            return cookies
            
        except json.JSONDecodeError:
            progress.warning('Session cookies corrupted, clearing')
            self.delete_cookies(username)
            return None
        except Exception as e:
            # Silent failure on cookie load
            return None
    
    def delete_cookies(self, username: str) -> bool:
        """Delete saved cookies for a specific account"""
        try:
            cookie_file = self._get_cookie_file(username)
            if cookie_file.exists():
                cookie_file.unlink()
                # Silent cookie deletion
            return True
        except Exception as e:
            # Silent failure
            return False
    
    def has_cookies(self, username: str) -> bool:
        """Check if cookies exist for a username"""
        cookie_file = self._get_cookie_file(username)
        return cookie_file.exists()


class DolphinAntyClient:
    """Client for managing Dolphin Anty browser profiles"""
    
    def __init__(self):
        self.token = os.getenv('DOLPHIN_API_TOKEN')
        # Dolphin Anty local API - ensure it ends with /v1.0
        local_url = os.getenv('DOLPHIN_LOCAL_API_URL', 'http://localhost:3001')
        self.local_api_url = local_url.rstrip('/') + '/v1.0' if not local_url.endswith('/v1.0') else local_url
        self.public_api_url = 'https://dolphin-anty-api.com'
        self.headers = {'Content-Type': 'application/json'}
        self.api_headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }
        # Extract the host from local_api_url for port checking
        # This is CRITICAL: port checks must target the Dolphin Anty server, not localhost
        from urllib.parse import urlparse
        parsed = urlparse(local_url)
        self.dolphin_host = parsed.hostname or 'localhost'
        print(f'[CONFIG] Dolphin Anty host for port checks: {self.dolphin_host}')
    
    def login(self, show_progress: bool = True) -> bool:
        """Login to Dolphin Anty with token
        
        Args:
            show_progress: Whether to show progress messages (default: True)
        """
        if show_progress:
            print(f'🔗 Connecting to Dolphin Anty at: {self.local_api_url}')
        else:
            progress.info('Connecting to anti-detect browser', significant=False)
            
        try:
            response = requests.post(
                f'{self.local_api_url}/auth/login-with-token',
                json={'token': self.token},
                headers=self.headers,
                timeout=10
            )
            if response.status_code == 200:
                if show_progress:
                    print('[OK] Dolphin Anty login successful\n')
                return True
            if show_progress:
                print('[ERROR] Anti-detect browser authentication failed\n')
            else:
                progress.error(f'Anti-detect browser authentication failed')
            return False
        except requests.exceptions.ConnectionError:
            if show_progress:
                print('[ERROR] Cannot connect to Dolphin Anty - make sure it is running\n')
            else:
                progress.error('Cannot connect to anti-detect browser - make sure it is running')
            return False
        except Exception as e:
            if show_progress:
                print(f'[ERROR] Anti-detect browser connection failed: {e}\n')
            else:
                progress.error(f'Anti-detect browser connection failed')
            return False
    
    def get_profiles(self, limit: int = None) -> list:
        """Get list of browser profiles
        
        Args:
            limit: Maximum number of profiles to return (default: all)
        """
        url = f'{self.public_api_url}/browser_profiles'
        if limit:
            url += f'?limit={limit}'
        response = requests.get(
            url,
            headers=self.api_headers
        )
        if response.status_code == 200:
            return response.json().get('data', [])
        return []
    
    def find_profile_by_name(self, profile_name: str) -> dict | None:
        """Find a browser profile by its name
        
        Args:
            profile_name: The name of the browser profile to find
            
        Returns:
            Profile dict if found, None otherwise
        """
        try:
            # Fetch all profiles (Dolphin Anty API doesn't support name filtering)
            response = requests.get(
                f'{self.public_api_url}/browser_profiles',
                headers=self.api_headers
            )
            if response.status_code == 200:
                profiles = response.json().get('data', [])
                # Search for exact match (case-sensitive)
                for profile in profiles:
                    if profile.get('name') == profile_name:
                        return profile
                # If no exact match, try case-insensitive
                profile_name_lower = profile_name.lower()
                for profile in profiles:
                    if profile.get('name', '').lower() == profile_name_lower:
                        return profile
            return None
        except Exception as e:
            print(f'[ERR] Error finding profile by name: {e}')
            return None
    
    def find_profile_by_id(self, profile_id: str | int) -> dict | None:
        """Find a browser profile by its ID
        
        Args:
            profile_id: The ID of the browser profile to find (can be string or int)
            
        Returns:
            Profile dict if found, None otherwise
        """
        try:
            # Convert to int for comparison
            search_id = int(profile_id) if isinstance(profile_id, str) else profile_id
            
            # Fetch all profiles
            response = requests.get(
                f'{self.public_api_url}/browser_profiles',
                headers=self.api_headers
            )
            if response.status_code == 200:
                profiles = response.json().get('data', [])
                # Search for matching ID
                for profile in profiles:
                    if profile.get('id') == search_id:
                        return profile
            return None
        except Exception as e:
            print(f'[ERR] Error finding profile by ID: {e}')
            return None
    
    def _wait_for_port(self, port: int, host: str = None, timeout: int = 30) -> bool:
        """
        Wait until a port is open and accepting connections.
        
        This is a minimal readiness check to ensure the browser process
        has actually bound to the expected port before we attempt CDP connection.
        
        IMPORTANT: Uses self.dolphin_host by default to check the remote Dolphin Anty
        server, not localhost (which would be the Render server).
        
        Args:
            port: Port number to check
            host: Hostname (default: self.dolphin_host - the Dolphin Anty server)
            timeout: Maximum seconds to wait
            
        Returns:
            True if port is open, False if timeout reached
        """
        import socket
        
        # Use Dolphin Anty host by default, not localhost
        if host is None:
            host = self.dolphin_host
        
        print(f'[INFO] Checking port {port} on host {host}')
        
        start_time = time.time()
        # Poll interval optimized for AWS Lightsail 2GB instances
        # 0.75s balances responsiveness with avoiding excessive CPU usage
        poll_interval = 0.75
        last_log_time = 0
        
        while time.time() - start_time < timeout:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)  # Short timeout for each probe
                result = sock.connect_ex((host, port))
                sock.close()
                
                if result == 0:
                    # Port is open
                    elapsed = int(time.time() - start_time)
                    print(f'[OK] Port {port} is now open and ready (took {elapsed}s)')
                    return True
                    
            except socket.error:
                pass  # Port not ready yet
            
            # Log progress every 10 seconds for visibility
            elapsed = int(time.time() - start_time)
            if elapsed > 0 and elapsed % 10 == 0 and elapsed != last_log_time:
                print(f'[WAIT] Still waiting for port {port}... ({elapsed}s/{timeout}s)')
                last_log_time = elapsed
            
            time.sleep(poll_interval)
        
        return False
    
    def _verify_cdp_ready(self, port: int, host: str = None, timeout: int = 10) -> bool:
        """
        Verify that the Chrome DevTools Protocol endpoint is responsive.
        
        Sends a simple HTTP request to the CDP JSON endpoint to confirm
        the browser is ready to accept automation connections.
        
        IMPORTANT: Uses self.dolphin_host by default to check the remote Dolphin Anty
        server, not localhost (which would be the Render server).
        
        Args:
            port: CDP port number
            host: Hostname (default: self.dolphin_host - the Dolphin Anty server)
            timeout: Request timeout in seconds
            
        Returns:
            True if CDP endpoint responds, False otherwise
        """
        # Use Dolphin Anty host by default, not localhost
        if host is None:
            host = self.dolphin_host
            
        try:
            # CDP exposes a JSON endpoint that lists available debugging targets
            cdp_url = f'http://{host}:{port}/json/version'
            print(f'[INFO] Checking CDP endpoint at {cdp_url}')
            response = requests.get(cdp_url, timeout=timeout)
            
            if response.status_code == 200:
                # Optionally verify response contains expected CDP info
                data = response.json()
                if 'webSocketDebuggerUrl' in data or 'Browser' in data:
                    return True
                # Even without expected fields, a 200 response means CDP is up
                return True
                
        except requests.exceptions.RequestException:
            pass  # CDP not ready or unreachable
        except Exception:
            pass  # Unexpected error, treat as not ready
        
        return False
    
    def is_profile_running(self, profile_id: int) -> bool:
        """
        Check if a browser profile is currently running.
        
        Args:
            profile_id: The ID of the browser profile to check
            
        Returns:
            True if profile is running, False otherwise
        """
        try:
            # Dolphin Anty provides an endpoint to check active profiles
            response = requests.get(
                f'{self.local_api_url}/browser_profiles/{profile_id}/active',
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                # Check if the profile has automation info (means it's running)
                return data.get('success', False) and data.get('automation') is not None
            
            return False
            
        except Exception as e:
            print(f'[WARN] Could not check if profile {profile_id} is running: {e}')
            return False
    
    def ensure_profile_stopped(self, profile_id: int) -> bool:
        """
        Ensure a profile is fully stopped before starting it.
        Checks if running and stops it, then waits for cleanup.
        
        Args:
            profile_id: The ID of the browser profile to stop
            
        Returns:
            True if profile is confirmed stopped, False if stop failed
        """
        try:
            # First check if profile is running
            if self.is_profile_running(profile_id):
                print(f'[INFO] Profile {profile_id} is currently running - stopping it first...')
                self.stop_profile(profile_id)
                time.sleep(3)  # Wait for cleanup
                
                # Verify it stopped
                if self.is_profile_running(profile_id):
                    print(f'[WARN] Profile {profile_id} still running after stop request')
                    # Try force stop one more time
                    self.stop_profile(profile_id)
                    time.sleep(2)
                    return not self.is_profile_running(profile_id)
                else:
                    print(f'[OK] Profile {profile_id} stopped successfully')
                    return True
            else:
                print(f'[OK] Profile {profile_id} is not running - ready to start')
                return True
                
        except Exception as e:
            print(f'[WARN] Error checking/stopping profile {profile_id}: {e}')
            # Try to stop anyway as a safety measure
            self.stop_profile(profile_id)
            time.sleep(2)
            return True  # Assume it worked
    
    def start_profile(self, profile_id: int, headless: bool = None, 
                      max_retries: int = 3, startup_timeout: int = 120) -> dict | None:
        """
        Start a browser profile using REST API with readiness verification.
        
        This method implements a deterministic startup sequence:
        1. Call the Dolphin Anty REST endpoint to start the profile
        2. Initial grace period (10s) to allow browser process to start binding to port
        3. Wait for the returned port to be open (browser process started)
        4. Verify the CDP endpoint is responsive (browser ready for automation)
        5. Return automation info only after readiness is confirmed
        
        Retry logic handles transient failures (timeouts, port not ready).
        Permanent errors (401, 403, 404) fail immediately.
        
        CRITICAL FIX: Extended timeouts and initial delay to fix port binding race
        condition on AWS Lightsail 2GB instances where browser startup is slower.
        
        Timing configuration:
        - startup_timeout: 120s (total timeout for entire startup sequence)
        - initial_delay: 10s (grace period BEFORE first port check - CRITICAL)
        - port_timeout: 90s (max wait for port to become available)
        - cdp_timeout: 20s (max wait for CDP endpoint to respond)
        - retry_cooldown: 8s (pause between retry attempts)
        - poll_interval: 0.75s (frequency of port availability checks)
        
        Args:
            profile_id: The ID of the browser profile to start
            headless: Run in headless mode. If None, defaults to True (always headless).
            max_retries: Number of retry attempts on transient failures (default: 3)
            startup_timeout: Max seconds to wait for browser readiness (default: 120)
            
        Returns:
            Automation info dict with port and wsEndpoint, or None on failure
        """
        # Default to headless mode (always run headless unless explicitly set to False)
        if headless is None:
            headless = True
        
        # =============================================================
        # PRE-START: Ensure profile is stopped before starting
        # =============================================================
        # This prevents 500 errors from trying to start an already-running profile
        print(f'[CHECK] Checking if profile {profile_id} is already running...')
        if not self.ensure_profile_stopped(profile_id):
            print(f'[WARN] Could not confirm profile {profile_id} is stopped - proceeding anyway')
        
        # Build base URL (port will be auto-assigned by Dolphin Anty)
        # Adding a random component ensures we get a fresh port allocation each time
        base_url = f'{self.local_api_url}/browser_profiles/{profile_id}/start?automation=1'
        if headless:
            base_url += '&headless=true'
        
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # =============================================================
                # STEP 1: Call REST API to start the profile
                # =============================================================
                # Add cache-busting parameter to ensure fresh port allocation on each attempt
                url = f'{base_url}&_t={int(time.time() * 1000)}'
                print(f'[INFO] Attempt {attempt + 1}/{max_retries}: Requesting new browser instance...')
                
                response = requests.get(url, headers=self.headers, timeout=30)
                
                # Handle permanent errors - fail fast, no retry
                if response.status_code == 401:
                    print(f'[ERR] Authentication failed (401) - check API token')
                    return None
                if response.status_code == 403:
                    print(f'[ERR] Access forbidden (403) - insufficient permissions')
                    return None
                if response.status_code == 404:
                    print(f'[ERR] Profile not found (404) - profile ID {profile_id} does not exist')
                    return None
                
                # Handle non-200 responses as transient errors
                if response.status_code != 200:
                    # Try to extract error details from response body
                    error_details = ''
                    try:
                        error_data = response.json()
                        error_details = error_data.get('error', error_data.get('message', ''))
                    except:
                        error_details = response.text[:200] if response.text else ''
                    
                    last_error = f'REST API returned status {response.status_code}'
                    print(f'[WARN] Profile start attempt {attempt + 1}/{max_retries}: {last_error}')
                    if error_details:
                        print(f'[WARN] Dolphin Anty error: {error_details}')
                    
                    # On 500 error, profile might be in bad state - try stopping it first
                    if response.status_code == 500:
                        print(f'[INFO] Attempting to stop profile {profile_id} before retry (may be stuck)...')
                        self.stop_profile(profile_id)
                        time.sleep(3)  # Give it time to fully stop
                    
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
                    continue
                
                # Parse response
                data = response.json()
                if not data.get('success'):
                    error_msg = data.get('error', 'Unknown error from Dolphin Anty')
                    last_error = f'Profile start failed: {error_msg}'
                    print(f'[WARN] Profile start attempt {attempt + 1}/{max_retries}: {last_error}')
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                    continue
                
                automation_info = data.get('automation', {})
                port = automation_info.get('port')
                ws_endpoint = automation_info.get('wsEndpoint', 'N/A')
                
                if not port:
                    last_error = 'No port returned in automation info'
                    print(f'[WARN] Profile start attempt {attempt + 1}/{max_retries}: {last_error}')
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                    continue
                
                # Log the assigned port for debugging
                print(f'[OK] Dolphin Anty assigned port {port} for this session')
                print(f'[INFO] WebSocket endpoint: {ws_endpoint}')
                
                # =============================================================
                # STEP 2: Initial grace period for browser process startup
                # =============================================================
                # CRITICAL FIX: 10s delay to fix port binding race condition on AWS Lightsail
                # The browser process needs time to start and bind to the port before we check.
                # Without this delay, we check too early and timeout waiting for a port that
                # the browser hasn't had time to bind to yet.
                initial_delay = 10
                print(f'[WAIT] Allowing browser process {initial_delay}s to initialize...')
                time.sleep(initial_delay)
                
                # =============================================================
                # STEP 3: Check if running remotely (skip port check if browser binds to 127.0.0.1)
                # =============================================================
                # When Dolphin Anty runs on a remote Windows server, the browser often binds
                # to 127.0.0.1 only, making it inaccessible from our Render server.
                # In this case, we skip the port/CDP checks and trust Dolphin Anty's response.
                is_remote = self.dolphin_host != 'localhost' and self.dolphin_host != '127.0.0.1'
                
                if is_remote:
                    print(f'[INFO] Remote Dolphin Anty detected ({self.dolphin_host})')
                    print(f'[INFO] Skipping port check (browser likely binds to 127.0.0.1 on Windows)')
                    print(f'[INFO] Trusting Dolphin Anty response - port {port} should be ready')
                    # Give extra time for browser to fully initialize
                    extra_delay = 5
                    print(f'[WAIT] Additional {extra_delay}s wait for remote browser stability...')
                    time.sleep(extra_delay)
                    # Return immediately, trusting the automation info
                    print(f'[OK] Profile started successfully (remote mode)')
                    return automation_info
                
                # =============================================================
                # STEP 4: Wait for port to be open (LOCAL mode only)
                # =============================================================
                # Extended timeout for AWS Lightsail 2GB instances with slower I/O
                port_timeout = 90
                print(f'[CHECK] Waiting up to {port_timeout}s for port {port}...')
                if not self._wait_for_port(port, timeout=port_timeout):
                    last_error = f'Timeout waiting for port {port} to open after {port_timeout}s'
                    print(f'[WARN] Profile start attempt {attempt + 1}/{max_retries}: {last_error}')
                    # Try to stop the profile before retrying
                    self.stop_profile(profile_id)
                    if attempt < max_retries - 1:
                        time.sleep(8)  # 8s cooldown between retries for AWS Lightsail
                    continue
                
                # =============================================================
                # STEP 5: Verify CDP endpoint is responsive (LOCAL mode only)
                # =============================================================
                # Extended CDP timeout for AWS Lightsail 2GB instances
                cdp_timeout = 20
                print(f'[CHECK] Verifying CDP endpoint is responsive (timeout: {cdp_timeout}s)...')
                if not self._verify_cdp_ready(port, timeout=cdp_timeout):
                    last_error = f'CDP endpoint not responsive on port {port}'
                    print(f'[WARN] Profile start attempt {attempt + 1}/{max_retries}: {last_error}')
                    # Try to stop the profile before retrying
                    self.stop_profile(profile_id)
                    if attempt < max_retries - 1:
                        time.sleep(8)  # 8s cooldown between retries for AWS Lightsail
                    continue
                
                # =============================================================
                # SUCCESS: Profile started and browser is ready
                # =============================================================
                return automation_info
                
            except requests.exceptions.Timeout:
                last_error = 'Request timeout'
                print(f'[WARN] Profile start attempt {attempt + 1}/{max_retries}: {last_error}')
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    
            except requests.exceptions.ConnectionError:
                last_error = 'Connection error - Dolphin Anty may not be running'
                print(f'[WARN] Profile start attempt {attempt + 1}/{max_retries}: {last_error}')
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    
            except Exception as e:
                last_error = f'Unexpected error: {str(e)}'
                print(f'[WARN] Profile start attempt {attempt + 1}/{max_retries}: {last_error}')
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
        
        # All retries exhausted
        print(f'[ERR] Failed to start profile {profile_id} after {max_retries} attempts: {last_error}')
        return None
    
    def stop_profile(self, profile_id: int) -> bool:
        """Stop a running browser profile"""
        response = requests.get(
            f'{self.local_api_url}/browser_profiles/{profile_id}/stop',
            headers=self.headers
        )
        return response.status_code == 200


# Instagram selectors for Playwright
class InstagramSelectors:
    """CSS/XPath selectors for Instagram elements"""
    
    # Login page
    USERNAME_INPUT = 'input[name="username"]'
    PASSWORD_INPUT = 'input[name="password"]'
    LOGIN_BUTTON = 'button[type="submit"]'
    
    # Cookie consent
    COOKIE_ACCEPT_BUTTON = 'button:has-text("Allow all cookies"), button:has-text("Accept"), button:has-text("Allow essential and optional cookies")'
    
    # Post-login prompts
    SAVE_LOGIN_NOT_NOW = 'button:has-text("Not Now"), div[role="button"]:has-text("Not Now")'
    NOTIFICATIONS_NOT_NOW = 'button:has-text("Not Now"), div[role="button"]:has-text("Not Now")'
    
    # Logged in indicators
    HOME_NAV = 'a[href="/"], svg[aria-label="Home"]'
    PROFILE_ICON = 'img[alt*="profile picture"], span[role="link"] img'
    SEARCH_ICON = 'svg[aria-label="Search"], a[href="/explore/"]'
    
    # Profile page - Posts
    POST_LINKS = 'a[href*="/p/"], a[href*="/reel/"]'
    
    # ===========================================
    # INDIVIDUAL POST PAGE SELECTORS
    # Based on Instagram's actual DOM structure
    # ===========================================
    
    # COMMENT INPUT - the bottom bar with "Add a comment..."
    # This is DIFFERENT from reply inputs within comment threads
    # The main comment input is at the bottom of the post, has specific aria-label
    # Structure: form > textarea[aria-label="Add a comment…"]
    COMMENT_INPUT_FORM = 'article form textarea, section form textarea'
    COMMENT_INPUT = 'textarea[aria-label="Add a comment…"], textarea[placeholder="Add a comment…"]'
    
    # POST BUTTON - appears ONLY after text is typed in the comment input
    # It's a sibling/nearby element to the textarea, becomes visible when there's text
    POST_COMMENT_BUTTON = 'div[role="button"]:text("Post"), button:text("Post"), form div:text("Post")'
    
    # Timestamp for filtering posts by date
    POST_TIMESTAMP = 'time[datetime]'
    
    # Close button for post modal
    CLOSE_POST_BUTTON = 'svg[aria-label="Close"], button[aria-label="Close"]'
    
    # Post modal navigation (for navigating between posts without page reload)
    NEXT_POST_BUTTON = 'button[aria-label="Next"], div[role="button"] svg[aria-label="Next"]'
    PREV_POST_BUTTON = 'button[aria-label="Go Back"], div[role="button"] svg[aria-label="Go Back"]'
    
    # Post row on profile grid
    POST_ROW = 'article > div > div > div'
    FIRST_POST = 'article a[href*="/p/"], article a[href*="/reel/"]'


async def detect_bot_challenge(page: Page) -> bool:
    """
    Detect if Instagram is showing a bot challenge or human verification.
    
    Args:
        page: Playwright page object
    
    Returns:
        True if bot challenge detected, False otherwise
    """
    try:
        page_content = await page.content()
        
        # Check for common bot challenge phrases
        bot_challenge_phrases = [
            "Prove that you are not a bot",
            "Confirm you're human",
            "Confirm you're not a bot",
            "Verify you're human",
            "Verify you're not a bot",
            "We need to confirm that you're human",
            "Complete the security check",
            "Suspicious activity",
            "Unusual activity"
        ]
        
        for phrase in bot_challenge_phrases:
            if phrase.lower() in page_content.lower():
                return True
        
        # Check for CAPTCHA elements
        captcha_selectors = [
            '[name="captcha"]',
            '#captcha',
            '.captcha',
            '[data-testid="captcha"]'
        ]
        
        for selector in captcha_selectors:
            element = await page.query_selector(selector)
            if element:
                return True
        
        return False
        
    except Exception as e:
        # If we can't detect, assume no challenge
        return False


async def verify_instagram_login(page: Page) -> bool:
    """
    Verify if we're logged into Instagram.
    
    Args:
        page: Playwright page object
    
    Returns:
        True if logged in, False otherwise
    """
    try:
        # Check for logged-in elements
        selectors_to_check = [
            InstagramSelectors.HOME_NAV,
            InstagramSelectors.SEARCH_ICON,
            'svg[aria-label="New post"]',
            'a[href*="/direct/inbox/"]',
        ]
        
        for selector in selectors_to_check:
            try:
                element = await page.wait_for_selector(selector, timeout=3000)
                if element:
                    # Silent verification success
                    return True
            except:
                continue
        
        # Also check if login form is NOT present (means we're logged in)
        try:
            login_form = await page.query_selector(InstagramSelectors.USERNAME_INPUT)
            if login_form is None:
                # No login form found, might be logged in
                # Check URL
                current_url = page.url
                if 'login' not in current_url and 'accounts' not in current_url:
                    # Silent verification success
                    return True
        except:
            pass
        
        # Not logged in - silent return
        return False
        
    except Exception as e:
        # Silent verification failure
        return False


async def perform_instagram_login(page: Page, username: str, password: str):
    """
    Perform a fresh Instagram login using Playwright.
    
    Args:
        page: Playwright page object
        username: Instagram username/email/phone
        password: Instagram password
    """
    # Navigate to Instagram login page with retry logic
    progress.action('Navigating to login page')
    await navigate_with_retry(page, 'https://www.instagram.com/accounts/login/?hl=en')
    
    # Accept cookies if prompted
    try:
        cookie_button = await page.wait_for_selector(InstagramSelectors.COOKIE_ACCEPT_BUTTON, timeout=5000)
        if cookie_button:
            await cookie_button.click()
            await asyncio.sleep(1)
            progress.info('Cookie consent accepted', significant=False)
    except:
        # No cookie banner - silent continue
        pass
    
    # Wait for login form
    progress.action('Waiting for login form')
    await page.wait_for_selector(InstagramSelectors.USERNAME_INPUT, timeout=15000)
    await asyncio.sleep(1)
    
    # Enter username/email
    progress.action(f'Entering credentials for {username}')
    username_input = await page.query_selector(InstagramSelectors.USERNAME_INPUT)
    await username_input.click()
    await username_input.fill(username)
    await asyncio.sleep(0.5)
    
    # Enter password
    password_input = await page.query_selector(InstagramSelectors.PASSWORD_INPUT)
    await password_input.click()
    await password_input.fill(password)
    await asyncio.sleep(0.5)
    
    # Click login button
    progress.action('Submitting login form')
    login_button = await page.query_selector(InstagramSelectors.LOGIN_BUTTON)
    await login_button.click()
    await asyncio.sleep(5)  # Wait for login to process
    
    # Handle "Save Your Login Info?" prompt if it appears
    try:
        not_now_button = await page.wait_for_selector(InstagramSelectors.SAVE_LOGIN_NOT_NOW, timeout=5000)
        if not_now_button:
            await not_now_button.click()
            await asyncio.sleep(2)
            progress.info('Declined to save login info', significant=False)
    except:
        # No save prompt - silent continue
        pass
    
    # Handle "Turn on Notifications?" prompt if it appears
    try:
        not_now_button = await page.wait_for_selector(InstagramSelectors.NOTIFICATIONS_NOT_NOW, timeout=5000)
        if not_now_button:
            await not_now_button.click()
            await asyncio.sleep(2)
            progress.info('Declined notifications', significant=False)
    except:
        # No notifications prompt - silent continue
        pass
    
    # Wait for page to stabilize
    progress.info('Waiting for login to complete', significant=False)
    await asyncio.sleep(3)
    
    # Check for bot challenge
    if await detect_bot_challenge(page):
        raise Exception('Instagram bot challenge detected - human verification required')





def parse_date_threshold(date_str: str) -> datetime:
    """
    Parse the date threshold from environment variable.
    
    Args:
        date_str: Date string in format YYYY-MM-DD
        
    Returns:
        datetime object
    """
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        # Silent fallback to default
        return datetime.now() - timedelta(days=7)


def parse_instagram_timestamp(timestamp_str: str) -> datetime | None:
    """
    Parse Instagram's timestamp format to datetime.
    Instagram uses ISO 8601 format in the datetime attribute.
    
    Args:
        timestamp_str: ISO 8601 timestamp string
        
    Returns:
        datetime object or None if parsing fails
    """
    try:
        # Instagram uses ISO 8601 format: 2024-12-04T10:30:00.000Z
        # Remove the 'Z' and parse
        clean_timestamp = timestamp_str.replace('Z', '+00:00')
        return datetime.fromisoformat(clean_timestamp.replace('+00:00', ''))
    except Exception as e:
        print(f'[WARN]  Could not parse timestamp: {timestamp_str} - {e}')
        return None


async def get_post_links_from_profile(page: Page, target_user: str, logger: AutomationLogger, max_posts: int = 50) -> list[str]:
    """
    Get post links from a user's profile page in chronological order (newest first).
    Instagram displays posts in a grid, with newest posts at the top-left.
    
    Args:
        page: Playwright page object
        target_user: Instagram username
        logger: AutomationLogger instance
        max_posts: Maximum number of posts to collect
        
    Returns:
        List of post URLs in order (newest first)
    """
    post_links = []
    
    try:
        # Navigate to the user's profile with retry
        progress.info(f'Loading profile page for @{target_user}', significant=False)
        await navigate_with_retry(page, f'https://www.instagram.com/{target_user}/?hl=en')
        
        # Wait for posts to load
        await asyncio.sleep(2)
        
        # Collect posts while scrolling - Instagram loads more as you scroll
        last_count = 0
        scroll_attempts = 0
        max_scroll_attempts = 10  # Limit scrolling to avoid infinite loops
        
        while len(post_links) < max_posts and scroll_attempts < max_scroll_attempts:
            # Find all post links currently visible
            post_elements = await page.query_selector_all(InstagramSelectors.POST_LINKS)
            
            for element in post_elements:
                href = await element.get_attribute('href')
                if href and ('/p/' in href or '/reel/' in href):
                    full_url = f'https://www.instagram.com{href}' if href.startswith('/') else href
                    if full_url not in post_links:
                        post_links.append(full_url)
                        logger.log_post_found()
            
            # Check if we found new posts
            if len(post_links) == last_count:
                scroll_attempts += 1
            else:
                scroll_attempts = 0  # Reset if we found new posts
                last_count = len(post_links)
            
            # Scroll down to load more posts if needed
            if len(post_links) < max_posts:
                await page.evaluate('window.scrollBy(0, window.innerHeight)')
                await asyncio.sleep(1.5)
        
        # Scroll back to top for clean state
        await page.evaluate('window.scrollTo(0, 0)')
        await asyncio.sleep(1)
        
        logger.log_success(f'Found {len(post_links)} posts on @{target_user} profile')
        
    except Exception as e:
        progress.error(f'Unable to scan profile posts: {str(e)[:50]}')
    
    return post_links


async def get_post_timestamp(page: Page) -> datetime | None:
    """
    Get the timestamp of the current post.
    
    Args:
        page: Playwright page object (should be on a post page)
        
    Returns:
        datetime object or None
    """
    try:
        time_element = await page.wait_for_selector(InstagramSelectors.POST_TIMESTAMP, timeout=ELEMENT_TIMEOUT)
        if time_element:
            datetime_attr = await time_element.get_attribute('datetime')
            if datetime_attr:
                return parse_instagram_timestamp(datetime_attr)
    except Exception as e:
        print(f'[WARN]  Could not get post timestamp: {e}')
    return None


async def comment_on_post(page: Page, comment_text: str, logger: AutomationLogger) -> bool:
    """
    Add a comment to the current post.
    Targets the comment input bar at the BOTTOM of the post (not reply fields in comments).
    
    The comment bar shows "Add a comment..." and the POST button only appears after typing.
    
    Args:
        page: Playwright page object (should be on a post page)
        comment_text: The comment to post
        logger: AutomationLogger instance
        
    Returns:
        True if comment was posted, False otherwise
    """
    
    async def find_bottom_comment_input():
        """
        Find the main comment input at the bottom of the post.
        This is different from reply inputs within the comment thread.
        """
        # Strategy 1: Find textarea with specific aria-label within a form
        try:
            # The bottom comment bar is usually in a form element
            input_el = await page.query_selector('form textarea[aria-label="Add a comment…"]')
            if input_el:
                return input_el
        except:
            pass
        
        # Strategy 2: Use the form-based selector
        try:
            input_el = await page.query_selector(InstagramSelectors.COMMENT_INPUT_FORM)
            if input_el:
                return input_el
        except:
            pass
        
        # Strategy 3: Find via JavaScript - get the last/bottom-most comment textarea
        try:
            input_el = await page.evaluate_handle('''() => {
                // Get all comment textareas
                const textareas = document.querySelectorAll('textarea[aria-label="Add a comment…"], textarea[placeholder="Add a comment…"]');
                if (textareas.length === 0) return null;
                
                // Find the one that's in a form (main comment input)
                for (const ta of textareas) {
                    const form = ta.closest('form');
                    if (form) {
                        return ta;
                    }
                }
                
                // Fallback: return the last textarea (usually the main one at bottom)
                return textareas[textareas.length - 1];
            }''')
            if input_el:
                return input_el
        except:
            pass
        
        # Strategy 4: Generic selector as last resort
        try:
            input_el = await page.query_selector(InstagramSelectors.COMMENT_INPUT)
            if input_el:
                return input_el
        except:
            pass
        
        return None
    
    async def find_post_button():
        """
        Find the POST button that appears after typing in the comment field.
        This button only becomes visible/enabled after text is entered.
        """
        # Strategy 1: Find by text content
        try:
            # Look for elements with text "Post" near the comment input
            btn = await page.query_selector('form div[role="button"]:has-text("Post"), form button:has-text("Post")')
            if btn:
                return btn
        except:
            pass
        
        # Strategy 2: Use JavaScript to find the Post button
        try:
            btn = await page.evaluate_handle('''() => {
                // Find all elements with "Post" text
                const elements = document.querySelectorAll('div[role="button"], button');
                for (const el of elements) {
                    if (el.textContent.trim() === 'Post') {
                        // Make sure it's near a form/textarea (the comment form)
                        const form = el.closest('form') || el.closest('section');
                        if (form) {
                            return el;
                        }
                    }
                }
                return null;
            }''')
            if btn:
                return btn
        except:
            pass
        
        # Strategy 3: Use the selector from constants
        try:
            btn = await page.query_selector(InstagramSelectors.POST_COMMENT_BUTTON)
            if btn:
                return btn
        except:
            pass
        
        return None
    
    for attempt in range(IG_MAX_COMMENT_RETRIES):
        try:
            progress.info(f'Preparing to comment', significant=False)
            
            # Find the bottom comment input
            comment_input = await find_bottom_comment_input()
            
            if not comment_input:
                progress.warning(f'Comment box not found, retrying ({attempt + 1}/{IG_MAX_COMMENT_RETRIES})')
                if attempt < IG_MAX_COMMENT_RETRIES - 1:
                    await asyncio.sleep(1)
                    continue
                else:
                    progress.error('Comment box not available after all retries')
                    return False
            
            # Scroll the comment input into view (ensure we don't scroll away from post)
            try:
                await comment_input.scroll_into_view_if_needed()
                await asyncio.sleep(get_random_delay(0.2, 0.5))
            except:
                pass
            
            # Human-like click on the comment input
            await human_like_click(page, comment_input, logger)
            await asyncio.sleep(get_random_delay(0.3, 0.7))
            
            # Clear any existing text
            try:
                await comment_input.fill('')
                await asyncio.sleep(0.2)
            except:
                # If fill doesn't work, try selecting all and deleting
                await comment_input.press('Meta+a')
                await comment_input.press('Backspace')
                await asyncio.sleep(0.2)
            
            # Type the comment with human-like patterns (variable delays, word pauses, occasional typos)
            progress.info('Typing comment naturally', significant=False)
            await human_like_type(page, comment_input, comment_text, logger)
            
            # Review pause - simulating reading back what was typed
            await do_review_pause(logger)
            
            # Wait for the POST button to appear (it only shows after typing)
            progress.info('Submitting comment', significant=False)
            await asyncio.sleep(get_random_delay(0.3, 0.6))
            
            post_button = await find_post_button()
            
            if post_button:
                # Human-like click on the POST button
                await human_like_click(page, post_button, logger)
                
                await asyncio.sleep(get_random_delay(1.5, 2.5))
                
                # Verify comment was posted by checking if input is cleared
                try:
                    comment_input = await find_bottom_comment_input()
                    if comment_input:
                        current_value = await comment_input.input_value()
                        if not current_value or len(current_value) < len(comment_text):
                            logger.log_success(f'[OK] Comment posted successfully: "{comment_text}"')
                            progress.info(f'Comment submitted: "{comment_text}"', significant=True)
                            return True
                except:
                    # If we can't check, assume it worked
                    logger.log_success(f'[OK] Comment likely posted: "{comment_text}"')
                    progress.info(f'Comment submitted: "{comment_text}"', significant=True)
                    return True
                
                progress.warning('Post button clicked but waiting for confirmation')
            else:
                progress.warning('Post button did not appear')
                
                # Fallback: Try pressing Enter to submit
                await comment_input.press('Enter')
                await asyncio.sleep(2)
                
                # Check if comment was posted
                try:
                    comment_input = await find_bottom_comment_input()
                    if comment_input:
                        current_value = await comment_input.input_value()
                        if not current_value or len(current_value) < len(comment_text):
                            logger.log_success(f'[OK] Comment posted via Enter: "{comment_text}"')
                            return True
                except:
                    logger.log_success(f'[OK] Comment likely posted via Enter: "{comment_text}"')
                    return True
            
        except Exception as e:
            progress.warning(f'Comment attempt failed, retrying ({attempt + 1}/{IG_MAX_COMMENT_RETRIES})')
            if attempt < IG_MAX_COMMENT_RETRIES - 1:
                await asyncio.sleep(1)
    
    progress.error(f'Could not submit comment after {IG_MAX_COMMENT_RETRIES} attempts')
    return False


async def process_posts_after_date(
    page: Page, 
    target_user: str, 
    date_threshold: datetime, 
    comment_text: str,
    logger: AutomationLogger
) -> dict:
    """
    Process posts from a user that were posted after the given date.
    Comments on each qualifying post.
    
    Uses early termination: since Instagram posts are displayed newest first,
    once we encounter CONSECUTIVE_OLD_POSTS_LIMIT posts older than the threshold,
    we stop processing to avoid unnecessary work.
    
    Args:
        page: Playwright page object
        target_user: Instagram username to process posts from
        date_threshold: Only process posts after this date
        comment_text: Comment to post on each post
        logger: AutomationLogger instance
        
    Returns:
        Dict with processing results
    """
    result = {
        "success": True,
        "posts_found": 0,
        "posts_processed": 0,
        "posts_commented": 0,
        "posts_skipped": 0,
        "stopped_early": False,
        "errors": []
    }
    
    progress.scanning_posts(target_user)
    
    # Get post links from the profile (newest first)
    post_links = await get_post_links_from_profile(page, target_user, logger)
    result["posts_found"] = len(post_links)
    
    if not post_links:
        progress.warning('No posts found on this profile')
        return result
    
    progress.post_found(len(post_links))
    
    # Track consecutive old posts for early termination
    consecutive_old_posts = 0
    
    # Process each post (newest to oldest)
    for i, post_url in enumerate(post_links):
        progress.info(f'Analyzing post {i + 1} of {len(post_links)}', significant=False)
        
        try:
            # Navigate to the post with retry
            await navigate_with_retry(page, post_url)
            
            # Get the post timestamp
            post_date = await get_post_timestamp(page)
            
            if post_date:
                progress.info(f'Post from {post_date.strftime("%b %d")}', significant=False)
                
                # Check if post is after the threshold date
                if post_date < date_threshold:
                    consecutive_old_posts += 1
                    progress.post_skipped(f'post too old ({consecutive_old_posts}/{CONSECUTIVE_OLD_POSTS_LIMIT})')
                    logger.log_post_processed(skipped=True)
                    result["posts_skipped"] += 1
                    
                    # Early termination: stop if we've hit too many consecutive old posts
                    if consecutive_old_posts >= CONSECUTIVE_OLD_POSTS_LIMIT:
                        progress.warning(f'Stopping after {CONSECUTIVE_OLD_POSTS_LIMIT} consecutive old posts')
                        result["stopped_early"] = True
                        break
                    continue
                else:
                    # Reset counter when we find a new post
                    consecutive_old_posts = 0
            else:
                progress.info('Processing post (date unavailable)', significant=False)
                # Don't count as old post if we can't determine date
            
            # Comment on the post
            progress.commenting_on_post(i + 1, len(post_links), target_user)
            commented = await comment_on_post(page, comment_text, logger)
            if commented:
                result["posts_commented"] += 1
                progress.comment_submitted(i + 1, len(post_links), comment_text)
            
            logger.log_post_processed(commented=commented)
            result["posts_processed"] += 1
            
            # Human-like delay between posts (3-7 seconds)
            await do_post_to_post_delay(logger)
            
        except Exception as e:
            error_msg = f'Error processing post {post_url}: {e}'
            progress.error(f'Post processing failed: {str(e)[:50]}')
            result["errors"].append(error_msg)
            # Continue to next post even if this one fails
    
    return result


async def process_posts_by_count(
    page: Page, 
    target_user: str, 
    post_count: int,
    comment_text: str,
    logger: AutomationLogger
) -> dict:
    """
    Process a fixed number of posts from a user (newest first).
    Comments on the specified number of posts.
    
    Args:
        page: Playwright page object
        target_user: Instagram username to process posts from
        post_count: Number of posts to process
        comment_text: Comment to post on each post
        logger: AutomationLogger instance
        
    Returns:
        Dict with processing results
    """
    result = {
        "success": True,
        "posts_found": 0,
        "posts_processed": 0,
        "posts_commented": 0,
        "posts_skipped": 0,
        "stopped_early": False,
        "errors": []
    }
    
    progress.scanning_posts(target_user)
    
    # Get post links from the profile (newest first)
    post_links = await get_post_links_from_profile(page, target_user, logger)
    result["posts_found"] = len(post_links)
    
    if not post_links:
        progress.warning('No posts found on this profile')
        return result
    
    # Limit to the specified number of posts
    posts_to_process = post_links[:post_count]
    progress.info(f'Processing {len(posts_to_process)} most recent posts', significant=False)
    
    progress.post_found(len(posts_to_process))
    
    # Process each post
    for i, post_url in enumerate(posts_to_process):
        progress.info(f'Analyzing post {i + 1} of {len(posts_to_process)}', significant=False)
        
        try:
            # Navigate to the post with retry
            await navigate_with_retry(page, post_url)
            
            # Get the post timestamp (for logging purposes)
            post_date = await get_post_timestamp(page)
            if post_date:
                progress.info(f'Post from {post_date.strftime("%b %d")}', significant=False)
            
            # Comment on the post
            progress.commenting_on_post(i + 1, len(posts_to_process), target_user)
            commented = await comment_on_post(page, comment_text, logger)
            if commented:
                result["posts_commented"] += 1
                progress.comment_submitted(i + 1, len(posts_to_process), comment_text)
            
            logger.log_post_processed(commented=commented)
            result["posts_processed"] += 1
            
            # Human-like delay between posts (3-7 seconds)
            await do_post_to_post_delay(logger)
            
        except Exception as e:
            error_msg = f'Error processing post {post_url}: {e}'
            progress.error(f'Post processing failed: {str(e)[:50]}')
            result["errors"].append(error_msg)
            # Continue to next post even if this one fails
    
    return result


async def instagram_login(page: Page, username: str, password: str, target_user: str):
    """
    Check Instagram login status and login if needed.
    Browser profiles maintain their own sessions.
    
    Args:
        page: Playwright page object
        username: Instagram username/email/phone
        password: Instagram password
        target_user: Instagram username to navigate to
    
    Raises:
        Exception: If bot challenge is detected
    """
    
    # Navigate to Instagram and check if already logged in
    progress.action('Checking login status')
    await navigate_with_retry(page, 'https://www.instagram.com/?hl=en')
    
    # Check for bot challenge immediately
    if await detect_bot_challenge(page):
        raise Exception('Instagram bot challenge detected - account flagged for verification')
    
    # Verify if we're already logged in
    is_logged_in = await verify_instagram_login(page)
    
    if is_logged_in:
        progress.success(f'Already logged in as @{username}')
    else:
        # Not logged in, perform fresh login
        progress.warning('Not logged in, logging in now')
        await perform_instagram_login(page, username, password)
        
        # Check for bot challenge after login
        if await detect_bot_challenge(page):
            raise Exception('Instagram bot challenge detected - human verification required')
    
    # Navigate to target user's profile with retry
    progress.navigating_to_profile(target_user)
    await navigate_with_retry(page, f'https://www.instagram.com/{target_user}/?hl=en')
    
    # Final bot challenge check after navigation
    if await detect_bot_challenge(page):
        raise Exception('Instagram bot challenge detected on profile page')


# ===========================================
# SUPABASE DATABASE HELPERS
# ===========================================

def get_supabase_client() -> Client:
    """
    Initialize and return Supabase client.
    """
    url = os.getenv('SUPABASE_URL')
    key = os.getenv('SUPABASE_ANON_KEY')
    
    if not url or not key:
        raise ValueError('SUPABASE_URL and SUPABASE_ANON_KEY must be set in .env file')
    
    return create_client(url, key)


def get_next_campaigns():
    """
    Get pending campaigns from Supabase database ordered by queue_position.
    
    Returns:
        List of campaign dictionaries with:
        - campaign_id: unique identifier
        - custom_comment: comment text
        - platform: social media platform
        - user_accounts: list of account usernames to use
        - target_profiles: list of profiles to target
        - targeting_mode: how to target (by count/date)
        - target_date: date threshold for filtering
        - number_of_posts: post count limit
        - status: campaign status
    """
    try:
        supabase = get_supabase_client()
        
        # Query campaigns with status 'not-started' ordered by queue_position
        response = supabase.table('comment_campaigns').select('*').eq('status', 'not-started').order('queue_position').execute()
        
        if response.data:
            print(f'[DB] Found {len(response.data)} pending campaign(s)')
            return response.data
        else:
            print('[DB] No pending campaigns found')
            return []
    
    except Exception as e:
        print(f'[ERR] Could not load campaigns from database: {e}')
        return []


def update_campaign_status(campaign_id: str, status: str):
    """
    Update campaign status in database.
    
    Args:
        campaign_id: Campaign identifier
        status: New status ('not-started', 'in-progress', 'completed', 'failed')
    """
    try:
        supabase = get_supabase_client()
        supabase.table('comment_campaigns').update({
            'status': status,
            'updated_at': datetime.now().isoformat()
        }).eq('campaign_id', campaign_id).execute()
        print(f'[DB] Updated campaign {campaign_id} status to: {status}')
    except Exception as e:
        print(f'[ERR] Could not update campaign status: {e}')


def get_account_credentials(username: str, platform: str = 'instagram'):
    """
    Get account credentials from social_accounts table.
    
    Args:
        username: Account username
        platform: Social media platform (default: 'instagram')
        
    Returns:
        Dict with username, password, and browser_profile, or None if not found
    """
    try:
        supabase = get_supabase_client()
        response = supabase.table('social_accounts').select('username,password,browser_profile').eq('username', username).eq('platform', platform).eq('is_active', True).limit(1).execute()
        
        if response.data and len(response.data) > 0:
            return response.data[0]
        else:
            print(f'[WARN] No credentials found for {username} on {platform}')
            return None
    except Exception as e:
        print(f'[ERR] Could not get account credentials: {e}')
        return None


def get_env_config():
    """
    Get task configuration from .env file.
    
    Returns:
        Dict with task configuration
    """
    # Determine mode from .env
    mode = os.getenv('COMMENT_MODE', 'date').lower()  # 'date' or 'count'
    
    config = {
        'mode': mode,
        'comment_text': os.getenv('COMMENT_TEXT', 'Great post!'),
        'target_users': load_target_users()
    }
    
    if mode == 'count':
        config['post_count'] = int(os.getenv('POST_COUNT', '10'))
    else:  # date mode
        date_filter_str = os.getenv('DATE_FILTER') or os.getenv('DATE_THRESHOLD', '')
        if date_filter_str:
            config['date_threshold'] = parse_date_threshold(date_filter_str)
        else:
            config['date_threshold'] = datetime.now() - timedelta(days=7)
    
    print('[ENV] Loaded configuration from .env file')
    return config


def load_target_users() -> list:
    """
    Load target users from environment variables.
    Supports up to 5 target users (INSTAGRAM_TARGET_USER_1 through INSTAGRAM_TARGET_USER_5).
    
    Returns:
        List of target usernames (empty strings are filtered out)
    """
    target_users = []
    for i in range(1, 6):  # Support max 5 target users
        user = os.getenv(f'INSTAGRAM_TARGET_USER_{i}', '').strip()
        if user:  # Only add non-empty usernames
            target_users.append(user)
    
    # Fallback to old single target user format
    if not target_users:
        legacy_target = os.getenv('INSTAGRAM_TARGET_USER', '').strip()
        if legacy_target:
            target_users = [legacy_target]
    
    return target_users


class PreFlightCheckResult:
    """Result of pre-flight validation checks"""
    
    def __init__(self, success: bool, message: str = "", errors: List[str] = None):
        self.success = success
        self.message = message
        self.errors = errors or []


async def run_campaign_preflight_checks(campaign: dict) -> PreFlightCheckResult:
    """
    Run all validation checks before starting a campaign.
    If ANY check fails, return False and skip this campaign.
    
    Checks:
    1. Campaign has required fields
    2. User accounts are specified
    3. Target profiles are specified
    4. Account credentials exist in database
    5. Dolphin Anty is reachable
    6. Dolphin Anty has available browser profiles
    
    Args:
        campaign: Campaign dictionary from database
        
    Returns:
        PreFlightCheckResult with success status and error messages
    """
    errors = []
    
    # Check 1: Required campaign fields
    required_fields = ['campaign_id', 'user_accounts', 'target_profiles', 'custom_comment', 'platform']
    for field in required_fields:
        if field not in campaign or not campaign[field]:
            errors.append(f'Missing required field: {field}')
    
    if errors:
        return PreFlightCheckResult(False, "Campaign missing required configuration", errors)
    
    # Check 2: User accounts specified
    user_accounts = campaign.get('user_accounts', [])
    if not user_accounts or len(user_accounts) == 0:
        errors.append('No user accounts specified in campaign')
        return PreFlightCheckResult(False, "No accounts configured", errors)
    
    # Check 3: Target profiles specified
    target_profiles = campaign.get('target_profiles', [])
    if not target_profiles or len(target_profiles) == 0:
        errors.append('No target profiles specified in campaign')
        return PreFlightCheckResult(False, "No target profiles configured", errors)
    
    # Check 4: Account credentials exist
    platform = campaign.get('platform', 'instagram')
    account_username = user_accounts[0]
    
    credentials = get_account_credentials(account_username, platform)
    if not credentials:
        errors.append(f'Account credentials not found: @{account_username}')
        return PreFlightCheckResult(False, f"Account credentials missing for @{account_username}", errors)
    
    # Check 4b: Browser profile is assigned
    browser_profile_name = credentials.get('browser_profile', '')
    if not browser_profile_name:
        errors.append(f'No browser profile assigned to account @{account_username}')
        return PreFlightCheckResult(False, f"Browser profile not assigned to @{account_username}", errors)
    
    # Check 5: Dolphin Anty connection
    dolphin = DolphinAntyClient()
    
    if not dolphin.login():
        errors.append('Cannot connect to Dolphin Anty - browser service not running')
        return PreFlightCheckResult(False, "Anti-detect browser unreachable", errors)
    
    # Check 6: Assigned browser profile exists in Dolphin Anty
    # Try to find by name first (primary method), then by ID as fallback
    profile = dolphin.find_profile_by_name(browser_profile_name)
    if not profile:
        profile = dolphin.find_profile_by_id(browser_profile_name)
    
    if not profile:
        errors.append(f'Browser profile "{browser_profile_name}" not found in Dolphin Anty')
        return PreFlightCheckResult(False, f"Browser profile '{browser_profile_name}' not found", errors)
    
    # All checks passed
    return PreFlightCheckResult(
        success=True,
        message=f"All checks passed - ready to run campaign {campaign.get('campaign_id')}"
    )


async def run_automation_with_dolphin_anty():
    """
    Main function to run Playwright automation using Dolphin Anty's anti-detect browser.
    
    Queue processing:
    1. Get pending campaigns ordered by queue_position
    2. For EACH campaign:
       a. Run pre-flight checks
       b. If checks FAIL: log errors, skip to next campaign (leave status as 'not-started')
       c. If checks PASS: change status to 'in-progress' and run campaign
       d. On completion: change status to 'completed'
    3. Return results for all campaigns
    """
    
    # Get pending campaigns from database
    campaigns = get_next_campaigns()
    
    if not campaigns:
        print('[INFO] No campaigns to process. Exiting.')
        return
    
    print(f'\n[INFO] Processing {len(campaigns)} campaign(s)...')
    
    # Track results for API response
    campaign_results = []
    
    # Process each campaign in queue
    for campaign_idx, campaign in enumerate(campaigns, 1):
        campaign_id = campaign.get("campaign_id")
        
        print('\n' + '='*70)
        print(f'[CAMPAIGN] {campaign_idx}/{len(campaigns)}: {campaign_id}')
        print('='*70)
        
        # Emit campaign start event
        progress.campaign_started(campaign_id, campaign_idx, len(campaigns))
        
        # ====================================================================
        # STEP 1: RUN PRE-FLIGHT CHECKS (before changing status)
        # ====================================================================
        print('\n[CHECK] Running pre-flight validation checks...')
        check_result = await run_campaign_preflight_checks(campaign)
        
        if not check_result.success:
            # Checks failed - skip this campaign
            print(f'\n[SKIP] Campaign {campaign_id} failed pre-flight checks:')
            for error in check_result.errors:
                print(f'  - {error}')
                progress.warning(error)
            
            campaign_results.append({
                'campaign_id': campaign_id,
                'status': 'skipped',
                'reason': check_result.message,
                'errors': check_result.errors
            })
            
            # Skip to next campaign (DO NOT change status from 'not-started')
            continue
        
        print(f'[OK] All pre-flight checks passed!')
        print(f'    - Campaign configuration valid')
        print(f'    - Account credentials found')
        print(f'    - Anti-detect browser connected')
        print(f'    - Browser profiles available')
        
        # ====================================================================
        # STEP 2: CHANGE STATUS TO 'in-progress' (checks passed)
        # ====================================================================
        print(f'\n[STATUS] Updating campaign status to: in-progress')
        update_campaign_status(campaign_id, 'in-progress')
        
        # Extract campaign configuration
        platform = campaign.get('platform', 'instagram')
        user_accounts = campaign.get('user_accounts', [])
        target_users = campaign.get('target_profiles', [])
        comment_text = campaign.get('custom_comment', 'Great post!')
        
        print(f'\n[INFO] Campaign will run using {len(user_accounts)} account(s):')
        for idx, acc in enumerate(user_accounts, 1):
            print(f'    {idx}. @{acc}')
        
        # Determine mode (count vs date)
        number_of_posts = campaign.get('number_of_posts')
        target_date = campaign.get('target_date')
        
        if number_of_posts is not None and number_of_posts > 0:
            mode = 'count'
            post_count = number_of_posts
        elif target_date is not None:
            mode = 'date'
            date_threshold = datetime.fromisoformat(target_date.replace('Z', '+00:00'))
        else:
            mode = 'date'
            date_threshold = datetime.now() - timedelta(days=7)
        
        # ====================================================================
        # STEP 3: PROCESS EACH USER ACCOUNT IN SEQUENCE
        # Each account runs in its own browser profile with the same criteria
        # ====================================================================
        
        campaign_success = False
        all_accounts_successful = True
        
        for account_idx, account_username in enumerate(user_accounts, 1):
            print('\n' + '='*70)
            print(f'[ACCOUNT] {account_idx}/{len(user_accounts)}: @{account_username}')
            print('='*70)
            
            # Get credentials for this account
            credentials = get_account_credentials(account_username, platform)
            if not credentials:
                print(f'[ERR] Could not get credentials for @{account_username}, skipping...')
                all_accounts_successful = False
                continue
            
            instagram_username = credentials['username']
            instagram_password = credentials['password']
            browser_profile_name = credentials.get('browser_profile', '')
            
            if not browser_profile_name:
                print(f'[ERR] No browser profile assigned to @{account_username}, skipping...')
                all_accounts_successful = False
                continue
            
            # Print configuration for this account
            print('\n' + '='*50)
            print('[CONFIG] CONFIGURATION')
            print('='*50)
            print(f'   Campaign ID: {campaign_id}')
            print(f'   Platform: {platform.upper()}')
            print(f'   Account: @{instagram_username}')
            print(f'   Browser Profile: {browser_profile_name}')
            print(f'   Target Profiles ({len(target_users)}): ' + ', '.join([f'@{u}' for u in target_users]))
            print(f'   Comment Text: "{comment_text}"')
            
            if mode == 'count':
                print(f'   Posts per Profile: {post_count}')
            else:
                print(f'   Date Filter: {date_threshold.strftime("%Y-%m-%d")} (posts after this date will be processed)')
                print(f'   Early Stop: After {CONSECUTIVE_OLD_POSTS_LIMIT} consecutive old posts')
            
            print('='*50)
            
            # Initialize helpers for this account
            logger = AutomationLogger()
            dolphin = DolphinAntyClient()
            
            account_success = False
            playwright = None
            browser = None
            profile_id = None
            
            try:
                # Connect to Dolphin Anty and show detailed connection info
                if not dolphin.login(show_progress=True):
                    raise Exception("Failed to connect to Dolphin Anty")
                
                # Get and display all available profiles
                print('[CONFIG] Fetching browser profiles...')
                all_profiles = dolphin.get_profiles()
                if all_profiles:
                    print(f'[CONFIG] Found {len(all_profiles)} profile(s):')
                    for p in all_profiles:
                        print(f'  - ID: {p.get("id")}, Name: {p.get("name")}')
                    print()
                else:
                    print('[WARN] No browser profiles found\n')
                
                # Find the assigned browser profile by name
                if not browser_profile_name:
                    raise Exception(f"No browser profile assigned to account @{instagram_username}")
                
                print(f"[>>] Looking for assigned profile: {browser_profile_name}")
                
                # Try to find by name first (primary method)
                profile = dolphin.find_profile_by_name(browser_profile_name)
                
                # If not found by name, try by ID as fallback
                if not profile:
                    profile = dolphin.find_profile_by_id(browser_profile_name)
                
                if not profile:
                    raise Exception(f"Browser profile '{browser_profile_name}' not found in Dolphin Anty")
                
                profile_id = profile.get('id')
                print(f"[OK] Found profile: {profile.get('name')} (ID: {profile_id})")
                print(f"\n[>>] Starting profile: {profile.get('name')} (ID: {profile_id})")
                
                progress.browser_launching()
                automation_info = dolphin.start_profile(profile_id)
                if not automation_info:
                    raise Exception("Failed to start Dolphin Anty profile")
                
                ws_endpoint = automation_info.get('wsEndpoint')
                port = automation_info.get('port')
                
                # Extract hostname from DOLPHIN_LOCAL_API_URL for remote VPS connections
                from urllib.parse import urlparse
                dolphin_url = os.getenv('DOLPHIN_LOCAL_API_URL', 'http://localhost:3001')
                parsed_url = urlparse(dolphin_url)
                dolphin_host = parsed_url.hostname or 'localhost'
                
                # Build the full CDP WebSocket URL using the Dolphin Anty host
                if ws_endpoint.startswith('/'):
                    cdp_url = f"ws://{dolphin_host}:{port}{ws_endpoint}"
                elif ws_endpoint.startswith('ws://') or ws_endpoint.startswith('wss://'):
                    cdp_url = ws_endpoint
                else:
                    cdp_url = f"ws://{dolphin_host}:{port}/{ws_endpoint}"
                
                print(f'[OK] Profile started!')
                print(f'   WebSocket Path: {ws_endpoint}')
                print(f'   Port: {port}')
                print(f'   Full CDP URL: {cdp_url}')
                
                # Connect Playwright to Dolphin Anty
                print(f'\n🔗 Connecting Playwright to Dolphin Anty browser...')
                
                playwright = await async_playwright().start()
                browser = await playwright.chromium.connect_over_cdp(cdp_url)
                print('[OK] Playwright connected to Dolphin Anty browser!\n')
                progress.browser_connected()
                
                # Get or create context and page
                contexts = browser.contexts
                if contexts:
                    context = contexts[0]
                    pages = context.pages
                    if pages:
                        page = pages[0]
                    else:
                        page = await context.new_page()
                else:
                    context = await browser.new_context()
                    page = await context.new_page()
                
                # Instagram login automation
                print('='*50)
                print('📸 STARTING INSTAGRAM AUTOMATION (Playwright)')
                print('='*50 + '\n')
                
                first_target = target_users[0]
                progress.logging_in(instagram_username)
                await instagram_login(
                    page=page,
                    username=instagram_username,
                    password=instagram_password,
                    target_user=first_target
                )
                progress.login_success(instagram_username)
                
                print('\n' + '='*50)
                print('[OK] LOGIN PHASE COMPLETED!')
                print('='*50)
                
                # Process posts for each target user
                all_results = []
                
                for idx, target_user in enumerate(target_users, 1):
                    print('\n' + '='*50)
                    print(f'[PROFILE] PROCESSING TARGET {idx}/{len(target_users)}: @{target_user}')
                    print('='*50)
                
                    progress.navigating_to_profile(target_user)
                    user_logger = AutomationLogger()
                    
                    if mode == 'count':
                        post_result = await process_posts_by_count(
                            page=page,
                            target_user=target_user,
                            post_count=post_count,
                            comment_text=comment_text,
                            logger=user_logger
                        )
                    else:
                        post_result = await process_posts_after_date(
                            page=page,
                            target_user=target_user,
                            date_threshold=date_threshold,
                            comment_text=comment_text,
                            logger=user_logger
                        )
                    
                    print(f'\n[OK] COMPLETED @{target_user}')
                    user_logger.print_summary(stopped_early=post_result.get("stopped_early", False))
                    progress.profile_completed(target_user, post_result.get('posts_commented', 0))
                    
                    all_results.append({
                        "target_user": target_user,
                        "result": post_result
                    })
                    
                    if idx < len(target_users):
                        await do_profile_to_profile_delay(idx, logger)
                
                # Print summary for this account
                print('\n' + '='*50)
                print(f'[OK] ACCOUNT @{instagram_username} COMPLETED!')
                print('='*50)
                for result_data in all_results:
                    user = result_data['target_user']
                    result = result_data['result']
                    print(f'\n@{user}:')
                    print(f'  Posts Found: {result.get("posts_found", 0)}')
                    print(f'  Posts Processed: {result.get("posts_processed", 0)}')
                    print(f'  Posts Commented: {result.get("posts_commented", 0)}')
                    print(f'  Posts Skipped: {result.get("posts_skipped", 0)}')
                    print(f'  Stopped Early: {result.get("stopped_early", False)}')
                
                account_success = True
                
                if account_success:
                    print(f'\n[OK] Account @{instagram_username} completed successfully')
                else:
                    print(f'\n[WARN] Account @{instagram_username} had errors')
                    all_accounts_successful = False
                
            except Exception as e:
                print(f'\n[ERR] Account automation error for @{instagram_username}: {e}')
                
                # Check if it's a bot challenge error
                error_msg = str(e)
                if 'bot challenge' in error_msg.lower() or 'human verification' in error_msg.lower():
                    print(f'[ERR] Instagram bot challenge detected - marking campaign as failed')
                    progress.error(f'Instagram requires human verification for @{instagram_username}')
                    update_campaign_status(campaign_id, 'failed')
                    all_accounts_successful = False
                    # Break out of account loop - no point trying other accounts
                    break
                else:
                    progress.error(f'Error with @{instagram_username}: {str(e)[:100]}')
                
                account_success = False
                all_accounts_successful = False
                
            finally:
                print(f'\n[CLEANUP] Cleaning up browser resources for @{instagram_username}...')
                progress.cleanup()
                try:
                    if browser:
                        await browser.close()
                    if playwright:
                        await playwright.stop()
                except Exception as e:
                    print(f'[WARN] Error during cleanup: {e}')
                
                try:
                    if profile_id:
                        dolphin.stop_profile(profile_id)
                        print(f'[OK] Browser profile stopped')
                except Exception as e:
                    print(f'[WARN] Could not stop profile: {e}')
                
                # Delay before next account (if not the last one)
                if account_idx < len(user_accounts):
                    delay_time = random.uniform(10, 20)
                    print(f'\n[WAIT] Waiting {delay_time:.1f}s before starting next account...\n')
                    await asyncio.sleep(delay_time)
        
        # Update campaign status based on overall success
        campaign_success = all_accounts_successful
        
        if campaign_success:
            print(f'\n[OK] All {len(user_accounts)} account(s) completed successfully')
            campaign_results.append({
                'campaign_id': campaign_id,
                'status': 'completed'
            })
            # Update to completed
            print(f'\n[STATUS] Updating campaign status to: completed')
            update_campaign_status(campaign_id, 'completed')
            progress.campaign_completed(campaign_id, campaign_idx, len(campaigns))
        else:
            print(f'\n[WARN] Some accounts failed or encountered errors')
            # Check if already marked as failed (bot challenge)
            # If not, mark as failed now
            try:
                supabase = get_supabase_client()
                current_status = supabase.table('comment_campaigns').select('status').eq('campaign_id', campaign_id).execute()
                if current_status.data and current_status.data[0]['status'] != 'failed':
                    print(f'\n[STATUS] Updating campaign status to: failed')
                    update_campaign_status(campaign_id, 'failed')
                else:
                    print(f'\n[STATUS] Campaign already marked as failed')
            except:
                print(f'\n[STATUS] Updating campaign status to: failed')
                update_campaign_status(campaign_id, 'failed')
            
            campaign_results.append({
                'campaign_id': campaign_id,
                'status': 'failed'
            })
            progress.error(f'Campaign {campaign_id} failed - check logs for details')
        
        # Delay between campaigns
        if campaign_idx < len(campaigns):
            delay_time = random.uniform(30, 60)
            print(f'\n[WAIT] Waiting {delay_time:.1f}s before next campaign...\n')
            await asyncio.sleep(delay_time)
    
    # Print final summary
    print('\n' + '='*70)
    print('[OK] QUEUE PROCESSING COMPLETED!')
    print('='*70)
    
    completed = sum(1 for r in campaign_results if r.get('status') == 'completed')
    skipped = sum(1 for r in campaign_results if r.get('status') == 'skipped')
    failed = sum(1 for r in campaign_results if r.get('status') == 'failed')
    
    print(f'   Completed: {completed}')
    print(f'   Skipped: {skipped}')
    print(f'   Failed: {failed}')
    print('='*70)
    
    return campaign_results


def campaign_polling_worker():
    """
    Background worker that polls for new campaigns every 10 seconds.
    This is a fallback for when webhooks can't reach localhost (Supabase cloud limitation).
    """
    import time
    print('[POLLING] Campaign polling worker started (checks every 10s)')
    
    while True:
        try:
            time.sleep(10)  # Check every 10 seconds
            
            # Check if automation is already running
            if event_store.status == 'running':
                continue
            
            # Check for pending campaigns
            pending = get_next_campaigns()
            if pending and event_store.status != 'running':
                print(f'\n[POLLING] ✨ Detected {len(pending)} new campaign(s)! Auto-starting automation...')
                # Start automation in background thread
                thread = threading.Thread(target=run_automation_in_thread, daemon=True)
                thread.start()
        except Exception as e:
            print(f'[POLLING] Error checking for campaigns: {e}')
            # Continue polling even if there's an error


# ===========================================
# BACKGROUND WORKERS
# ===========================================

_workers_started = False

def start_background_workers():
    """Start background workers for production deployment"""
    global _workers_started
    if _workers_started:
        return  # Prevent double-start
    
    _workers_started = True
    polling_thread = threading.Thread(target=campaign_polling_worker, daemon=True)
    polling_thread.start()
    print('[WORKER] Background polling worker started')


# Auto-start workers when running under gunicorn or on Render
if IS_PRODUCTION:
    start_background_workers()


if __name__ == '__main__':
    # Check if running as API server or direct automation
    if len(sys.argv) > 1 and sys.argv[1] == 'api':
        # Run Flask API server
        print('[SERVER] Starting Instagram Comment Bot API Server...')
        print(f'[API] Documentation: http://localhost:{PORT}/api/docs')
        print(f'[API] Current Progress: http://localhost:{PORT}/api/progress/current')
        print(f'[API] Event Feed: http://localhost:{PORT}/api/progress/events')
        
        # Check for pending campaigns and auto-start if any exist
        # Only run this in the main process (not in Flask's reloader)
        if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
            # Start background polling worker for auto-detection
            start_background_workers()
            
            try:
                pending = get_next_campaigns()
                if pending:
                    print(f'[AUTO-START] Found {len(pending)} pending campaign(s), starting automation...')
                    # Start automation in background thread automatically
                    thread = threading.Thread(target=run_automation_in_thread, daemon=True)
                    thread.start()
                else:
                    print('[INFO] No pending campaigns found. Polling worker will auto-detect new campaigns...')
            except Exception as e:
                print(f'[WARN] Could not check for pending campaigns: {e}')
        
        app.run(debug=True, host='0.0.0.0', port=PORT, threaded=True)
    else:
        # Run automation directly
        print('Running automation directly (use "python instagramApp.py api" for API server)')
        asyncio.run(run_automation_with_dolphin_anty())