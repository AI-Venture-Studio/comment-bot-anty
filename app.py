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
from twitter import TwitterAutomation, TwitterSelectors, TweetReplyResult, stream_type_text
from instagram import InstagramAutomation, InstagramSelectors
from threads import ThreadsAutomation, ThreadsSelectors
from media_manager import (
    init_media_manager,
    verify_media_exists_in_storage,
    download_campaign_media,
    delete_local_campaign_dir,
    delete_campaign_media_from_storage,
)
import lock_manager

dotenv.load_dotenv()
init_media_manager()

# ===========================================
# ENVIRONMENT CONFIGURATION
# ===========================================

# Detect production environment
# Check for explicit PRODUCTION env var first (recommended for Windows VPS)
IS_PRODUCTION = (
    os.environ.get('PRODUCTION', '').lower() in ('true', '1', 'yes') or
    os.environ.get('RENDER') or 
    os.environ.get('GUNICORN_CMD_ARGS') or 
    'gunicorn' in os.environ.get('SERVER_SOFTWARE', '') or
    os.environ.get('WAITRESS_THREADS')  # For Windows-compatible WSGI server
)

# Get port from environment (Render provides PORT)
PORT = int(os.environ.get('PORT', 5001))

# CORS configuration - restrict in production
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '*').split(',')

# ===========================================
# FLASK API SETUP
# ===========================================


app = Flask(__name__)

# Configure CORS with proper settings for API
cors_config = {
    'origins': ALLOWED_ORIGINS if IS_PRODUCTION and ALLOWED_ORIGINS != ['*'] else '*',
    'methods': ['GET', 'POST', 'OPTIONS'],
    'allow_headers': ['Content-Type', 'Authorization'],
    'supports_credentials': True
}
CORS(app, **cors_config)

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
        "title": "Social Media Comment Bot API",
        "description": "Real-time progress streaming API for Instagram and X/Twitter automation campaigns",
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
    """Thread-safe in-memory event storage for current automation session.
    
    CHECKPOINT-BASED DESIGN:
    - Only stores FINAL outcomes (success or failure)
    - No retries, attempts, or transient states are exposed
    - Each checkpoint represents a completed decision
    """
    
    def __init__(self):
        self.events: List[Dict] = []
        self.checkpoints: List[Dict] = []  # Final outcome checkpoints only
        self.current_progress = 0
        self.status = 'idle'  # idle, running, completed, error
        self.lock = threading.Lock()
        self.latest_sentence = "Waiting to start..."
        self.abort_signal = False
        self.comment_count = 0  # Track total successful comments
        self.locked_accounts: list = []
        
    def add_checkpoint(self, event_type: str, status: str, message: str,
                       target: str = None, index: int = None, total: int = None):
        """
        Add a FINAL checkpoint event (success or failure only).
        
        Args:
            event_type: 'campaign' | 'target' | 'comment'
            status: 'success' | 'failure'
            message: Human-readable outcome message
            target: Target profile username (optional)
            index: Current index for counting events (optional)
            total: Total count for this action type (optional)
        """
        with self.lock:
            checkpoint = {
                'type': event_type,
                'status': status,
                'message': message,
                'target': target,
                'index': index,
                'total': total,
                'timestamp': datetime.now().isoformat()
            }
            
            self.checkpoints.append(checkpoint)
            self.latest_sentence = message
            
            # Update comment count on successful comment
            if event_type == 'comment' and status == 'success':
                self.comment_count += 1
            
            # Determine category for legacy compatibility
            category = 'success' if status == 'success' else 'error'
            
            # Print to console for debugging
            status_icon = '✓' if status == 'success' else '✗'
            print(f'[CHECKPOINT] {status_icon} [{event_type.upper()}] {message}')
            
            return checkpoint
    
    def add_event(self, sentence: str, category: str, progress: int = None, 
                  significant: bool = True):
        """
        Legacy event method - used for internal logging only.
        These events are NOT shown in the UI carousel.
        """
        with self.lock:
            event = {
                'sentence': sentence,
                'category': category,
                'progress': progress if progress is not None else self.current_progress,
                'timestamp': datetime.now().isoformat(),
                'significant': False  # Force non-significant for legacy events
            }
            
            if progress is not None:
                self.current_progress = min(100, max(0, progress))
            
            self.events.append(event)
            
            # Print to console for debugging (dimmed)
            print(f'[{category.upper()}] {sentence} ({self.current_progress}%)')
            
            return event
    
    def get_checkpoints(self, limit: int = None) -> List[Dict]:
        """Get checkpoint events for UI carousel (final outcomes only)"""
        with self.lock:
            checkpoints = self.checkpoints.copy()
            
            # Most recent first for carousel
            checkpoints.reverse()
            
            if limit:
                checkpoints = checkpoints[:limit]
            
            return checkpoints
    
    def get_events(self, significant_only: bool = False, limit: int = None, 
                   since_timestamp: str = None) -> List[Dict]:
        """Get legacy events - for backward compatibility only"""
        with self.lock:
            # Return checkpoints as legacy events for backward compatibility
            events = []
            for cp in self.checkpoints:
                events.append({
                    'sentence': cp['message'],
                    'category': 'success' if cp['status'] == 'success' else 'error',
                    'progress': self.current_progress,
                    'timestamp': cp['timestamp'],
                    'significant': True
                })
            
            # Most recent first
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
                'total_events': len(self.checkpoints),
                'comment_count': self.comment_count,
                'locked_accounts': list(self.locked_accounts),
            }
    
    def set_status(self, status: str):
        """Update automation status"""
        with self.lock:
            self.status = status
    
    def set_progress(self, progress: int):
        """Update progress percentage"""
        with self.lock:
            self.current_progress = min(100, max(0, progress))
    
    def set_abort(self):
        """Signal to abort the current automation"""
        with self.lock:
            self.abort_signal = True
    
    def is_aborted(self) -> bool:
        """Check if abort was requested"""
        with self.lock:
            return self.abort_signal
    
    def clear(self):
        """Clear all events (new session)"""
        with self.lock:
            self.events.clear()
            self.checkpoints.clear()
            self.current_progress = 0
            self.status = 'idle'
            self.latest_sentence = "Waiting to start..."
            self.abort_signal = False
            self.comment_count = 0
            self.locked_accounts = []

# Global event store instance
event_store = EventStore()


# ===========================================
# PROGRESS EMITTER (CHECKPOINT-BASED)
# ===========================================

class ProgressEmitter:
    """
    Singleton class to emit progress checkpoints during automation.
    
    CHECKPOINT-BASED DESIGN:
    - Only emits FINAL outcomes (success or failure)
    - Retries and internal steps are hidden from UI
    - Each method represents a completed action
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    # =========================================
    # INTERNAL LOGGING (not shown in UI)
    # =========================================
    
    def _log(self, message: str, category: str = 'action'):
        """Internal logging - NOT shown in carousel"""
        event_store.add_event(message, category, significant=False)
    
    # =========================================
    # CAMPAIGN CHECKPOINTS
    # =========================================
    
    def campaign_starting(self):
        """Checkpoint: Campaign is starting"""
        event_store.add_checkpoint(
            event_type='campaign',
            status='success',
            message='Starting campaign'
        )
    
    def campaign_completed(self):
        """Checkpoint: Campaign completed successfully"""
        event_store.set_progress(100)
        event_store.add_checkpoint(
            event_type='campaign',
            status='success',
            message='Campaign completed successfully'
        )
    
    def campaign_failed(self, reason: str = None):
        """Checkpoint: Campaign failed"""
        message = f'Campaign failed: {reason}' if reason else 'Campaign failed'
        event_store.add_checkpoint(
            event_type='campaign',
            status='failure',
            message=message
        )
    
    def campaign_aborted(self):
        """Checkpoint: Campaign was aborted by user"""
        event_store.set_status('aborted')
        event_store.add_checkpoint(
            event_type='campaign',
            status='failure',
            message='Campaign aborted by user'
        )
    
    # =========================================
    # LOGIN CHECKPOINTS
    # =========================================
    
    def login_success(self, username: str):
        """Checkpoint: Login successful"""
        event_store.add_checkpoint(
            event_type='campaign',
            status='success',
            message=f'Login successful (@{username})'
        )
    
    def login_failed(self, username: str, reason: str = None):
        """Checkpoint: Login failed"""
        message = f'Login failed (@{username})'
        if reason:
            message += f': {reason}'
        event_store.add_checkpoint(
            event_type='campaign',
            status='failure',
            message=message
        )
    
    # =========================================
    # TARGET PROFILE CHECKPOINTS
    # =========================================
    
    def target_opened(self, target_user: str):
        """Checkpoint: Target profile opened successfully"""
        event_store.add_checkpoint(
            event_type='target',
            status='success',
            message=f'Target profile opened (@{target_user})',
            target=target_user
        )
    
    def target_failed(self, target_user: str, reason: str = None):
        """Checkpoint: Failed to open target profile"""
        message = f'Failed to open profile (@{target_user})'
        if reason:
            message += f': {reason}'
        event_store.add_checkpoint(
            event_type='target',
            status='failure',
            message=message,
            target=target_user
        )
    
    def posts_scanned(self, target_user: str, count: int):
        """Checkpoint: Posts scanned successfully"""
        event_store.add_checkpoint(
            event_type='target',
            status='success',
            message=f'Posts scanned ({count} found) @{target_user}',
            target=target_user,
            total=count
        )
    
    def posts_scan_failed(self, target_user: str, reason: str = None):
        """Checkpoint: Failed to scan posts"""
        message = f'Failed to scan posts (@{target_user})'
        if reason:
            message += f': {reason}'
        event_store.add_checkpoint(
            event_type='target',
            status='failure',
            message=message,
            target=target_user
        )
    
    def target_completed(self, target_user: str, comments_posted: int):
        """Checkpoint: Finished processing target profile"""
        event_store.add_checkpoint(
            event_type='target',
            status='success',
            message=f'Finished @{target_user} ({comments_posted} comments)',
            target=target_user,
            total=comments_posted
        )
    
    def target_profile_failed(self, target_user: str, reason: str = None):
        """Checkpoint: Target profile processing failed"""
        message = f'Target profile failed (@{target_user})'
        if reason:
            message += f': {reason}'
        event_store.add_checkpoint(
            event_type='target',
            status='failure',
            message=message,
            target=target_user
        )
    
    # =========================================
    # COMMENT CHECKPOINTS
    # =========================================
    
    def comment_posted(self, target_user: str, index: int, total: int):
        """Checkpoint: Comment posted successfully"""
        event_store.add_checkpoint(
            event_type='comment',
            status='success',
            message=f'Comment posted ({index}/{total}) @{target_user}',
            target=target_user,
            index=index,
            total=total
        )
    
    def comment_failed(self, target_user: str, index: int, total: int, reason: str = None):
        """Checkpoint: Comment failed"""
        message = f'Comment failed ({index}/{total}) @{target_user}'
        if reason:
            message += f': {reason}'
        event_store.add_checkpoint(
            event_type='comment',
            status='failure',
            message=message,
            target=target_user,
            index=index,
            total=total
        )
    
    # =========================================
    # LEGACY METHODS (for internal logging only)
    # These do NOT show in the UI carousel
    # =========================================
    
    def navigation(self, message: str, progress: Optional[int] = None):
        self._log(message, 'navigation')
    
    def action(self, message: str, progress: Optional[int] = None, significant: bool = False):
        self._log(message, 'action')
    
    def success(self, message: str, progress: Optional[int] = None):
        self._log(message, 'success')
    
    def warning(self, message: str, progress: Optional[int] = None):
        self._log(message, 'warning')
    
    def error(self, message: str, progress: Optional[int] = None):
        self._log(message, 'error')
    
    def info(self, message: str, progress: Optional[int] = None, significant: bool = False):
        self._log(message, 'action')
    
    # Legacy specific events - now just log internally
    def campaign_started(self, campaign_id: str, index: int, total: int):
        self._log(f'Processing campaign {index} of {total}', 'action')
    
    def browser_launching(self):
        self._log('Launching browser profile', 'action')
    
    def browser_connected(self):
        self._log('Browser connected', 'action')
    
    def logging_in(self, username: str):
        self._log(f'Attempting login for @{username}', 'action')
    
    def navigating_to_profile(self, target_user: str):
        self._log(f'Navigating to @{target_user}', 'navigation')
    
    def scanning_posts(self, target_user: str):
        self._log(f'Scanning posts from @{target_user}', 'action')
    
    def post_found(self, count: int):
        self._log(f'Found {count} posts', 'action')
    
    def commenting_on_post(self, post_num: int, total: int, target_user: str):
        self._log(f'Processing post {post_num}/{total}', 'action')
    
    def comment_submitted(self, post_num: int, total: int, comment: str):
        # This is now handled by comment_posted checkpoint
        self._log(f'Comment submitted on post {post_num}', 'success')
    
    def post_skipped(self, reason: str):
        self._log(f'Post skipped: {reason}', 'warning')
    
    def profile_completed(self, target_user: str, commented: int):
        # This is now handled by target_completed checkpoint
        self._log(f'Finished @{target_user}', 'success')
    
    def taking_break(self, duration: int):
        self._log(f'Pausing for {duration}s', 'action')
    
    def cleanup(self):
        self._log('Cleaning up resources', 'action')

# Global singleton instance
progress = ProgressEmitter()


# ===========================================
# FLASK API ENDPOINTS
# ===========================================

@app.route('/', methods=['GET'])
def index():
    """
    API Root
    ---
    tags:
      - Health
    summary: API welcome message
    description: Returns welcome message and available endpoints
    responses:
      200:
        description: Welcome message with API information
        schema:
          type: object
          properties:
            message:
              type: string
              example: Welcome to Social Media Comment Bot API
            version:
              type: string
              example: 1.0.0
            endpoints:
              type: object
              properties:
                docs:
                  type: string
                  example: /api/docs
                health:
                  type: string
                  example: /health
    """
    return jsonify({
        'message': 'Social Media Comment Bot API by AIVS',
        'version': '4.0.0 - Twitter image Support',
        'status': 'running'
    })


@app.route('/health', methods=['GET'])
def health_check():
    """
    Health Check
    ---
    tags:
      - Health
    summary: Check API health status
    description: Returns the health status of the API and its dependencies
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
              example: social-media-comment-bot
            environment:
              type: string
              example: production
            workers_started:
              type: boolean
              example: true
    """
    # Check Dolphin Anty connection
    dolphin_status = 'unknown'
    try:
        dolphin = DolphinAntyClient()
        dolphin_status = 'connected' if dolphin.login(show_progress=False) else 'disconnected'
    except:
        dolphin_status = 'error'
    
    # Check Supabase connection
    supabase_status = 'unknown'
    try:
        client = get_supabase_client()
        supabase_status = 'connected'
    except:
        supabase_status = 'error'
    
    return jsonify({
        'status': 'healthy',
        'service': 'social-media-comment-bot',
        'environment': 'production' if IS_PRODUCTION else 'development',
        'automation_status': event_store.status,
        'dependencies': {
            'dolphin_anty': dolphin_status,
            'supabase': supabase_status
        },
        'timestamp': datetime.now().isoformat()
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
            campaign_info:
              type: object
              description: Active campaign metadata (if available)
              properties:
                campaign_id:
                  type: string
                platform:
                  type: string
                user_accounts:
                  type: array
                  items:
                    type: string
                target_profiles:
                  type: array
                  items:
                    type: string
                custom_comment:
                  type: string
    """
    state = event_store.get_current_state()
    
    # If EventStore shows idle/completed but there's an active campaign in DB,
    # fetch and include that campaign's metadata for UI persistence
    if state['status'] in ['idle', 'completed'] or state['total_events'] == 0:
        active_campaign = get_active_campaign_from_db()
        if active_campaign:
            state['status'] = 'running'
            state['campaign_info'] = active_campaign
            if state['latest_sentence'] == 'Waiting to start...':
                state['latest_sentence'] = f"Campaign {active_campaign.get('campaign_id', 'running')} in progress..."
    
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


@app.route('/api/progress/checkpoints', methods=['GET'])
def get_checkpoints():
    """
    Get Checkpoint Feed for Carousel
    ---
    tags:
      - Progress
    summary: Fetch final outcome checkpoints for UI carousel
    description: |
      Returns only FINAL outcomes (success or failure) for display in the carousel.
      No retries, attempts, or transient states are included.
      Each checkpoint represents a completed decision.
    parameters:
      - in: query
        name: limit
        type: integer
        required: false
        description: Maximum number of checkpoints to return
        example: 10
    responses:
      200:
        description: Checkpoint feed
        schema:
          type: object
          properties:
            checkpoints:
              type: array
              items:
                type: object
                properties:
                  type:
                    type: string
                    enum: [campaign, target, comment]
                    example: comment
                  status:
                    type: string
                    enum: [success, failure]
                    example: success
                  message:
                    type: string
                    example: "Comment posted (1/2) @jakepaul"
                  target:
                    type: string
                    example: jakepaul
                  index:
                    type: integer
                    example: 1
                  total:
                    type: integer
                    example: 2
                  timestamp:
                    type: string
                    format: date-time
            total:
              type: integer
              example: 5
            comment_count:
              type: integer
              example: 3
              description: Total successful comments posted
            status:
              type: string
              example: running
    """
    limit = request.args.get('limit', type=int, default=10)
    
    checkpoints = event_store.get_checkpoints(limit=limit)
    state = event_store.get_current_state()
    
    return jsonify({
        'checkpoints': checkpoints,
        'total': len(checkpoints),
        'comment_count': state['comment_count'],
        'status': event_store.status
    })


def run_automation_in_thread(campaign_id: str = None):
    """Run automation in background thread for a specific campaign."""
    try:
        event_store.clear()
        event_store.set_status('running')
        
        # Emit campaign starting checkpoint
        progress.campaign_starting()
        
        # Run the async automation for the specified campaign
        asyncio.run(run_automation_with_dolphin_anty(campaign_id=campaign_id))
        
        # Check if aborted
        if event_store.is_aborted():
            event_store.set_status('aborted')
            progress.campaign_aborted()
        else:
            event_store.set_status('completed')
            progress.campaign_completed()
        
    except Exception as e:
        event_store.set_status('error')
        progress.campaign_failed(str(e))


@app.route('/api/start', methods=['POST'])
def start_automation():
    """
    Start Automation
    ---
    tags:
      - Progress
    summary: Start the automation process
    description: Initiates the social media comment bot automation in a background thread (supports Instagram and X/Twitter)
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
    
    # Read the campaign_id from the request body
    data = request.get_json(silent=True) or {}
    campaign_id = data.get('campaign_id')
    
    if not campaign_id:
        return jsonify({'error': 'campaign_id is required'}), 400
    
    # Start automation in background thread for the specific campaign
    thread = threading.Thread(
        target=run_automation_in_thread,
        args=(campaign_id,),
        daemon=True,
    )
    thread.start()
    
    return jsonify({
        'status': 'started',
        'message': f'Campaign {campaign_id} started successfully'
    })


@app.route('/api/abort', methods=['POST'])
def abort_automation():
    """
    Abort Automation
    ---
    tags:
      - Progress
    summary: Abort the running automation
    description: Signals the automation to stop gracefully and marks campaigns as aborted
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            campaign_id:
              type: string
              description: The campaign ID to abort
    responses:
      200:
        description: Abort signal sent successfully
        schema:
          type: object
          properties:
            status:
              type: string
              example: aborting
            message:
              type: string
              example: Abort signal sent
      400:
        description: No automation running
    """
    # Get campaign_id from request first (before status check to avoid race condition)
    data = request.get_json() or {}
    campaign_id = data.get('campaign_id')
    
    # Capture current status atomically
    current_status = event_store.status
    
    if current_status == 'running':
        # Set abort signal
        event_store.set_abort()
        
        if campaign_id:
            progress.warning(f'Abort requested for campaign {campaign_id} - stopping gracefully')
            print(f'\n[ABORT] User requested abort for campaign: {campaign_id}')
            # Update database immediately
            try:
                update_campaign_status(campaign_id, 'aborted')
            except Exception as e:
                print(f'[WARN] Could not update campaign status in DB: {e}')
        else:
            progress.warning('Abort requested - stopping automation gracefully')
            print('\n[ABORT] User requested abort')
        
        return jsonify({
            'status': 'aborting',
            'message': 'Abort signal sent - automation will stop gracefully'
        })
    
    elif current_status in ['completed', 'idle', 'error', 'aborted']:
        return jsonify({
            'status': 'not_running',
            'message': f'No automation is currently running (status: {current_status})'
        }), 200  # Return 200 since this isn't an error
    
    else:
        return jsonify({
            'status': 'unknown',
            'message': f'Unexpected status: {current_status}'
        }), 400


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
        
        # Acknowledge all webhook events - automation is started manually via /api/start
        print('[WEBHOOK] Acknowledged. Automation must be started manually via /api/start.')
        return jsonify({
            'status': 'received',
            'automation_started': False,
            'message': f'Webhook acknowledged: {webhook_type} — use /api/start to run automation'
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


async def do_post_to_post_delay(base_delay: float = None, logger=None):
    """
    Natural delay between processing posts with Gaussian jitter.
    
    Args:
        base_delay: User-configured base delay (seconds). If None, uses default.
        logger: Optional logger instance
    """
    if base_delay is None:
        base_delay = POST_TO_POST_DELAY_DEFAULT
    
    # Clamp base_delay to allowed range
    base_delay = max(POST_TO_POST_DELAY_MIN, min(POST_TO_POST_DELAY_MAX, base_delay))
    
    # Apply ±20% Gaussian jitter
    jitter_range = base_delay * 0.2
    min_delay = base_delay - jitter_range
    max_delay = base_delay + jitter_range
    
    # Ensure we stay within absolute bounds
    min_delay = max(POST_TO_POST_DELAY_MIN, min_delay)
    max_delay = min(POST_TO_POST_DELAY_MAX, max_delay)
    
    delay = get_random_delay(min_delay, max_delay)
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
# HUMAN-LIKE BEHAVIOR SETTINGS (LOCKED)
# ===========================================
# These constants are locked for safety and cannot be changed by users

# Typing delays (milliseconds) - realistic human typing speed
IG_TYPING_DELAY_MIN = 220  # minimum delay between keystrokes (LOCKED)
IG_TYPING_DELAY_MAX = 320  # maximum delay between keystrokes (LOCKED)

# Word and punctuation pauses (seconds)
WORD_PAUSE_MIN = 0.4  # pause between words (LOCKED)
WORD_PAUSE_MAX = 1.2  # (LOCKED)
PUNCTUATION_PAUSE_MIN = 0.8  # pause after punctuation/sentences (LOCKED)
PUNCTUATION_PAUSE_MAX = 2.5  # (LOCKED)

# Pre-typing and post-typing pauses (seconds)
PRE_TYPING_HESITATION_MIN = 0.8  # hesitation before starting to type (LOCKED)
PRE_TYPING_HESITATION_MAX = 2.0  # (LOCKED)
REVIEW_PAUSE_MIN = 2.5  # pause after typing to "review" before posting (LOCKED)
REVIEW_PAUSE_MAX = 6.0  # (LOCKED)

# Typo simulation
TYPO_CHANCE = 0.07  # 7% chance of making a typo per word (LOCKED)
TYPO_CORRECTION_DELAY_MIN = 0.3  # delay before noticing and correcting typo (LOCKED)
TYPO_CORRECTION_DELAY_MAX = 0.8  # (LOCKED)

# Action delays (seconds) - LOCKED except post-to-post which is user-configurable
POST_TO_POST_DELAY_MIN = 8  # User-configurable range minimum (LOCKED)
POST_TO_POST_DELAY_MAX = 20  # User-configurable range maximum (LOCKED)
POST_TO_POST_DELAY_DEFAULT = 15  # Default value if not specified by user
PROFILE_TO_PROFILE_DELAY_MIN = 25  # delay between profiles (LOCKED)
PROFILE_TO_PROFILE_DELAY_MAX = 60  # (LOCKED)
LONG_PAUSE_MIN = 180  # occasional long break: 3 minutes (LOCKED)
LONG_PAUSE_MAX = 300  # 5 minutes (LOCKED)
LONG_PAUSE_FREQUENCY = 6  # long pause every 6 profiles (LOCKED)

# Mouse movement settings
MOUSE_OVERSHOOT_CHANCE = 0.3  # 30% chance of overshooting target (LOCKED)
MOUSE_MOVEMENT_STEPS_MIN = 12  # minimum steps for curved movement (LOCKED)
MOUSE_MOVEMENT_STEPS_MAX = 30  # maximum steps for curved movement (LOCKED)
MOUSE_STEP_DELAY_MIN = 0.6  # delay between movement steps (seconds) (LOCKED)
MOUSE_STEP_DELAY_MAX = 1.6  # (LOCKED)
MOUSE_PRE_CLICK_PAUSE_MIN = 0.1  # pause before clicking (LOCKED)
MOUSE_PRE_CLICK_PAUSE_MAX = 0.4  # (LOCKED)

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
    """Manages browser cookies per social account (platform + username)"""
    
    def __init__(self, cookies_dir: str = "cookies"):
        self.cookies_dir = Path(cookies_dir)
        self.cookies_dir.mkdir(exist_ok=True)
    
    def _get_cookie_file(self, username: str, platform: str = "instagram") -> Path:
        """Get the cookie file path for a specific platform + username
        
        Args:
            username: Account username
            platform: Social media platform (instagram, x, tiktok, etc.)
        """
        # Sanitize username and platform for filename
        safe_username = "".join(c for c in username if c.isalnum() or c in "_-")
        safe_platform = "".join(c for c in platform if c.isalnum() or c in "_-")
        return self.cookies_dir / f"{safe_platform}_{safe_username}_cookies.json"
    
    def save_cookies(self, username: str, cookies: list, platform: str = "instagram") -> bool:
        """
        Save cookies for a specific social account.
        
        Args:
            username: Account username
            cookies: List of cookie dictionaries from browser
            platform: Social media platform (instagram, x, etc.)
        """
        try:
            cookie_file = self._get_cookie_file(username, platform)
            cookie_data = {
                "username": username,
                "platform": platform,
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
    
    def load_cookies(self, username: str, platform: str = "instagram") -> list | None:
        """
        Load cookies for a specific social account.
        Verifies the cookies belong to the requested username and platform.
        
        Args:
            username: Account username to load cookies for
            platform: Social media platform (instagram, x, etc.)
            
        Returns:
            List of cookies if found and valid, None otherwise
        """
        try:
            cookie_file = self._get_cookie_file(username, platform)
            if not cookie_file.exists():
                # No cookies - silent, this is expected for first login
                return None
            
            with open(cookie_file, 'r') as f:
                cookie_data = json.load(f)
            
            # Verify the cookies belong to the correct account and platform
            stored_username = cookie_data.get("username", "")
            stored_platform = cookie_data.get("platform", "instagram")
            
            if stored_username.lower() != username.lower():
                progress.warning(f'Session cookies belong to different account, clearing')
                self.delete_cookies(username, platform)
                return None
            
            if stored_platform.lower() != platform.lower():
                progress.warning(f'Session cookies belong to different platform, clearing')
                self.delete_cookies(username, platform)
                return None
            
            cookies = cookie_data.get("cookies", [])
            if not cookies:
                # Empty cookies - silent cleanup
                return None
            
            # Cookie load successful - silent
            return cookies
            
        except json.JSONDecodeError:
            progress.warning('Session cookies corrupted, clearing')
            self.delete_cookies(username, platform)
            return None
        except Exception as e:
            # Silent failure on cookie load
            return None
    
    def delete_cookies(self, username: str, platform: str = "instagram") -> bool:
        """Delete saved cookies for a specific account
        
        Args:
            username: Account username
            platform: Social media platform
        """
        try:
            cookie_file = self._get_cookie_file(username, platform)
            if cookie_file.exists():
                cookie_file.unlink()
                # Silent cookie deletion
            return True
        except Exception as e:
            # Silent failure
            return False
    
    def has_cookies(self, username: str, platform: str = "instagram") -> bool:
        """Check if cookies exist for an account
        
        Args:
            username: Account username
            platform: Social media platform
        """
        cookie_file = self._get_cookie_file(username, platform)
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
                    
                    # Detect Windows file lock errors - these won't resolve with retries
                    file_lock_keywords = ['EBUSY', 'resource busy', 'locked', 'UNKNOWN: unknown error, open']
                    is_file_lock_error = any(keyword in error_details for keyword in file_lock_keywords)
                    
                    if is_file_lock_error:
                        print(f'[ERR] Windows file lock detected on profile {profile_id}!')
                        print(f'[ERR] The browser profile has corrupted/locked files from a previous crash.')
                        print(f'[ERR] FIX: Run this on Windows server:')
                        print(f'[ERR]   Remove-Item -Recurse -Force "C:\\Users\\Administrator\\AppData\\Roaming\\dolphin_anty\\browser_profiles\\{profile_id}\\data_dir\\Default"')
                        print(f'[ERR] Or assign a different browser profile to this account.')
                        # Don't retry - file locks won't clear with simple retries
                        return None
                    
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


# ===========================================
# INSTAGRAM AUTOMATION (imported from instagram.py)
# ===========================================
# InstagramAutomation and InstagramSelectors are imported from instagram.py
# See instagram.py for:
#   - InstagramSelectors class (CSS/XPath selectors)
#   - InstagramAutomation class:
#       - login() - login verification and authentication
#       - process_posts_by_count() - process fixed number of posts
#       - process_posts_after_date() - process posts after a date threshold
#       - comment_on_post() - add comment to a post
#       - detect_bot_challenge() - detect Instagram bot challenges
#       - and other Instagram-specific helper functions


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
            # Only print if no automation is currently running (reduces log noise)
            if event_store.status != 'running':
                print('[DB] No pending campaigns found')
            return []
    
    except Exception as e:
        print(f'[ERR] Could not load campaigns from database: {e}')
        return []


def get_campaign_by_id(campaign_id: str):
    """
    Get a single campaign by its campaign_id.
    
    Only returns the campaign if its status is 'not-started' (guards against
    double-runs).  Returns a single-item list so the caller's for-loop
    requires zero changes.
    
    Args:
        campaign_id: The campaign_id to look up.
    
    Returns:
        Single-item list with the campaign dict, or empty list if not found
        or not in 'not-started' status.
    """
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table('comment_campaigns')
            .select('*')
            .eq('campaign_id', campaign_id)
            .eq('status', 'not-started')
            .limit(1)
            .execute()
        )
        
        if response.data:
            print(f'[DB] Found campaign {campaign_id} (status: not-started)')
            return response.data
        else:
            print(f'[DB] Campaign {campaign_id} not found or not in not-started status')
            return []
    
    except Exception as e:
        print(f'[ERR] Could not load campaign {campaign_id} from database: {e}')
        return []


def update_campaign_status(campaign_id: str, status: str):
    """
    Update campaign status in database.
    
    Args:
        campaign_id: Campaign identifier
        status: New status ('not-started', 'in-progress', 'completed', 'failed', 'aborted')
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


def get_active_campaign_from_db():
    """
    Retrieve currently active (in-progress) campaign from database.
    Used to restore progress UI state after page refresh.
    
    Returns:
        Dict with campaign data or None if no active campaign
    """
    try:
        supabase = get_supabase_client()
        response = supabase.table('comment_campaigns').select(
            'campaign_id,platform,user_accounts,target_profiles,custom_comment,status,created_at'
        ).eq('status', 'in-progress').limit(1).execute()
        
        if response.data and len(response.data) > 0:
            campaign = response.data[0]
            return {
                'campaign_id': campaign.get('campaign_id'),
                'platform': campaign.get('platform', 'instagram'),
                'user_accounts': campaign.get('user_accounts', []),
                'target_profiles': campaign.get('target_profiles', []),
                'custom_comment': campaign.get('custom_comment', ''),
                'created_at': campaign.get('created_at')
            }
        return None
    except Exception as e:
        print(f'[ERR] Could not get active campaign from database: {e}')
        return None


def deactivate_account(username: str, platform: str = 'instagram'):
    """
    Deactivate a social account when it's suspended or flagged.
    
    Args:
        username: Account username
        platform: Social media platform (default: 'instagram')
    """
    try:
        supabase = get_supabase_client()
        
        # Try both with and without @ symbol to handle different storage formats
        clean_username = username.lstrip('@')
        usernames_to_try = [clean_username, f'@{clean_username}']
        
        print(f'[DEBUG] Attempting to deactivate account: usernames={usernames_to_try}, platform={platform}')
        
        response = supabase.table('social_accounts').update({
            'is_active': False,
            'updated_at': datetime.now().isoformat()
        }).in_('username', usernames_to_try).eq('platform', platform).execute()
        
        if response.data and len(response.data) > 0:
            updated_username = response.data[0].get('username', username)
            print(f'[DB] ✓ Successfully deactivated account @{updated_username} on {platform}')
        else:
            print(f'[WARN] Deactivate query returned no data for @{username} on {platform}')
    except Exception as e:
        print(f'[ERR] Could not deactivate account @{username}: {e}')


def reactivate_account(username: str, platform: str = 'instagram'):
    """
    Reactivate a social account after it successfully passes login/bot check.
    
    This is called when an inactive account successfully logs in,
    indicating the account has been manually resolved.
    
    Args:
        username: Account username
        platform: Social media platform (default: 'instagram')
    """
    try:
        supabase = get_supabase_client()
        
        # Try both with and without @ symbol to handle different storage formats
        clean_username = username.lstrip('@')
        usernames_to_try = [clean_username, f'@{clean_username}']
        
        print(f'[DEBUG] Attempting to reactivate account: usernames={usernames_to_try}, platform={platform}')
        
        # Update using IN clause to match either format
        response = supabase.table('social_accounts').update({
            'is_active': True,
            'updated_at': datetime.now().isoformat()
        }).in_('username', usernames_to_try).eq('platform', platform).execute()
        
        # Check if update was successful
        if response.data and len(response.data) > 0:
            updated_username = response.data[0].get('username', username)
            print(f'[DB] ✓ Successfully reactivated account @{updated_username} on {platform}')
            print(f'[DEBUG] Updated record: {response.data[0]}')
        else:
            print(f'[WARN] Reactivate query returned no data for @{username} on {platform}')
            print(f'[DEBUG] Tried usernames: {usernames_to_try}')
    except Exception as e:
        print(f'[ERR] Could not reactivate account @{username}: {e}')


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
        
        # Try both with and without @ symbol to handle different storage formats
        usernames_to_try = []
        if username:
            clean_username = username.lstrip('@')
            usernames_to_try = [clean_username, f'@{clean_username}']
        
        print(f'[DEBUG] Looking up credentials: usernames={usernames_to_try}, platform="{platform}"')
        
        # Try to find account with either username format
        # Note: We don't filter by is_active here - inactive accounts should still be tried
        # If the account encounters a roadblock during login, it will be skipped at that point
        response = supabase.table('social_accounts').select('username,password,browser_profile,is_active').in_('username', usernames_to_try).eq('platform', platform).limit(1).execute()
        
        if response.data and len(response.data) > 0:
            found_username = response.data[0].get('username')
            is_active = response.data[0].get('is_active', True)
            status_str = '✓' if is_active else '⚠️ (inactive)'
            print(f'[DEBUG] {status_str} Found credentials for "{found_username}" on {platform}')
            return response.data[0]
        else:
            print(f'[WARN] No credentials found for {username} on {platform}')
            print(f'[DEBUG] Tried usernames: {usernames_to_try}')
            print(f'[DEBUG] Query returned {len(response.data) if response.data else 0} results')
            return None
    except Exception as e:
        print(f'[ERR] Could not get account credentials: {e}')
        return None


def validate_accounts_status(usernames: list, platform: str = 'instagram') -> dict:
    """
    Validate that all accounts are active before starting campaign.
    
    Args:
        usernames: List of account usernames to validate
        platform: Social media platform (default: 'instagram')
        
    Returns:
        Dict with 'valid' (bool) and 'inactive_accounts' (list) keys
    """
    try:
        supabase = get_supabase_client()
        response = supabase.table('social_accounts').select('username,is_active').eq('platform', platform).in_('username', usernames).execute()
        
        if not response.data:
            return {'valid': False, 'inactive_accounts': usernames}
        
        inactive_accounts = [acc['username'] for acc in response.data if not acc['is_active']]
        
        return {
            'valid': len(inactive_accounts) == 0,
            'inactive_accounts': inactive_accounts
        }
    except Exception as e:
        print(f'[ERR] Could not validate accounts status: {e}')
        return {'valid': False, 'inactive_accounts': []}


def get_platform_browser_profiles(platform: str) -> list:
    """
    Get browser profiles assigned to active accounts for a specific platform.
    
    This ensures proper separation between platforms - X campaigns only see
    browser profiles with X accounts, Instagram campaigns only see Instagram profiles, etc.
    
    Args:
        platform: Social media platform ('instagram', 'x', 'tiktok', etc.)
        
    Returns:
        List of dicts with 'username' and 'browser_profile' for the platform
    """
    try:
        supabase = get_supabase_client()
        # Note: We don't filter by is_active - inactive accounts should still be available
        # The campaign will attempt to use them and skip if they encounter roadblocks
        response = supabase.table('social_accounts').select(
            'username,browser_profile'
        ).eq('platform', platform).execute()
        
        if response.data:
            # Filter out accounts without browser profiles
            profiles = [
                acc for acc in response.data 
                if acc.get('browser_profile')
            ]
            return profiles
        return []
    except Exception as e:
        print(f'[ERR] Could not get browser profiles for platform {platform}: {e}')
        return []


def get_platform_account_count(platform: str) -> int:
    """
    Get count of active accounts with browser profiles for a specific platform.
    
    Args:
        platform: Social media platform
        
    Returns:
        Count of active accounts with assigned browser profiles
    """
    profiles = get_platform_browser_profiles(platform)
    return len(profiles)


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
    4. Account credentials exist in database (platform-specific)
    5. Dolphin Anty is reachable
    6. Browser profiles exist for the platform
    7. Assigned browser profile exists in Dolphin Anty
    
    Args:
        campaign: Campaign dictionary from database
        
    Returns:
        PreFlightCheckResult with success status and error messages
    """
    errors = []
    
    # Check 1: Required campaign fields
    required_fields = ['campaign_id', 'user_accounts', 'target_profiles', 'platform']
    for field in required_fields:
        if field not in campaign or not campaign[field]:
            errors.append(f'Missing required field: {field}')

    if errors:
        return PreFlightCheckResult(False, "Campaign missing required configuration", errors)

    # A campaign must have either comment text OR media attachments (or both)
    has_comment = bool(campaign.get('custom_comment', ''))
    has_media = bool(campaign.get('media_attachments'))
    if not has_comment and not has_media:
        errors.append('Campaign must have either comment text or media attachments')
        return PreFlightCheckResult(False, "No content: provide custom_comment, media_attachments, or both", errors)
    
    # Extract platform early for platform-specific checks
    platform = campaign.get('platform', 'instagram')
    platform_label = 'X/Twitter' if platform == 'x' else platform.capitalize()
    
    # Check 2: User accounts specified
    user_accounts = campaign.get('user_accounts', [])
    if not user_accounts or len(user_accounts) == 0:
        errors.append(f'No {platform_label} accounts specified in campaign')
        return PreFlightCheckResult(False, f"No {platform_label} accounts configured", errors)
    
    # Check 3: Target profiles specified
    target_profiles = campaign.get('target_profiles', [])
    if not target_profiles or len(target_profiles) == 0:
        errors.append('No target profiles specified in campaign')
        return PreFlightCheckResult(False, "No target profiles configured", errors)
    
    # Check 4: Account credentials exist (platform-specific)
    account_username = user_accounts[0]
    
    credentials = get_account_credentials(account_username, platform)
    if not credentials:
        errors.append(f'{platform_label} account credentials not found: @{account_username}')
        return PreFlightCheckResult(False, f"{platform_label} account credentials missing for @{account_username}", errors)
    
    # Check 4b: Browser profile is assigned to this platform account
    browser_profile_name = credentials.get('browser_profile', '')
    if not browser_profile_name:
        errors.append(f'No browser profile assigned to {platform_label} account @{account_username}')
        return PreFlightCheckResult(False, f"Browser profile not assigned to @{account_username}", errors)

    # Check 4c: Accounts not locked by another bot
    locked = lock_manager.check_locked_accounts(user_accounts, platform)
    if locked:
        names = ", ".join(f"@{u} (held by {owner.split(':')[0]})" for u, owner in locked.items())
        errors.append(f"Accounts in use by another bot: {names}")
        return PreFlightCheckResult(False, "Accounts currently in use by another bot", errors)

    # Check 5: Dolphin Anty connection
    dolphin = DolphinAntyClient()
    
    if not dolphin.login():
        errors.append('Cannot connect to Dolphin Anty - browser service not running')
        return PreFlightCheckResult(False, "Anti-detect browser unreachable", errors)
    
    # Check 6: Get platform-specific browser profiles (for logging/info)
    platform_profiles = get_platform_browser_profiles(platform)
    platform_profile_count = len(platform_profiles)
    print(f'[CHECK] Found {platform_profile_count} browser profile(s) for {platform_label}')
    for p in platform_profiles:
        print(f'  - @{p.get("username")} -> {p.get("browser_profile")}')
    
    if platform_profile_count == 0:
        errors.append(f'No browser profiles configured for {platform_label} accounts')
        return PreFlightCheckResult(False, f"No {platform_label} browser profiles available", errors)
    
    # Check 7: Assigned browser profile exists in Dolphin Anty
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
        message=f"All checks passed - ready to run {platform_label} campaign {campaign.get('campaign_id')}"
    )


async def run_automation_with_dolphin_anty(campaign_id: str = None):
    """
    Main function to run Playwright automation using Dolphin Anty's anti-detect browser.
    
    When campaign_id is provided, only that specific campaign is processed.
    When campaign_id is None, falls back to processing all pending campaigns
    ordered by queue_position (backward-compat).
    """
    
    # Get campaign(s) to process
    if campaign_id:
        campaigns = get_campaign_by_id(campaign_id)
    else:
        campaigns = get_next_campaigns()
    
    if not campaigns:
        print('[INFO] No campaigns to process. Exiting.')
        return
    
    print(f'\n[INFO] Processing {len(campaigns)} campaign(s)...')
    
    # Track results for API response
    campaign_results = []
    
    # Process each campaign in queue
    for campaign_idx, campaign in enumerate(campaigns, 1):
        # Check for abort signal
        if event_store.is_aborted():
            print('\n[ABORT] Abort signal detected - stopping campaign processing')
            progress.warning('Campaign processing aborted by user')
            
            # Mark current campaign as aborted if it's in-progress
            campaign_id = campaign.get("campaign_id")
            if campaign_id:
                update_campaign_status(campaign_id, 'aborted')
            
            # Mark all remaining campaigns as not-started
            for remaining_campaign in campaigns[campaign_idx:]:
                remaining_id = remaining_campaign.get("campaign_id")
                if remaining_id:
                    print(f'[ABORT] Resetting campaign {remaining_id} to not-started')
            
            break
        
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
            
            # Update campaign status to 'failed' to prevent polling worker from re-detecting it
            try:
                update_campaign_status(campaign_id, 'failed')
                print(f'[STATUS] Campaign {campaign_id} marked as failed due to pre-flight check failures')
            except Exception as e:
                print(f'[WARN] Could not update campaign status: {e}')
            
            campaign_results.append({
                'campaign_id': campaign_id,
                'status': 'skipped',
                'reason': check_result.message,
                'errors': check_result.errors
            })
            
            # Skip to next campaign
            continue

        # ====================================================================
        # MEDIA PRE-FLIGHT CHECK: Verify storage files exist before proceeding
        # ====================================================================
        media_attachments = campaign.get('media_attachments') or []
        if media_attachments:
            print(f'[CHECK] Verifying {len(media_attachments)} media attachment(s) in storage for campaign {campaign_id}...')
            all_exist, missing_paths = verify_media_exists_in_storage(media_attachments)
            if not all_exist:
                print(f'[SKIP] Campaign {campaign_id}: media file(s) missing from storage: {missing_paths}')
                for mp in missing_paths:
                    progress.warning(f'Media file missing from storage: {mp}')
                try:
                    update_campaign_status(campaign_id, 'failed')
                    print(f'[STATUS] Campaign {campaign_id} marked as failed due to missing media files')
                except Exception as e:
                    print(f'[WARN] Could not update campaign status: {e}')
                campaign_results.append({
                    'campaign_id': campaign_id,
                    'status': 'skipped',
                    'reason': 'Media files missing from storage',
                    'errors': missing_paths
                })
                continue

        # Extract platform for logging
        platform = campaign.get('platform', 'instagram')
        platform_label = 'X/Twitter' if platform == 'x' else platform.capitalize()
        
        print(f'[OK] All pre-flight checks passed!')
        print(f'    - Campaign configuration valid')
        print(f'    - {platform_label} account credentials found')
        print(f'    - Anti-detect browser connected')
        print(f'    - {platform_label} browser profiles available')
        print(f'    - Note: Account suspension status will be verified during execution')
        
        # ====================================================================
        # STEP 2: CHANGE STATUS TO 'in-progress' (checks passed)
        # ====================================================================
        print(f'\n[STATUS] Updating campaign status to: in-progress')
        update_campaign_status(campaign_id, 'in-progress')
        
        # Extract campaign configuration (platform already extracted above)
        user_accounts = campaign.get('user_accounts', [])
        target_users = campaign.get('target_profiles', [])
        comment_text = campaign.get('custom_comment') or ''
        
        # Backwards compatibility: default to 15 seconds if post_delay is not set or is None
        post_delay = campaign.get('post_delay')
        if post_delay is None:
            post_delay = POST_TO_POST_DELAY_DEFAULT
        
        print(f'\n[INFO] Campaign will run using {len(user_accounts)} account(s):')
        for idx, acc in enumerate(user_accounts, 1):
            print(f'    {idx}. @{acc}')
        print(f'[CONFIG] Time between comments: {post_delay}s (with ±20% randomization)')
        
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
        # MEDIA DOWNLOAD: Fetch attachments to local disk before account loop
        # ====================================================================
        if media_attachments:
            print(f'[MEDIA] Downloading {len(media_attachments)} attachment(s) for campaign {campaign_id}...')
            try:
                local_media_paths = download_campaign_media(campaign_id, media_attachments)
                print(f'[MEDIA] Downloaded {len(local_media_paths)} file(s) to local temp for campaign {campaign_id}')
            except RuntimeError as e:
                print(f'[ERR] Media download failed for campaign {campaign_id}: {e}')
                progress.error(f'Media download failed for campaign {campaign_id}')
                try:
                    update_campaign_status(campaign_id, 'failed')
                    print(f'[STATUS] Campaign {campaign_id} marked as failed due to media download error')
                except Exception as ue:
                    print(f'[WARN] Could not update campaign status: {ue}')
                campaign_results.append({
                    'campaign_id': campaign_id,
                    'status': 'failed',
                    'reason': str(e)
                })
                continue
        else:
            local_media_paths = []

        # ====================================================================
        # STEP 3: PROCESS EACH USER ACCOUNT IN SEQUENCE
        # Each account runs in its own browser profile with the same criteria
        # ====================================================================
        
        campaign_success = False
        all_accounts_successful = True
        at_least_one_account_succeeded = False  # Track if any account worked
        accounts_tried = 0
        accounts_failed = 0
        
        for account_idx, account_username in enumerate(user_accounts, 1):
            # Check for abort signal before processing each account
            if event_store.is_aborted():
                print(f'\n[ABORT] Abort signal detected - skipping account @{account_username}')
                progress.warning(f'Skipping remaining accounts due to abort')
                all_accounts_successful = False
                break
            
            print('\n' + '='*70)
            print(f'[ACCOUNT] {account_idx}/{len(user_accounts)}: @{account_username}')
            print('='*70)
            
            # Note: We no longer check is_active status before processing
            # Inactive accounts will be tried and skipped only if they encounter actual roadblocks
            # This allows manually-resolved accounts to be retried without database updates
            print(f'[INFO] Attempting to use account @{account_username}...')
            
            # Get credentials for this account
            credentials = get_account_credentials(account_username, platform)
            if not credentials:
                print(f'[ERR] Could not get credentials for @{account_username}, skipping...')
                all_accounts_successful = False
                continue
            
            account_creds_username = credentials['username']
            account_creds_password = credentials['password']
            browser_profile_name = credentials.get('browser_profile', '')
            
            if not browser_profile_name:
                print(f'[ERR] No browser profile assigned to @{account_username}, skipping...')
                all_accounts_successful = False
                continue
            
            # Print configuration for this account
            # platform_label is defined earlier in the preflight success block
            print('\n' + '='*50)
            print(f'[CONFIG] {platform_label.upper()} CAMPAIGN CONFIGURATION')
            print('='*50)
            print(f'   Campaign ID: {campaign_id}')
            print(f'   Platform: {platform_label}')
            print(f'   Account: @{account_creds_username}')
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

            # ── Acquire cross-bot lock ─────────────────────────────────────
            bot_id = f"comment-bot:{campaign_id}"
            if not lock_manager.acquire_lock(account_username, platform, bot_id):
                print(f'[LOCK] @{account_username} is in use by another bot — skipping')
                progress.warning(f'@{account_username} in use by another bot — skipped')
                event_store.locked_accounts.append(account_username)
                all_accounts_successful = False
                continue

            try:
                # Connect to Dolphin Anty and show detailed connection info
                if not dolphin.login(show_progress=True):
                    raise Exception("Failed to connect to Dolphin Anty")
                
                # Get and display browser profiles for this platform ONLY
                # This ensures X campaigns see X profiles, Instagram campaigns see Instagram profiles
                # Note: platform_label is defined earlier in the preflight success block
                print(f'[CONFIG] Fetching {platform_label} browser profiles...')
                platform_profiles = get_platform_browser_profiles(platform)
                if platform_profiles:
                    print(f'[CONFIG] Found {len(platform_profiles)} {platform_label} profile(s):')
                    for p in platform_profiles:
                        print(f'  - @{p.get("username")} -> {p.get("browser_profile")}')
                    print()
                else:
                    print(f'[WARN] No browser profiles found for {platform_label}\n')
                
                # Find the assigned browser profile by name
                if not browser_profile_name:
                    raise Exception(f"No browser profile assigned to account @{account_creds_username}")
                
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
                automation_info = dolphin.start_profile(profile_id, headless=False)
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
                
                # ============================================================
                # PLATFORM-SPECIFIC AUTOMATION
                # ============================================================
                
                if platform == 'x':
                    # X/Twitter automation
                    print('='*50)
                    print('🐦 STARTING X/TWITTER AUTOMATION (Playwright)')
                    print('='*50 + '\n')
                    
                    # Create human-like functions dict for TwitterAutomation
                    human_like_funcs = {
                        'human_like_click': human_like_click,
                        'human_like_type': human_like_type,
                        'human_like_mouse_move': human_like_mouse_move,
                        'get_random_delay': get_random_delay,
                        'do_review_pause': do_review_pause,
                        'do_post_to_post_delay': do_post_to_post_delay,
                        'do_profile_to_profile_delay': do_profile_to_profile_delay,
                        'navigate_with_retry': navigate_with_retry,
                    }
                    
                    # Initialize Twitter automation
                    twitter_bot = TwitterAutomation(
                        progress_emitter=progress,
                        event_store=event_store,
                        human_like_funcs=human_like_funcs
                    )
                    
                    first_target = target_users[0]
                    progress.logging_in(account_creds_username)
                    try:
                        await twitter_bot.login(
                            page=page,
                            username=account_creds_username,
                            password=account_creds_password,
                            target_user=first_target
                        )
                        progress.login_success(account_creds_username)
                        
                        # Reactivate account if it was previously inactive (passed bot check)
                        is_active = credentials.get('is_active')
                        print(f'[DEBUG] Account @{account_creds_username} is_active value: {is_active} (type: {type(is_active).__name__})')
                        if is_active is False or is_active == 'false' or is_active == 0:
                            reactivate_account(account_creds_username, platform)
                            print(f'[OK] Account @{account_creds_username} passed verification - reactivated')
                    except Exception as login_error:
                        progress.login_failed(account_creds_username, str(login_error)[:100])
                        raise
                    
                    print('\n' + '='*50)
                    print('[OK] LOGIN PHASE COMPLETED!')
                    print('='*50)
                    
                    # Process tweets for each target user
                    all_results = []
                    
                    for idx, target_user in enumerate(target_users, 1):
                        if event_store.is_aborted():
                            print('\n[ABORT] Abort signal detected - stopping target processing')
                            progress.warning('Skipping remaining targets due to abort')
                            break
                        
                        print('\n' + '='*50)
                        print(f'[PROFILE] PROCESSING TARGET {idx}/{len(target_users)}: @{target_user}')
                        print('='*50)
                        
                        progress.navigating_to_profile(target_user)
                        user_logger = AutomationLogger()
                        
                        if mode == 'count':
                            post_result = await twitter_bot.process_posts_by_count(
                                page=page,
                                target_user=target_user,
                                post_count=post_count,
                                comment_text=comment_text,
                                logger=user_logger,
                                post_delay=post_delay,
                                local_media_paths=local_media_paths,
                            )
                        else:
                            post_result = await twitter_bot.process_posts_after_date(
                                page=page,
                                target_user=target_user,
                                date_threshold=date_threshold,
                                comment_text=comment_text,
                                logger=user_logger,
                                post_delay=post_delay,
                                local_media_paths=local_media_paths,
                            )
                        
                        print(f'\n[OK] COMPLETED @{target_user}')
                        user_logger.print_summary(stopped_early=post_result.get("stopped_early", False))
                        
                        comments_posted = post_result.get('posts_commented', 0)
                        progress.target_completed(target_user, comments_posted)
                        
                        all_results.append({
                            "target_user": target_user,
                            "result": post_result
                        })
                        
                        if idx < len(target_users):
                            await do_profile_to_profile_delay(idx, logger)
                
                elif platform == 'threads':
                    # Threads automation
                    print('='*50)
                    print('🧵 STARTING THREADS AUTOMATION (Playwright)')
                    print('='*50 + '\n')
                    
                    # Create human-like functions dict for ThreadsAutomation
                    human_like_funcs = {
                        'human_like_click': human_like_click,
                        'human_like_type': human_like_type,
                        'human_like_mouse_move': human_like_mouse_move,
                        'get_random_delay': get_random_delay,
                        'do_review_pause': do_review_pause,
                        'do_post_to_post_delay': do_post_to_post_delay,
                        'do_profile_to_profile_delay': do_profile_to_profile_delay,
                        'navigate_with_retry': navigate_with_retry,
                    }
                    
                    # Initialize Threads automation
                    threads_bot = ThreadsAutomation(
                        progress_emitter=progress,
                        event_store=event_store,
                        human_like_funcs=human_like_funcs
                    )
                    
                    first_target = target_users[0]
                    progress.logging_in(account_creds_username)
                    try:
                        await threads_bot.login(
                            page=page,
                            username=account_creds_username,
                            password=account_creds_password,
                            target_user=first_target
                        )
                        progress.login_success(account_creds_username)
                        
                        # Reactivate account if it was previously inactive (passed bot check)
                        is_active = credentials.get('is_active')
                        print(f'[DEBUG] Account @{account_creds_username} is_active value: {is_active} (type: {type(is_active).__name__})')
                        if is_active is False or is_active == 'false' or is_active == 0:
                            reactivate_account(account_creds_username, platform)
                            print(f'[OK] Account @{account_creds_username} passed verification - reactivated')
                    except Exception as login_error:
                        progress.login_failed(account_creds_username, str(login_error)[:100])
                        raise
                    
                    print('\n' + '='*50)
                    print('[OK] LOGIN PHASE COMPLETED!')
                    print('='*50)
                    
                    # Process posts for each target user
                    all_results = []
                    
                    for idx, target_user in enumerate(target_users, 1):
                        if event_store.is_aborted():
                            print('\n[ABORT] Abort signal detected - stopping target processing')
                            progress.warning('Skipping remaining targets due to abort')
                            break
                        
                        print('\n' + '='*50)
                        print(f'[PROFILE] PROCESSING TARGET {idx}/{len(target_users)}: @{target_user}')
                        print('='*50)
                        
                        progress.navigating_to_profile(target_user)
                        user_logger = AutomationLogger()
                        
                        if mode == 'count':
                            post_result = await threads_bot.process_posts_by_count(
                                page=page,
                                target_user=target_user,
                                post_count=post_count,
                                comment_text=comment_text,
                                logger=user_logger,
                                post_delay=post_delay,
                                local_media_paths=local_media_paths,
                            )
                        else:
                            post_result = await threads_bot.process_posts_after_date(
                                page=page,
                                target_user=target_user,
                                date_threshold=date_threshold,
                                comment_text=comment_text,
                                logger=user_logger,
                                post_delay=post_delay,
                                local_media_paths=local_media_paths,
                            )
                        
                        print(f'\n[OK] COMPLETED @{target_user}')
                        user_logger.print_summary(stopped_early=post_result.get("stopped_early", False))
                        
                        comments_posted = post_result.get('posts_commented', 0)
                        progress.target_completed(target_user, comments_posted)
                        
                        all_results.append({
                            "target_user": target_user,
                            "result": post_result
                        })
                        
                        if idx < len(target_users):
                            await do_profile_to_profile_delay(idx, logger)
                
                else:
                    # Instagram automation (default)
                    print('='*50)
                    print('📸 STARTING INSTAGRAM AUTOMATION (Playwright)')
                    print('='*50 + '\n')
                    
                    # Create human-like functions dict for InstagramAutomation
                    human_like_funcs = {
                        'human_like_click': human_like_click,
                        'human_like_type': human_like_type,
                        'human_like_mouse_move': human_like_mouse_move,
                        'get_random_delay': get_random_delay,
                        'do_review_pause': do_review_pause,
                        'do_post_to_post_delay': do_post_to_post_delay,
                        'do_profile_to_profile_delay': do_profile_to_profile_delay,
                        'navigate_with_retry': navigate_with_retry,
                    }
                    
                    # Initialize Instagram automation
                    instagram_bot = InstagramAutomation(
                        progress_emitter=progress,
                        event_store=event_store,
                        human_like_funcs=human_like_funcs
                    )
                    
                    first_target = target_users[0]
                    progress.logging_in(account_creds_username)
                    try:
                        await instagram_bot.login(
                            page=page,
                            username=account_creds_username,
                            password=account_creds_password,
                            target_user=first_target
                        )
                        progress.login_success(account_creds_username)
                        
                        # Reactivate account if it was previously inactive (passed bot check)
                        if credentials.get('is_active') == False:
                            reactivate_account(account_creds_username, platform)
                            print(f'[OK] Account @{account_creds_username} passed verification - reactivated')
                    except Exception as login_error:
                        progress.login_failed(account_creds_username, str(login_error)[:100])
                        raise  # Re-raise to be caught by outer exception handler
                    
                    print('\n' + '='*50)
                    print('[OK] LOGIN PHASE COMPLETED!')
                    print('='*50)
                    
                    # Process posts for each target user
                    all_results = []
                    
                    for idx, target_user in enumerate(target_users, 1):
                        # Check for abort signal before processing each target
                        if event_store.is_aborted():
                            print('\n[ABORT] Abort signal detected - stopping target processing')
                            progress.warning('Skipping remaining targets due to abort')
                            break
                        
                        print('\n' + '='*50)
                        print(f'[PROFILE] PROCESSING TARGET {idx}/{len(target_users)}: @{target_user}')
                        print('='*50)
                    
                        progress.navigating_to_profile(target_user)
                        user_logger = AutomationLogger()
                        
                        if mode == 'count':
                            post_result = await instagram_bot.process_posts_by_count(
                                page=page,
                                target_user=target_user,
                                post_count=post_count,
                                comment_text=comment_text,
                                logger=user_logger,
                                post_delay=post_delay
                            )
                        else:
                            post_result = await instagram_bot.process_posts_after_date(
                                page=page,
                                target_user=target_user,
                                date_threshold=date_threshold,
                                comment_text=comment_text,
                                logger=user_logger,
                                post_delay=post_delay
                            )
                        
                        print(f'\n[OK] COMPLETED @{target_user}')
                        user_logger.print_summary(stopped_early=post_result.get("stopped_early", False))
                        
                        # Emit target completed checkpoint
                        comments_posted = post_result.get('posts_commented', 0)
                        progress.target_completed(target_user, comments_posted)
                        
                        all_results.append({
                            "target_user": target_user,
                            "result": post_result
                        })
                        
                        if idx < len(target_users):
                            await do_profile_to_profile_delay(idx, logger)
                
                # Print summary for this account
                print('\n' + '='*50)
                print(f'[OK] ACCOUNT @{account_creds_username} COMPLETED!')
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
                at_least_one_account_succeeded = True  # Mark that we had at least one success
                
                if account_success:
                    print(f'\n[OK] Account @{account_creds_username} completed successfully')
                else:
                    print(f'\n[WARN] Account @{account_creds_username} had errors')
                    all_accounts_successful = False
                
            except Exception as e:
                print(f'\n[ERR] Account automation error for @{account_creds_username}: {e}')
                accounts_tried += 1
                accounts_failed += 1
                
                # Check if it's an abort signal
                if event_store.is_aborted():
                    print(f'[ABORT] Abort detected during error handling')
                    progress.warning(f'Campaign aborted - cleaning up @{account_creds_username}')
                    account_success = False
                    all_accounts_successful = False
                    # Break out of account loop - abort requested
                    break
                # Check if it's a suspension/bot challenge error
                elif ('bot challenge' in str(e).lower() or 
                      'human verification' in str(e).lower() or 
                      'account suspended' in str(e).lower() or
                      'phone verification' in str(e).lower() or
                      'login failed' in str(e).lower()):
                    print(f'[WARN] {platform_label} account @{account_creds_username} encountered a roadblock')
                    progress.warning(f'Account @{account_creds_username} blocked - will try next account')
                    
                    # Deactivate the blocked account so it's skipped in future campaigns
                    deactivate_account(account_creds_username, platform)
                    print(f'[INFO] Account @{account_creds_username} marked as inactive')
                    
                    # DON'T fail the campaign - continue to try next account
                    all_accounts_successful = False
                    # Continue to next account instead of breaking
                    # The finally block will handle cleanup
                else:
                    progress.error(f'Error with @{account_creds_username}: {str(e)[:100]}')
                
                account_success = False
                all_accounts_successful = False
                
            finally:
                print(f'\n[CLEANUP] Cleaning up browser resources for @{account_creds_username}...')
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

                # Release cross-bot lock (always, even on error)
                try:
                    lock_manager.release_lock(account_username, platform, f"comment-bot:{campaign_id}")
                except Exception:
                    pass

                # Check for abort before proceeding to next account
                if event_store.is_aborted():
                    print(f'\n[ABORT] Abort signal detected - skipping remaining accounts')
                    break
                
                # Delay before next account (if not the last one)
                if account_idx < len(user_accounts):
                    delay_time = random.uniform(10, 20)
                    print(f'\n[WAIT] Waiting {delay_time:.1f}s before starting next account...\n')
                    await asyncio.sleep(delay_time)
        
        # Update campaign status based on overall success
        # Campaign is successful if at least one account completed successfully
        campaign_success = at_least_one_account_succeeded
        
        # Check if campaign was aborted
        if event_store.is_aborted():
            print(f'\n[ABORT] Campaign {campaign_id} was aborted by user')
            campaign_results.append({
                'campaign_id': campaign_id,
                'status': 'aborted'
            })
            print(f'\n[STATUS] Updating campaign status to: aborted')
            update_campaign_status(campaign_id, 'aborted')
            progress.warning(f'Campaign {campaign_id} aborted')
        elif campaign_success:
            if all_accounts_successful:
                print(f'\n[OK] All {len(user_accounts)} account(s) completed successfully')
            else:
                print(f'\n[OK] Campaign completed ({accounts_failed} account(s) had issues but at least one succeeded)')
            campaign_results.append({
                'campaign_id': campaign_id,
                'status': 'completed'
            })
            # Update to completed
            print(f'\n[STATUS] Updating campaign status to: completed')
            update_campaign_status(campaign_id, 'completed')
            progress.campaign_completed()
        else:
            print(f'\n[ERR] All {len(user_accounts)} account(s) failed or encountered errors')
            # All accounts failed - mark campaign as failed
            print(f'\n[STATUS] Updating campaign status to: failed')
            update_campaign_status(campaign_id, 'failed')
            
            campaign_results.append({
                'campaign_id': campaign_id,
                'status': 'failed'
            })
            progress.error(f'Campaign {campaign_id} failed - all accounts blocked or errored')

        # ====================================================================
        # POST-CAMPAIGN MEDIA CLEANUP (unconditional — runs after all outcomes)
        # Tier 1: delete local temp directory for this campaign.
        # Tier 2: delete remote storage files if any were attached.
        # ====================================================================
        delete_local_campaign_dir(campaign_id)
        if media_attachments:
            delete_campaign_media_from_storage(media_attachments)

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
    aborted = sum(1 for r in campaign_results if r.get('status') == 'aborted')
    
    print(f'   Completed: {completed}')
    print(f'   Skipped: {skipped}')
    print(f'   Failed: {failed}')
    print(f'   Aborted: {aborted}')
    print('='*70)
    
    return campaign_results


# Manual-start mode: campaigns are started explicitly via POST /api/start
# No background polling worker — this keeps full control with the operator.


if __name__ == '__main__':
    # Check if running as API server or direct automation
    if len(sys.argv) > 1 and sys.argv[1] == 'api':
        # Run Flask API server
        print('[SERVER] Starting Social Media Comment Bot API Server...')
        print(f'[API] Documentation: http://localhost:{PORT}/api/docs')
        print(f'[API] Current Progress: http://localhost:{PORT}/api/progress/current')
        print(f'[API] Event Feed: http://localhost:{PORT}/api/progress/events')
        print(f'[ENV] Production mode: {IS_PRODUCTION}')
        
        print('[INFO] Manual-start mode — send POST /api/start to begin automation')
        
        # Use production-appropriate server settings
        if IS_PRODUCTION:
            # Production: use Waitress (Windows-compatible WSGI server)
            try:
                from waitress import serve
                print(f'[SERVER] Running with Waitress on 0.0.0.0:{PORT}')
                print(f'[SERVER] Production mode - debug disabled, 8 worker threads')
                serve(app, host='0.0.0.0', port=PORT, threads=8)
            except ImportError:
                print('[WARN] Waitress not installed, falling back to Flask dev server')
                print('[WARN] Install waitress for production: pip install waitress')
                app.run(debug=False, host='0.0.0.0', port=PORT, threaded=True)
        else:
            # Development: enable debug with Flask dev server
            print(f'[SERVER] Development mode - debug enabled')
            app.run(debug=True, host='0.0.0.0', port=PORT, threaded=True)
    else:
        # Run automation directly
        print('Running automation directly (use "python app.py api" for API server)')
        asyncio.run(run_automation_with_dolphin_anty())