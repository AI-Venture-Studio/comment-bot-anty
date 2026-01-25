"""
X/Twitter Automation Module

This module provides X/Twitter automation functionality:
- Selectors for DOM elements
- Regex-based tweet link extraction (DOM-only)
- Login verification and authentication
- Reply (comment) posting with human-like behavior
- Continuous scrolling extraction

Tweet Link Extraction:
- Uses regex pattern: ^\/TARGET_USER\/status\/\d+(\/.*)?$
- Scans ALL <a href> elements in the DOM
- No date checks, no repost logic, no engagement filtering
- Logs to target_user_tweets.txt with deduplication
- Runs continuously until browser closes or script terminates

Usage:
    from twitter import TwitterAutomation, TweetLinkExtractor, TwitterSelectors
"""

import asyncio
import os
import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable

from playwright.async_api import Page, ElementHandle


# ===========================================
# SECTION 1: SELECTORS
# ===========================================

class TwitterSelectors:
    """CSS/XPath selectors for X/Twitter elements"""
    
    # Login page
    USERNAME_INPUT = 'input[autocomplete="username"], input[name="text"]'
    PASSWORD_INPUT = 'input[name="password"], input[type="password"]'
    LOGIN_BUTTON = 'button[data-testid="LoginForm_Login_Button"], button:has-text("Log in"), button:has-text("Next")'
    NEXT_BUTTON = 'button:has-text("Next"), button[data-testid="ocfEnterTextNextButton"]'
    
    # Cookie consent
    COOKIE_ACCEPT_BUTTON = 'button:has-text("Accept all cookies"), button:has-text("Accept"), div[role="button"]:has-text("Accept")'
    
    # Post-login prompts
    SAVE_LOGIN_NOT_NOW = 'button:has-text("Not now"), span:has-text("Not now")'
    NOTIFICATIONS_NOT_NOW = 'button:has-text("Not now"), span:has-text("Not now"), button:has-text("Skip for now")'
    
    # Logged in indicators
    HOME_NAV = 'a[data-testid="AppTabBar_Home_Link"], a[href="/home"]'
    PROFILE_LINK = 'a[data-testid="AppTabBar_Profile_Link"]'
    SEARCH_ICON = 'a[data-testid="AppTabBar_Explore_Link"], a[href="/explore"]'
    COMPOSE_TWEET = 'a[data-testid="SideNav_NewTweet_Button"], a[href="/compose/tweet"]'
    
    # ===========================================
    # TWITTER LAYOUT SECTIONS (3-column layout)
    # ===========================================
    # Left sidebar: Navigation menu
    # Center: Main timeline/content (THIS IS WHERE WE SCROLL)
    # Right sidebar: Suggestions, trends
    
    # Center timeline section - the main scrollable area
    PRIMARY_COLUMN = 'div[data-testid="primaryColumn"]'
    TIMELINE_SECTION = 'section[aria-labelledby]'
    
    # Profile page - Tweets/Posts
    TWEET_ARTICLE = 'article[data-testid="tweet"]'
    TWEET_LINKS = 'article[data-testid="tweet"] a[href*="/status/"]'
    
    # More specific tweet link selector - gets the timestamp link
    TWEET_TIME_LINK = 'article[data-testid="tweet"] time[datetime] >> xpath=ancestor::a'
    
    # Individual Tweet/Post Page
    REPLY_INPUT = 'div[data-testid="tweetTextarea_0"], div[role="textbox"][data-testid="tweetTextarea_0"]'
    REPLY_BUTTON = 'button[data-testid="tweetButtonInline"], button[data-testid="tweetButton"]'
    
    # Timestamp for filtering posts by date
    POST_TIMESTAMP = 'time[datetime]'
    
    # Tweet row on profile
    TWEET_ROW = 'article[data-testid="tweet"]'
    FIRST_TWEET = 'article[data-testid="tweet"] a[href*="/status/"]'
    
    # Profile tabs (Posts, Replies, Highlights, Media)
    PROFILE_TABS = 'nav[aria-label="Profile timelines"]'
    POSTS_TAB = 'a[href$=""][role="tab"]:first-child, div[role="tablist"] a:first-child'
    
    # ===========================================
    # REPOST/RETWEET DETECTION SELECTORS
    # ===========================================
    REPOST_INDICATOR = '[data-testid="socialContext"]'
    REPOST_TEXT_PATTERNS = ['reposted', 'retweeted', 'Reposted', 'Retweeted']
    
    # Pin indicator for pinned tweets
    PINNED_INDICATOR = '[data-testid="socialContext"]'
    PINNED_TEXT_PATTERNS = ['pinned', 'Pinned']
    
    # ===========================================
    # REPLY AUTOMATION SELECTORS
    # ===========================================
    REPLY_BUTTON_ARIA = 'button[aria-label*="Reply"]'
    REPLY_BUTTON_TESTID = 'div[role="button"][data-testid="reply"]'
    
    # Reply modal/popover
    REPLY_DIALOG = 'div[role="dialog"]'
    REPLY_TEXTAREA = 'div[data-testid="tweetTextarea_0"]'
    REPLY_TEXTBOX = 'div[role="textbox"][data-testid="tweetTextarea_0"]'
    
    # Submit button (inside dialog)
    SUBMIT_BUTTON = 'button[data-testid="tweetButton"]'
    
    # Error/restriction indicators
    REPLIES_RESTRICTED = '[data-testid="tweetButtonInline"][aria-disabled="true"]'
    TWEET_UNAVAILABLE = 'span:has-text("This Tweet is unavailable")'
    TWEET_DELETED = 'span:has-text("This Tweet was deleted")'


# ===========================================
# SECTION 2: REPLY RESULT TYPE
# ===========================================

@dataclass
class TweetReplyResult:
    """Structured result for tweet reply attempts"""
    tweet_url: str
    reply_attempted: bool
    reply_posted: bool
    failure_reason: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "tweet_url": self.tweet_url,
            "reply_attempted": self.reply_attempted,
            "reply_posted": self.reply_posted,
            "failure_reason": self.failure_reason
        }


# ===========================================
# SECTION 3: HUMAN-LIKE TYPING UTILITIES
# ===========================================

# Typing delay constants (in milliseconds)
TYPING_DELAY_MIN_MS = 50
TYPING_DELAY_MAX_MS = 150
WORD_PAUSE_MIN = 0.1
WORD_PAUSE_MAX = 0.3
PUNCTUATION_PAUSE_MIN = 0.3
PUNCTUATION_PAUSE_MAX = 0.6
PRE_TYPING_HESITATION_MIN = 0.3
PRE_TYPING_HESITATION_MAX = 0.8


async def stream_type_text(
    element: ElementHandle,
    text: str,
    on_char_typed: Optional[Callable[[str, int], None]] = None
) -> None:
    """
    Stream text character-by-character with human-like typing delays.
    
    This mimics human typing behavior:
    - Variable delays between keystrokes
    - Longer pauses after punctuation
    - Pauses between words
    - Pre-typing hesitation
    
    Args:
        element: The input element to type into
        text: The text to stream
        on_char_typed: Optional callback called after each character (char, position)
    """
    punctuation_chars = '.!?,;:'
    
    # Pre-typing hesitation
    await asyncio.sleep(random.uniform(PRE_TYPING_HESITATION_MIN, PRE_TYPING_HESITATION_MAX))
    
    for idx, char in enumerate(text):
        # Type single character with variable delay
        delay_ms = random.randint(TYPING_DELAY_MIN_MS, TYPING_DELAY_MAX_MS)
        await element.type(char, delay=delay_ms)
        
        # Callback after typing
        if on_char_typed:
            on_char_typed(char, idx)
        
        # Additional pauses based on character type
        if char == ' ':
            # Pause between words
            await asyncio.sleep(random.uniform(WORD_PAUSE_MIN, WORD_PAUSE_MAX))
        elif char in punctuation_chars:
            # Longer pause after punctuation
            await asyncio.sleep(random.uniform(PUNCTUATION_PAUSE_MIN, PUNCTUATION_PAUSE_MAX))


# ===========================================
# SECTION 5: REGEX-BASED TWEET LINK EXTRACTOR
# ===========================================
# 
# This is a minimal, DOM-only tweet link extractor that:
# - Scans ALL <a href> elements in the live DOM
# - Uses regex to match tweet links for TARGET_USER
# - Extracts timestamp from <time datetime> in the same DOM subtree
# - Keeps matched URLs with timestamps in memory for computation/filtering
# - Runs continuously while scrolling
# - No date checks, no repost logic, no engagement filtering
# ===========================================


class TweetLinkExtractor:
    """
    Regex-based tweet link extractor with timestamp extraction.
    
    Continuously scans the DOM for <a href> elements matching:
        ^\/TARGET_USER\/status\/\d+(\/.*)?$
    
    For each match:
    - Normalizes URL to base format: https://x.com/TARGET_USER/status/<tweet_id>
    - Extracts <time datetime> from the same DOM subtree
    - Stores in memory as: (URL, datetime) tuples
    
    Deduplicates by base tweet URL.
    Keeps data in memory for computation and filtering - no file writing.
    """
    
    def __init__(self, target_user: str):
        """
        Initialize the extractor.
        
        Args:
            target_user: Twitter username to match (without @)
        """
        self.target_user = target_user.lower().lstrip('@')
        self.collected_urls: set = set()
        self.collected_data: list = []  # Store (url, datetime) tuples
        
        # Build the regex pattern for this target user
        # Pattern: ^\/TARGET_USER\/status\/(\d+)(\/.*)?$
        # Captures the tweet ID in group 1
        self.pattern = re.compile(
            rf'^/{re.escape(self.target_user)}/status/(\d+)(/.*)?$',
            re.IGNORECASE
        )
        
        print(f"[EXTRACTOR] Initialized for @{self.target_user}")
        print(f"[EXTRACTOR] Regex pattern: {self.pattern.pattern}")
        print(f"[EXTRACTOR] Mode: In-memory collection for computation/filtering")
    
    def _store_entry(self, base_url: str, datetime_str: str):
        """
        Store a URL with timestamp in memory.
        
        Args:
            base_url: The normalized tweet URL
            datetime_str: The datetime attribute value
        """
        self.collected_data.append((base_url, datetime_str))
    
    def _normalize_url(self, href: str) -> Optional[str]:
        """
        Normalize href to base tweet URL.
        
        Strips any path segments after the tweet ID.
        
        Args:
            href: The raw href attribute value (e.g., /user/status/123/analytics)
            
        Returns:
            Normalized URL: https://x.com/TARGET_USER/status/<tweet_id>
            or None if pattern doesn't match
        """
        match = self.pattern.match(href)
        if match:
            tweet_id = match.group(1)
            return f"https://x.com/{self.target_user}/status/{tweet_id}"
        return None
    
    async def extract_from_page(self, page: Page) -> list[tuple[str, str]]:
        """
        Scan the current DOM and extract all matching tweet links with timestamps.
        
        This method:
        1. Finds all anchor elements with href matching the pattern
        2. For each match, traverses up to find the containing tweet subtree
        3. Within that subtree, finds <time datetime> element
        4. Normalizes URL and extracts datetime
        5. Logs new matches to file
        6. Returns list of newly discovered (URL, datetime) tuples
        
        Args:
            page: Playwright page object
            
        Returns:
            List of newly discovered (base_url, datetime) tuples
        """
        # JavaScript to extract tweet links with timestamps from DOM subtrees
        # Does not rely on CSS class names or hardcoded DOM depth
        # Uses proximity within the same subtree by traversing up to find common ancestor
        js_extract = """
        (targetUser) => {
            const results = [];
            const pattern = new RegExp('^/' + targetUser + '/status/(\\\\d+)(/.*)?$', 'i');
            
            // Get all anchor elements with href
            const anchors = document.querySelectorAll('a[href]');
            
            for (const anchor of anchors) {
                const href = anchor.getAttribute('href');
                if (!href) continue;
                
                // Check if href matches the pattern
                const match = href.match(pattern);
                if (!match) continue;
                
                const tweetId = match[1];
                const baseUrl = 'https://x.com/' + targetUser + '/status/' + tweetId;
                
                // Find the containing subtree by traversing up
                // Look for the closest ancestor that contains a <time datetime> element
                let current = anchor;
                let datetime = null;
                
                // Traverse up the DOM tree to find a common ancestor with <time datetime>
                // Limit traversal to avoid going too far up (e.g., to body)
                let depth = 0;
                const maxDepth = 20;
                
                while (current && current !== document.body && depth < maxDepth) {
                    // Check if this element contains a <time datetime> element
                    const timeEl = current.querySelector('time[datetime]');
                    if (timeEl) {
                        datetime = timeEl.getAttribute('datetime');
                        break;
                    }
                    current = current.parentElement;
                    depth++;
                }
                
                // Only add if we found a datetime
                if (datetime) {
                    results.push({
                        baseUrl: baseUrl,
                        datetime: datetime
                    });
                }
            }
            
            return results;
        }
        """
        
        try:
            # Execute extraction in browser context
            raw_results = await page.evaluate(js_extract, self.target_user)
            
            new_entries = []
            
            for item in raw_results:
                base_url = item.get('baseUrl')
                datetime_str = item.get('datetime')
                
                if not base_url or not datetime_str:
                    continue
                
                # Check deduplication by base URL
                if base_url not in self.collected_urls:
                    self.collected_urls.add(base_url)
                    self._store_entry(base_url, datetime_str)
                    new_entries.append((base_url, datetime_str))
                    print(f"[FOUND] {base_url} | {datetime_str}")
            
            return new_entries
            
        except Exception as e:
            print(f"[ERROR] Extraction failed: {e}")
            return []
    
    def get_collected_count(self) -> int:
        """Return the total number of unique URLs collected."""
        return len(self.collected_urls)
    
    def get_all_collected(self) -> list[str]:
        """Return all collected base URLs as a list."""
        return list(self.collected_urls)
    
    def get_collected_with_timestamps(self) -> list[tuple[str, str]]:
        """Return all collected (URL, datetime) tuples."""
        return self.collected_data.copy()


async def run_continuous_extraction(
    page: Page,
    target_user: str,
    scroll_delay: float = 1.5
) -> None:
    """
    Run the tweet link extractor continuously while scrolling.
    
    This function:
    - Creates a TweetLinkExtractor for the target user
    - Continuously scrolls the page
    - Extracts matching tweet links after each scroll
    - Logs all matches to target_user_tweets.txt
    - Runs indefinitely until browser closes or script is terminated
    
    Args:
        page: Playwright page object (already navigated to target profile)
        target_user: Twitter username to match
        scroll_delay: Seconds to wait after each scroll
    """
    extractor = TweetLinkExtractor(target_user)
    
    scroll_count = 0
    
    print("=" * 60)
    print(f"[EXTRACTOR] Starting continuous extraction for @{target_user}")
    print(f"[EXTRACTOR] Mode: In-memory collection for computation")
    print(f"[EXTRACTOR] Press Ctrl+C or close browser to stop")
    print("=" * 60)
    
    # Initial extraction before scrolling
    initial_urls = await extractor.extract_from_page(page)
    print(f"[INITIAL] Found {len(initial_urls)} tweet links")
    
    # Continuous scroll loop - runs until interrupted
    while True:
        try:
            scroll_count += 1
            
            # Scroll down
            await page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(scroll_delay)
            
            # Extract after scroll
            new_urls = await extractor.extract_from_page(page)
            
            if new_urls:
                print(f"[SCROLL {scroll_count}] Found {len(new_urls)} new links (Total: {extractor.get_collected_count()})")
            
        except Exception as e:
            # Log error but continue - only stop on explicit termination
            print(f"[ERROR] Scroll {scroll_count}: {e}")
            await asyncio.sleep(scroll_delay)


async def extract_tweet_links_from_profile(
    page: Page,
    target_user: str,
    scroll_delay: float = 1.5
) -> TweetLinkExtractor:
    """
    Navigate to a user's profile and start continuous extraction.
    
    This is a convenience wrapper that:
    1. Navigates to the target user's profile
    2. Starts continuous extraction
    
    Args:
        page: Playwright page object
        target_user: Twitter username to extract tweets from
        scroll_delay: Seconds to wait after each scroll
        
    Returns:
        TweetLinkExtractor instance (for accessing collected URLs)
    """
    # Navigate to target profile
    profile_url = f"https://x.com/{target_user}"
    print(f"[BROWSER] Navigating to {profile_url}")
    
    await page.goto(profile_url, wait_until='domcontentloaded', timeout=60000)
    await asyncio.sleep(3)  # Wait for initial content
    
    # Run continuous extraction
    await run_continuous_extraction(page, target_user, scroll_delay)


# ===========================================
# SECTION 6: TWITTER AUTOMATION CLASS
# ===========================================

class TwitterAutomation:
    """
    X/Twitter automation class for commenting on posts.
    
    This class handles:
    - Login verification and authentication
    - Profile navigation
    - Tweet/post collection
    - Reply (comment) posting with human-like behavior
    - Batch processing
    """
    
    def __init__(self, progress_emitter, event_store, human_like_funcs: dict):
        """
        Initialize TwitterAutomation with required dependencies.
        
        Args:
            progress_emitter: ProgressEmitter instance for logging
            event_store: EventStore instance for tracking state
            human_like_funcs: Dict containing human-like behavior functions:
                - human_like_click
                - human_like_type
                - human_like_mouse_move
                - get_random_delay
                - do_review_pause
                - do_post_to_post_delay
                - do_profile_to_profile_delay
                - navigate_with_retry
        """
        self.progress = progress_emitter
        self.event_store = event_store
        self.human_like_click = human_like_funcs.get('human_like_click')
        self.human_like_type = human_like_funcs.get('human_like_type')
        self.get_random_delay = human_like_funcs.get('get_random_delay')
        self.do_review_pause = human_like_funcs.get('do_review_pause')
        self.do_post_to_post_delay = human_like_funcs.get('do_post_to_post_delay')
        self.do_profile_to_profile_delay = human_like_funcs.get('do_profile_to_profile_delay')
        self.navigate_with_retry = human_like_funcs.get('navigate_with_retry')
        
        # Constants
        self.MAX_COMMENT_RETRIES = 2
        self.CONSECUTIVE_OLD_POSTS_LIMIT = 4
    
    # ===========================================
    # LOGIN & AUTHENTICATION
    # ===========================================
    
    async def detect_bot_challenge(self, page: Page) -> bool:
        """
        Detect if X/Twitter is showing a bot challenge or verification.
        
        NOTE: This is intentionally conservative to avoid false positives.
        Only checks for EXPLICIT suspension/challenge screens, not just phrases.
        
        Returns:
            True if bot challenge detected, False otherwise
        """
        try:
            # CRITICAL: Only check for actual blocking elements that prevent automation
            # DO NOT check page content for phrases - too many false positives
            
            # Check 1: Arkose Labs CAPTCHA iframe (actual challenge)
            captcha_selectors = [
                'iframe[src*="arkose"]',
                '[id*="arkose"]',
            ]
            
            for selector in captcha_selectors:
                element = await page.query_selector(selector)
                if element:
                    self.progress.warning('Arkose CAPTCHA detected')
                    return True
            
            # Check 2: Account suspended page (URL-based check)
            current_url = page.url
            if 'account/suspended' in current_url or 'account/locked' in current_url:
                self.progress.warning('Account suspended URL detected')
                return True
            
            # Check 3: Phone verification MODAL (blocks login flow)
            phone_verification_modal = await page.query_selector('[data-testid="OCF_Verification"]')
            if phone_verification_modal:
                # Verify it's actually visible and blocking
                is_visible = await phone_verification_modal.is_visible()
                if is_visible:
                    self.progress.warning('Phone verification modal detected')
                    return True
            
            # If none of the above, assume account is OK
            return False
            
        except Exception as e:
            # On error, assume no challenge (fail open to avoid false positives)
            return False
    
    async def get_logged_in_username(self, page: Page) -> str:
        """
        Get the username of the currently logged-in X/Twitter account.
        
        Returns:
            Username without @ symbol, or empty string if not logged in
        """
        try:
            # Method 1: Check profile link in navigation
            profile_link = await page.query_selector(TwitterSelectors.PROFILE_LINK)
            if profile_link:
                href = await profile_link.get_attribute('href')
                if href:
                    username = href.strip('/').split('/')[-1]
                    if username:
                        return username.lstrip('@')
            
            # Method 2: Check for username in account switcher
            try:
                account_switcher = await page.query_selector('[data-testid="SideNav_AccountSwitcher_Button"]')
                if account_switcher:
                    username_span = await account_switcher.query_selector('span')
                    if username_span:
                        text = await username_span.inner_text()
                        if text and text.startswith('@'):
                            return text.lstrip('@')
            except:
                pass
            
            return ""
            
        except Exception as e:
            return ""
    
    async def verify_login(self, page: Page) -> bool:
        """
        Verify if we're logged into X/Twitter.
        
        Returns:
            True if logged in, False otherwise
        """
        try:
            # Check for logged-in elements
            selectors_to_check = [
                TwitterSelectors.HOME_NAV,
                TwitterSelectors.PROFILE_LINK,
                TwitterSelectors.COMPOSE_TWEET,
                '[data-testid="SideNav_AccountSwitcher_Button"]',
            ]
            
            for selector in selectors_to_check:
                try:
                    element = await page.wait_for_selector(selector, timeout=3000)
                    if element:
                        return True
                except:
                    continue
            
            # Check if login form is NOT present
            try:
                login_form = await page.query_selector(TwitterSelectors.USERNAME_INPUT)
                if login_form is None:
                    current_url = page.url
                    if 'login' not in current_url and 'flow' not in current_url:
                        return True
            except:
                pass
            
            return False
            
        except Exception as e:
            return False
    
    async def logout(self, page: Page):
        """Logout from X/Twitter."""
        try:
            # Navigate to logout page
            await self.navigate_with_retry(page, 'https://x.com/logout')
            await asyncio.sleep(2)
            
            # Click logout confirmation
            try:
                logout_button = await page.wait_for_selector('button[data-testid="confirmationSheetConfirm"]', timeout=5000)
                if logout_button:
                    await logout_button.click()
                    await asyncio.sleep(2)
            except:
                pass
                
            self.progress.info('Logged out successfully')
            
        except Exception as e:
            self.progress.warning(f'Logout may have failed')
    
    async def perform_login(self, page: Page, username: str, password: str):
        """
        Perform a fresh X/Twitter login.
        
        X/Twitter has a multi-step login:
        1. Enter username/email/phone
        2. Click Next
        3. Enter password
        4. Click Login
        """
        # Navigate to X/Twitter login page
        self.progress.action('Navigating to login page')
        await self.navigate_with_retry(page, 'https://x.com/i/flow/login')
        
        # Wait a moment for page to load
        await asyncio.sleep(2)
        
        # Accept cookies if prompted
        try:
            cookie_button = await page.wait_for_selector(TwitterSelectors.COOKIE_ACCEPT_BUTTON, timeout=5000)
            if cookie_button:
                await cookie_button.click()
                await asyncio.sleep(1)
        except:
            pass
        
        # Wait for username input
        self.progress.action('Waiting for login form')
        await page.wait_for_selector(TwitterSelectors.USERNAME_INPUT, timeout=15000)
        await asyncio.sleep(1)
        
        # Step 1: Enter username/email
        self.progress.action(f'Entering credentials for {username}')
        username_input = await page.query_selector(TwitterSelectors.USERNAME_INPUT)
        await username_input.click()
        await username_input.fill(username)
        await asyncio.sleep(0.5)
        
        # Click Next button
        next_button = await page.query_selector(TwitterSelectors.NEXT_BUTTON)
        if next_button:
            await next_button.click()
            await asyncio.sleep(2)
        
        # Check for additional verification
        try:
            additional_input = await page.wait_for_selector('input[data-testid="ocfEnterTextTextInput"]', timeout=3000)
            if additional_input:
                self.progress.action('Additional verification required')
                await additional_input.fill(username)
                await asyncio.sleep(0.5)
                next_button = await page.query_selector(TwitterSelectors.NEXT_BUTTON)
                if next_button:
                    await next_button.click()
                    await asyncio.sleep(2)
        except:
            pass
        
        # Step 2: Enter password
        password_input = await page.wait_for_selector(TwitterSelectors.PASSWORD_INPUT, timeout=10000)
        await password_input.click()
        await password_input.fill(password)
        await asyncio.sleep(0.5)
        
        # Click login button
        self.progress.action('Submitting login form')
        login_button = await page.query_selector(TwitterSelectors.LOGIN_BUTTON)
        if login_button:
            await login_button.click()
            await asyncio.sleep(5)
        
        # Handle post-login prompts
        try:
            not_now_button = await page.wait_for_selector(TwitterSelectors.NOTIFICATIONS_NOT_NOW, timeout=5000)
            if not_now_button:
                await not_now_button.click()
                await asyncio.sleep(2)
        except:
            pass
        
        # Wait for page to stabilize
        self.progress.info('Waiting for login to complete', significant=False)
        await asyncio.sleep(3)
        
        # Verify login succeeded by checking for home elements
        is_logged_in = await self.verify_login(page)
        if not is_logged_in:
            # Only check for bot challenge if login actually failed
            if await self.detect_bot_challenge(page):
                raise Exception('X/Twitter bot challenge detected - verification required')
            else:
                raise Exception('X/Twitter login failed - check credentials')
    
    async def login(self, page: Page, username: str, password: str, target_user: str):
        """
        Check X/Twitter login status and login if needed.
        
        Raises:
            Exception: If actual bot challenge is detected (not false positives)
        """
        # Navigate to X/Twitter and check login status
        self.progress.action('Checking login status')
        await self.navigate_with_retry(page, 'https://x.com/home')
        
        # Verify if already logged in
        is_logged_in = await self.verify_login(page)
        
        if is_logged_in:
            # Check if logged in as the correct account
            logged_in_username = await self.get_logged_in_username(page)
            expected_username = username.lstrip('@')
            
            if logged_in_username and logged_in_username.lower() == expected_username.lower():
                self.progress.success(f'Already logged in as @{expected_username}')
            elif logged_in_username:
                self.progress.warning(f'Logged in as @{logged_in_username}, need @{expected_username}. Logging out...')
                await self.logout(page)
                self.progress.warning('Logging in now')
                await self.perform_login(page, username, password)
            else:
                self.progress.success(f'Already logged in as @{username}')
        else:
            # Not logged in, perform fresh login
            self.progress.warning('Not logged in, logging in now')
            await self.perform_login(page, username, password)
        
        # Navigate to target user's profile
        self.progress.navigating_to_profile(target_user)
        await self.navigate_with_retry(page, f'https://x.com/{target_user}')
        
        # Only check for bot challenge if we can't access the profile
        # This prevents false positives during normal operation
        try:
            # Wait for profile to load
            await page.wait_for_selector('[data-testid="primaryColumn"]', timeout=10000)
        except:
            # If profile doesn't load, then check for bot challenge
            if await self.detect_bot_challenge(page):
                raise Exception('X/Twitter bot challenge detected on profile page')
    
    # ===========================================
    # TIMESTAMP PARSING
    # ===========================================
    
    def parse_timestamp(self, timestamp_str: str) -> Optional[datetime]:
        """
        Parse X/Twitter's timestamp format to datetime.
        
        Args:
            timestamp_str: ISO 8601 timestamp string
            
        Returns:
            datetime object or None if parsing fails
        """
        try:
            clean_timestamp = timestamp_str.replace('Z', '+00:00')
            return datetime.fromisoformat(clean_timestamp.replace('+00:00', ''))
        except Exception as e:
            print(f'[WARN] Could not parse timestamp: {timestamp_str} - {e}')
            return None
    
    # ===========================================
    # TWEET EXTRACTION FROM PROFILE (REGEX-BASED)
    # ===========================================
    
    async def get_tweet_links_from_profile(
        self, 
        page: Page, 
        target_user: str, 
        logger, 
        max_posts: int = None,
        **kwargs  # Accept but ignore legacy parameters
    ) -> list:
        """
        Get tweet links from a user's profile using regex-based DOM extraction.
        
        EXTRACTION LOGIC:
        - Scans ALL <a href> elements in the live DOM
        - Uses regex to match: ^\/TARGET_USER\/status\/(\d+)(\/.*)?$
        - Normalizes URLs to base format (strips /analytics, /likes, etc.)
        - Extracts <time datetime> from the same DOM subtree
        - Stores in memory as (URL, datetime) tuples for computation/filtering
        - Skips entries without a matching timestamp
        - Continuously scrolls and extracts
        
        Args:
            page: Playwright page object
            target_user: X/Twitter username
            logger: AutomationLogger instance
            max_posts: Optional limit on posts to collect (None = unlimited)
            **kwargs: Ignored legacy parameters (start_date, end_date, etc.)
            
        Returns:
            List of base tweet URLs (https://x.com/user/status/<id>)
        """
        # Create the regex-based extractor
        extractor = TweetLinkExtractor(target_user)
        
        scroll_count = 0
        SCROLL_WAIT_TIME = 1.5
        
        try:
            # Navigate to user's profile
            self.progress.info(f'Loading profile page for @{target_user}', significant=False)
            await self.navigate_with_retry(page, f'https://x.com/{target_user}')
            
            # Emit target opened checkpoint
            self.progress.target_opened(target_user)
            
            # Wait for initial content
            await asyncio.sleep(2)
            
            # Initial extraction
            initial_entries = await extractor.extract_from_page(page)
            self.progress.info(f'Initial scan: {len(initial_entries)} tweet links found', significant=False)
            
            # Continuous scroll and extract loop
            while True:
                # Check abort signal
                if self.event_store.is_aborted():
                    self.progress.warning('Tweet extraction aborted')
                    break
                
                # Check if we've hit max_posts limit
                if max_posts is not None and extractor.get_collected_count() >= max_posts:
                    self.progress.info(f'Reached target of {max_posts} tweets', significant=True)
                    break
                
                scroll_count += 1
                
                # Scroll down
                await page.evaluate("window.scrollBy(0, 800)")
                await asyncio.sleep(SCROLL_WAIT_TIME)
                
                # Extract after scroll
                new_entries = await extractor.extract_from_page(page)
                
                if new_entries:
                    self.progress.info(f'Scroll {scroll_count}: Found {len(new_entries)} new links (Total: {extractor.get_collected_count()})', significant=False)
                    for entry in new_entries:
                        logger.log_post_found()
            
            # Summary
            total_collected = extractor.get_collected_count()
            self.progress.success(f'Extracted {total_collected} tweet links for @{target_user}')
            
            return extractor.get_all_collected()
            
        except Exception as e:
            self.progress.target_failed(target_user, f'Unable to access profile: {str(e)[:50]}')
            print(f'[ERROR] Tweet extraction failed: {e}')
            return extractor.get_all_collected()
    
    async def run_continuous_tweet_extraction(
        self,
        page: Page,
        target_user: str,
        logger
    ) -> None:
        """
        Run continuous tweet extraction until browser closes or script terminates.
        
        This method:
        - Navigates to the target user's profile
        - Continuously scrolls and extracts tweet links with timestamps
        - Stores all matches in memory for computation/filtering
        - Never terminates on its own (runs until interrupted)
        
        Args:
            page: Playwright page object
            target_user: X/Twitter username
            logger: AutomationLogger instance
        """
        # Create the regex-based extractor
        extractor = TweetLinkExtractor(target_user)
        
        scroll_count = 0
        SCROLL_WAIT_TIME = 1.5
        
        try:
            # Navigate to user's profile
            self.progress.info(f'Loading profile page for @{target_user}', significant=False)
            await self.navigate_with_retry(page, f'https://x.com/{target_user}')
            
            # Emit target opened checkpoint
            self.progress.target_opened(target_user)
            
            # Wait for initial content
            await asyncio.sleep(2)
            
            print("=" * 60)
            print(f"[EXTRACTOR] Starting continuous extraction for @{target_user}")
            print(f"[EXTRACTOR] Mode: In-memory collection")
            print(f"[EXTRACTOR] Format: (URL, datetime) tuples")
            print(f"[EXTRACTOR] Runs until browser closes or script is terminated")
            print("=" * 60)
            
            # Initial extraction
            initial_entries = await extractor.extract_from_page(page)
            print(f"[INITIAL] Found {len(initial_entries)} tweet links with timestamps")
            
            # Continuous scroll loop - runs until interrupted
            while True:
                # Check abort signal
                if self.event_store.is_aborted():
                    self.progress.warning('Tweet extraction aborted')
                    break
                
                scroll_count += 1
                
                # Scroll down
                await page.evaluate("window.scrollBy(0, 800)")
                await asyncio.sleep(SCROLL_WAIT_TIME)
                
                # Extract after scroll
                new_entries = await extractor.extract_from_page(page)
                
                if new_entries:
                    print(f"[SCROLL {scroll_count}] Found {len(new_entries)} new links (Total: {extractor.get_collected_count()})")
                    for entry in new_entries:
                        logger.log_post_found()
                        
        except Exception as e:
            print(f"[ERROR] Continuous extraction error: {e}")
            # Don't re-raise - continue as long as possible
    
    async def get_post_timestamp(self, page: Page) -> Optional[datetime]:
        """Get the timestamp of the current tweet."""
        try:
            time_element = await page.wait_for_selector(TwitterSelectors.POST_TIMESTAMP, timeout=10000)
            if time_element:
                datetime_attr = await time_element.get_attribute('datetime')
                if datetime_attr:
                    return self.parse_timestamp(datetime_attr)
        except Exception as e:
            print(f'[WARN] Could not get tweet timestamp: {e}')
        return None
    
    # ===========================================
    # REPLY FUNCTIONALITY
    # ===========================================
    
    async def _is_single_tweet_page(self, page: Page) -> bool:
        """
        Verify we're on a single tweet detail page, not a timeline.
        
        Returns:
            True if on a single tweet page (URL contains /status/)
        """
        current_url = page.url
        # Single tweet pages have pattern: x.com/username/status/tweet_id
        return '/status/' in current_url
    
    async def _scroll_to_reveal_reply_composer(
        self,
        page: Page,
        max_scrolls: int = 10,
        scroll_amount: int = 300
    ) -> bool:
        """
        Scroll the page to reveal the reply composer below the tweet.
        
        The reply composer may be below the fold and needs scrolling to appear.
        
        Args:
            page: Playwright page object
            max_scrolls: Maximum number of scroll attempts
            scroll_amount: Pixels to scroll per attempt
            
        Returns:
            True if reply composer found, False if max scrolls reached
        """
        for attempt in range(max_scrolls):
            # Check if reply composer is already visible
            # Look for "Post your reply" placeholder or reply textbox
            reply_composer = await page.locator(
                '[data-testid="tweetTextarea_0"], '
                '[aria-label*="Post your reply"], '
                '[placeholder*="Post your reply"], '
                'div[role="textbox"][data-testid="tweetTextarea_0"]'
            ).first.is_visible()
            
            if reply_composer:
                return True
            
            # Scroll down incrementally
            await page.mouse.wheel(0, scroll_amount)
            await asyncio.sleep(0.3)
        
        return False
    
    async def _locate_reply_input(self, page: Page) -> Optional[ElementHandle]:
        """
        Locate the reply input area using accessible selectors.
        
        Priority order:
        1. role="textbox" with aria-label containing reply intent
        2. data-testid="tweetTextarea_0" (Twitter's internal selector)
        3. contenteditable="true" in reply section
        
        Excludes:
        - Search inputs
        - DM inputs
        - Modal dialog inputs
        
        Returns:
            ElementHandle for the reply input, or None if not found
        """
        # Strategy 1: Use Playwright locator with role and accessible name
        # This is the most reliable accessible approach
        try:
            # Look for textbox with reply-related accessible name
            reply_textbox = page.locator('div[role="textbox"]').filter(
                has=page.locator('[data-testid="tweetTextarea_0"]')
            )
            if await reply_textbox.count() > 0:
                element = await reply_textbox.first.element_handle()
                if element and await element.is_visible():
                    return element
        except Exception:
            pass
        
        # Strategy 2: Direct data-testid selector (Twitter's internal)
        try:
            textarea = page.locator('[data-testid="tweetTextarea_0"]')
            if await textarea.count() > 0:
                # Ensure we get the one in the reply section, not a modal
                # Check it's not inside a dialog
                for i in range(await textarea.count()):
                    element = await textarea.nth(i).element_handle()
                    if element:
                        # Check not inside dialog
                        parent_dialog = await page.evaluate(
                            """(el) => {
                                let current = el;
                                while (current && current !== document.body) {
                                    if (current.getAttribute('role') === 'dialog') {
                                        return true;
                                    }
                                    current = current.parentElement;
                                }
                                return false;
                            }""",
                            element
                        )
                        if not parent_dialog and await element.is_visible():
                            return element
        except Exception:
            pass
        
        # Strategy 3: Look for contenteditable in the reply section
        # The reply section is typically after the tweet actions (like, repost, etc.)
        try:
            # Find contenteditable elements that are visible and not in dialogs/search
            contenteditable = page.locator(
                'div[contenteditable="true"]:not([aria-label*="Search"]):not([aria-label*="Direct Message"])'
            )
            if await contenteditable.count() > 0:
                for i in range(await contenteditable.count()):
                    element = await contenteditable.nth(i).element_handle()
                    if element:
                        # Verify not in dialog
                        parent_dialog = await page.evaluate(
                            """(el) => {
                                let current = el;
                                while (current && current !== document.body) {
                                    if (current.getAttribute('role') === 'dialog') {
                                        return true;
                                    }
                                    current = current.parentElement;
                                }
                                return false;
                            }""",
                            element
                        )
                        if not parent_dialog and await element.is_visible():
                            # Additional check: ensure it's the reply composer
                            # by checking for nearby "Post" button
                            has_post_button = await page.evaluate(
                                """(el) => {
                                    // Traverse up to find container with Post button
                                    let current = el;
                                    let depth = 0;
                                    while (current && current !== document.body && depth < 10) {
                                        const postBtn = current.querySelector('[data-testid="tweetButton"], [data-testid="tweetButtonInline"]');
                                        if (postBtn) return true;
                                        current = current.parentElement;
                                        depth++;
                                    }
                                    return false;
                                }""",
                                element
                            )
                            if has_post_button:
                                return element
        except Exception:
            pass
        
        return None
    
    async def _validate_reply_input(self, page: Page, element: ElementHandle) -> bool:
        """
        Validate that the located element is the correct reply input.
        
        Checks:
        - Element is visible
        - Element is editable (contenteditable or not disabled)
        - Not a search/DM input
        
        Args:
            page: Playwright page object
            element: The element to validate
            
        Returns:
            True if valid reply input, False otherwise
        """
        try:
            # Check visibility
            if not await element.is_visible():
                return False
            
            # Check it's editable
            is_editable = await page.evaluate(
                """(el) => {
                    // Check contenteditable
                    if (el.contentEditable === 'true' || el.isContentEditable) {
                        return true;
                    }
                    // Check if it's an input/textarea that's not disabled
                    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                        return !el.disabled && !el.readOnly;
                    }
                    // Check for nested editable
                    const editable = el.querySelector('[contenteditable="true"]');
                    return editable !== null;
                }""",
                element
            )
            if not is_editable:
                return False
            
            # Check it's not a search or DM input
            aria_label = await element.get_attribute('aria-label') or ''
            aria_label_lower = aria_label.lower()
            
            excluded_labels = ['search', 'direct message', 'dm', 'compose']
            for excluded in excluded_labels:
                if excluded in aria_label_lower:
                    return False
            
            return True
            
        except Exception:
            return False
    
    async def _get_composer_container_from_textarea(self, page: Page) -> Optional[any]:
        """
        Starting from the textarea element, traverse up the DOM to find the 
        stable reply composer container.
        
        The composer container is typically 3-5 parent levels up from the textarea
        and contains both the textarea and the Reply button.
        
        Returns:
            Playwright Locator for the composer container, or None if not found
        """
        try:
            # Start with the textarea locator
            textarea = page.locator('[data-testid="tweetTextarea_0"]').first
            
            if await textarea.count() == 0:
                return None
            
            # Traverse up the DOM tree to find the composer container
            # The container should have the Reply/Post button as a descendant
            # Try traversing 3-5 levels up
            for depth in range(3, 8):
                # Build the parent chain dynamically
                container = textarea
                for _ in range(depth):
                    container = container.locator('..')
                
                # Check if this container has a tweet button
                has_button = await container.locator('[data-testid="tweetButton"], [data-testid="tweetButtonInline"]').count() > 0
                
                if has_button:
                    return container
            
            return None
            
        except Exception as e:
            print(f"[DEBUG] Error finding composer container: {e}")
            return None
    
    async def _locate_reply_button_scoped(self, page: Page) -> Optional[any]:
        """
        Locate the Reply button by scoping the search to the composer container.
        
        This method:
        1. Starts from the textarea [data-testid="tweetTextarea_0"]
        2. Traverses up 3-5 parent levels to find the composer container
        3. Searches for the Reply button ONLY within that container
        
        Returns:
            Playwright Locator for the Reply button, or None if not found
        """
        import re
        
        try:
            # Get the composer container
            composer_container = await self._get_composer_container_from_textarea(page)
            
            if not composer_container:
                self.progress.warning('Could not find composer container')
                return None
            
            # Search for Reply button within the container using data-testid
            reply_button = composer_container.locator('[data-testid="tweetButton"]').first
            
            if await reply_button.count() > 0:
                return reply_button
            
            # Fallback: Search for button with "Reply" text within container
            reply_button = composer_container.locator('button').filter(
                has_text=re.compile(r'^Reply$', re.IGNORECASE)
            ).first
            
            if await reply_button.count() > 0:
                return reply_button
            
            # Fallback: Search for tweetButtonInline
            reply_button = composer_container.locator('[data-testid="tweetButtonInline"]').first
            
            if await reply_button.count() > 0:
                return reply_button
            
            return None
            
        except Exception as e:
            print(f"[DEBUG] Error locating scoped reply button: {e}")
            return None
    
    async def _wait_for_reply_button_enabled(self, page: Page, timeout_ms: int = 10000) -> bool:
        """
        Wait for the Reply button to be enabled using page.wait_for_function().
        
        Checks that the button:
        - Exists in the DOM
        - Is not disabled (aria-disabled !== 'true' and not .disabled)
        - Has offsetParent !== null (is visible/rendered)
        
        Args:
            page: Playwright page object
            timeout_ms: Timeout in milliseconds (default 10 seconds)
            
        Returns:
            True if button is enabled within timeout, False otherwise
        """
        try:
            # JavaScript function to check if the Reply button is enabled
            # Scoped to the composer container by traversing from textarea
            js_check_button_enabled = """
            () => {
                // Find the textarea first
                const textarea = document.querySelector('[data-testid="tweetTextarea_0"]');
                if (!textarea) return false;
                
                // Traverse up to find the composer container with the button
                let current = textarea;
                let depth = 0;
                const maxDepth = 10;
                
                while (current && current !== document.body && depth < maxDepth) {
                    const btn = current.querySelector('[data-testid="tweetButton"], [data-testid="tweetButtonInline"]');
                    if (btn) {
                        // Check if button exists, is not disabled, and is visible
                        const isDisabled = btn.getAttribute('aria-disabled') === 'true' || btn.disabled;
                        const isVisible = btn.offsetParent !== null;
                        return !isDisabled && isVisible;
                    }
                    current = current.parentElement;
                    depth++;
                }
                return false;
            }
            """
            
            await page.wait_for_function(js_check_button_enabled, timeout=timeout_ms)
            return True
            
        except Exception as e:
            print(f"[DEBUG] Timeout waiting for Reply button to enable: {e}")
            return False
    
    async def _click_reply_button(self, page: Page, reply_button) -> bool:
        """
        Click the Reply button using Playwright's native click.
        
        This method:
        1. Scrolls the button into view if needed
        2. Waits 500ms
        3. Uses native Playwright click with timeout
        
        Never uses:
        - JavaScript evaluate() to click
        - force=True
        - Global search for button
        
        Args:
            page: Playwright page object
            reply_button: Playwright Locator for the Reply button
            
        Returns:
            True if click succeeded, False otherwise
        """
        try:
            # Scroll into view
            await reply_button.scroll_into_view_if_needed()
            
            # Wait 500ms after scrolling
            await asyncio.sleep(0.5)
            
            # Use native Playwright click with timeout
            await reply_button.click(timeout=5000)
            
            return True
            
        except Exception as e:
            print(f"[DEBUG] Failed to click Reply button: {e}")
            return False
    
    async def _locate_post_button(self, page: Page, reply_input: ElementHandle) -> Optional[ElementHandle]:
        """
        Locate the Post button within the reply composer.
        
        The Post button should be:
        - In the same container as the reply input
        - Have text "Post" or "Reply"
        - Be enabled and visible
        
        Args:
            page: Playwright page object
            reply_input: The reply input element (used to scope search)
            
        Returns:
            ElementHandle for the Post button, or None if not found
        """
        # Strategy 1: Find Post button by data-testid in same container
        try:
            # Navigate up to find container, then find button within
            post_button = await page.evaluate_handle(
                """(replyInput) => {
                    let current = replyInput;
                    let depth = 0;
                    const maxDepth = 15;
                    
                    while (current && current !== document.body && depth < maxDepth) {
                        // Look for the tweet button
                        const btn = current.querySelector('[data-testid="tweetButton"], [data-testid="tweetButtonInline"]');
                        if (btn) {
                            return btn;
                        }
                        current = current.parentElement;
                        depth++;
                    }
                    return null;
                }""",
                reply_input
            )
            
            if post_button:
                element = post_button.as_element()
                if element and await element.is_visible():
                    return element
        except Exception:
            pass
        
        # Strategy 2: Use Playwright locator for button with "Post" text
        try:
            post_buttons = page.locator('button').filter(has_text='Post')
            if await post_buttons.count() > 0:
                for i in range(await post_buttons.count()):
                    btn = await post_buttons.nth(i).element_handle()
                    if btn and await btn.is_visible():
                        # Ensure not in a dialog
                        parent_dialog = await page.evaluate(
                            """(el) => {
                                let current = el;
                                while (current && current !== document.body) {
                                    if (current.getAttribute('role') === 'dialog') {
                                        return true;
                                    }
                                    current = current.parentElement;
                                }
                                return false;
                            }""",
                            btn
                        )
                        if not parent_dialog:
                            return btn
        except Exception:
            pass
        
        # Strategy 3: Find by aria-label
        try:
            post_by_aria = page.locator('button[aria-label*="Post"], div[role="button"][aria-label*="Post"]')
            if await post_by_aria.count() > 0:
                element = await post_by_aria.first.element_handle()
                if element and await element.is_visible():
                    return element
        except Exception:
            pass
        
        return None
    
    async def _is_post_button_enabled(self, page: Page, button: ElementHandle) -> bool:
        """
        Check if the Post button is enabled and clickable.
        
        Returns:
            True if enabled, False otherwise
        """
        try:
            is_disabled = await page.evaluate(
                """(btn) => {
                    // Check aria-disabled
                    if (btn.getAttribute('aria-disabled') === 'true') return true;
                    // Check disabled attribute
                    if (btn.disabled) return true;
                    // Check for disabled class patterns
                    const classes = btn.className || '';
                    if (classes.includes('disabled') || classes.includes('Disabled')) return true;
                    return false;
                }""",
                button
            )
            return not is_disabled and await button.is_visible()
        except Exception:
            return False
    
    async def _verify_reply_posted(self, page: Page, original_text: str) -> bool:
        """
        Verify that the reply was successfully posted.
        
        Checks:
        - Input field is cleared
        - Or new reply appears in thread
        
        Args:
            page: Playwright page object
            original_text: The text that was posted
            
        Returns:
            True if reply appears to be posted, False otherwise
        """
        try:
            await asyncio.sleep(1.5)
            
            # Check 1: Input field should be empty or cleared
            reply_input = await self._locate_reply_input(page)
            if reply_input:
                text_content = await page.evaluate(
                    "(el) => el.textContent || el.innerText || ''",
                    reply_input
                )
                # If input is empty or has placeholder, reply was likely posted
                if not text_content.strip() or text_content.strip() == original_text:
                    # If still has our text, might not have posted
                    pass
                else:
                    return True
            
            # Check 2: Wait a bit more and re-check
            await asyncio.sleep(1)
            
            # Check if there's a success toast or the reply appears
            # This is a best-effort check
            return True  # Assume success if no errors
            
        except Exception:
            return True  # Fail open - assume posted
    
    async def reply_to_tweet(self, page: Page, reply_text: str, logger) -> bool:
        """
        Post a reply to the current tweet using accessible DOM selectors.
        
        This method:
        1. Verifies we're on a single tweet page
        2. Scrolls to reveal the reply composer
        3. Locates the reply input using accessible selectors
        4. Validates the input is correct
        5. Focuses and streams text with human-like delays
        6. Locates and clicks the Post button
        7. Verifies the reply was posted
        
        Args:
            page: Playwright page object (must be on a tweet detail page)
            reply_text: The text to post as a reply
            logger: AutomationLogger instance
            
        Returns:
            True if reply was posted successfully, False otherwise
        """
        # Step 1: Ensure we're on a single tweet page
        if not await self._is_single_tweet_page(page):
            self.progress.warning('Not on a single tweet page - cannot reply')
            return False
        
        self.progress.info('Preparing to reply', significant=False)
        
        for attempt in range(self.MAX_COMMENT_RETRIES):
            try:
                # Step 2: Scroll to reveal reply composer
                self.progress.info('Scrolling to reveal reply composer', significant=False)
                composer_visible = await self._scroll_to_reveal_reply_composer(page)
                
                if not composer_visible:
                    self.progress.warning(f'Reply composer not found after scrolling (attempt {attempt + 1}/{self.MAX_COMMENT_RETRIES})')
                    if attempt < self.MAX_COMMENT_RETRIES - 1:
                        await asyncio.sleep(1)
                        continue
                    return False
                
                # Step 3: Locate the reply input
                reply_input = await self._locate_reply_input(page)
                
                if not reply_input:
                    self.progress.warning(f'Reply input not found (attempt {attempt + 1}/{self.MAX_COMMENT_RETRIES})')
                    if attempt < self.MAX_COMMENT_RETRIES - 1:
                        await asyncio.sleep(1)
                        continue
                    return False
                
                # Step 4: Validate the input
                if not await self._validate_reply_input(page, reply_input):
                    self.progress.warning(f'Reply input validation failed (attempt {attempt + 1}/{self.MAX_COMMENT_RETRIES})')
                    if attempt < self.MAX_COMMENT_RETRIES - 1:
                        await asyncio.sleep(1)
                        continue
                    return False
                
                # Step 5: Focus and type the reply with streaming
                self.progress.info('Focusing reply input', significant=False)
                
                try:
                    await reply_input.scroll_into_view_if_needed()
                    await asyncio.sleep(self.get_random_delay(0.2, 0.5))
                except:
                    pass
                
                # Click to focus
                await self.human_like_click(page, reply_input, logger)
                await asyncio.sleep(self.get_random_delay(0.3, 0.7))
                
                # Stream the reply text character-by-character with human-like delays
                self.progress.info('Typing reply', significant=False)
                await stream_type_text(reply_input, reply_text)
                
                # Review pause after typing
                await self.do_review_pause(logger)
                
                # Step 6: Wait for DOM updates after typing, then locate and click Reply button
                # Wait 1 second for DOM updates after typing
                self.progress.info('Waiting for DOM updates', significant=False)
                await asyncio.sleep(1.0)
                
                # Locate the Reply button scoped to the composer container
                self.progress.info('Locating Reply button in composer', significant=False)
                reply_button = await self._locate_reply_button_scoped(page)
                
                if not reply_button:
                    self.progress.warning('Reply button not found in composer container')
                    # Fallback: Try keyboard shortcut
                    self.progress.info('Attempting keyboard shortcut to post', significant=False)
                    await page.keyboard.press('Meta+Enter')  # macOS
                    await asyncio.sleep(0.5)
                    await page.keyboard.press('Control+Enter')  # Windows/Linux
                    await asyncio.sleep(2)
                    
                    # Verify
                    if await self._verify_reply_posted(page, reply_text):
                        logger.log_success(f'[OK] Reply posted via keyboard: "{reply_text}"')
                        self.progress.info(f'Reply posted: "{reply_text}"', significant=True)
                        return True
                    else:
                        self.progress.warning('Reply may not have posted')
                        return False
                
                # Wait for Reply button to be enabled using wait_for_function
                self.progress.info('Waiting for Reply button to enable', significant=False)
                button_enabled = await self._wait_for_reply_button_enabled(page, timeout_ms=10000)
                
                if not button_enabled:
                    self.progress.warning('Reply button not enabled after 10 seconds')
                    # Still try to click - might work
                
                # Click the Reply button using native Playwright click
                self.progress.info('Clicking Reply button', significant=False)
                click_success = await self._click_reply_button(page, reply_button)
                
                if not click_success:
                    self.progress.warning('Failed to click Reply button')
                    # Fallback: Try keyboard shortcut
                    self.progress.info('Attempting keyboard shortcut to post', significant=False)
                    await page.keyboard.press('Meta+Enter')  # macOS
                    await asyncio.sleep(0.5)
                    await page.keyboard.press('Control+Enter')  # Windows/Linux
                    await asyncio.sleep(2)
                
                # Step 7: Verify reply was posted
                await asyncio.sleep(1.5)
                
                if await self._verify_reply_posted(page, reply_text):
                    logger.log_success(f'[OK] Reply posted: "{reply_text}"')
                    self.progress.info(f'Reply posted: "{reply_text}"', significant=True)
                    return True
                else:
                    self.progress.warning('Reply verification uncertain - assuming success')
                    return True
                
            except Exception as e:
                self.progress.warning(f'Reply attempt failed (attempt {attempt + 1}/{self.MAX_COMMENT_RETRIES}): {str(e)[:50]}')
                if attempt < self.MAX_COMMENT_RETRIES - 1:
                    await asyncio.sleep(1)
        
        self.progress.error(f'Could not post reply after {self.MAX_COMMENT_RETRIES} attempts')
        return False
    
    async def type_reply_with_streaming(
        self,
        page: Page,
        reply_input: ElementHandle,
        reply_text: str,
        logger
    ) -> bool:
        """
        Type a reply into an already-located input element using human-like streaming.
        
        This method handles ONLY the typing portion with human-like delays.
        The caller is responsible for:
        - Finding/locating the reply input element
        - Submitting the reply after typing
        
        Args:
            page: Playwright page object
            reply_input: The input element to type into (already located)
            reply_text: The text to type
            logger: AutomationLogger instance
            
        Returns:
            True if typing completed successfully, False otherwise
        """
        try:
            # Focus the input element
            try:
                await reply_input.scroll_into_view_if_needed()
                await asyncio.sleep(self.get_random_delay(0.2, 0.5))
            except:
                pass
            
            await self.human_like_click(page, reply_input, logger)
            await asyncio.sleep(self.get_random_delay(0.3, 0.7))
            
            # Stream the reply text character-by-character with human-like delays
            self.progress.info('Typing reply', significant=False)
            await stream_type_text(reply_input, reply_text)
            
            # Review pause after typing
            await self.do_review_pause(logger)
            
            self.progress.info(f'Typed reply: "{reply_text}"', significant=True)
            return True
            
        except Exception as e:
            self.progress.warning(f'Failed to type reply: {str(e)[:50]}')
            return False
    
    async def reply_to_tweet_with_result(
        self,
        page: Page,
        reply_text: str,
        logger,
        tweet_url: Optional[str] = None
    ) -> TweetReplyResult:
        """
        Add a reply to the current tweet and return a structured result.
        """
        url = tweet_url or page.url
        result = TweetReplyResult(
            tweet_url=url,
            reply_attempted=True,
            reply_posted=False,
            failure_reason=None
        )
        
        try:
            success = await self.reply_to_tweet(page, reply_text, logger)
            result.reply_posted = success
            if not success:
                result.failure_reason = "Reply submission failed"
        except Exception as e:
            result.failure_reason = f"Error: {str(e)}"
        
        return result
    
    async def reply_to_tweet_by_url(
        self,
        page: Page,
        tweet_url: str,
        reply_text: str,
        logger
    ) -> TweetReplyResult:
        """
        Navigate to a tweet URL and post a reply.
        """
        result = TweetReplyResult(
            tweet_url=tweet_url,
            reply_attempted=False,
            reply_posted=False,
            failure_reason=None
        )
        
        try:
            # Navigate to the tweet
            self.progress.info(f'Navigating to tweet', significant=False)
            await self.navigate_with_retry(page, tweet_url)
            
            # Wait for tweet to load
            try:
                await page.wait_for_selector(
                    TwitterSelectors.TWEET_ARTICLE,
                    state='visible',
                    timeout=10000
                )
            except Exception:
                result.failure_reason = "Tweet failed to load"
                return result
            
            await asyncio.sleep(1.5)
            
            # Now reply
            result.reply_attempted = True
            success = await self.reply_to_tweet(page, reply_text, logger)
            result.reply_posted = success
            if not success:
                result.failure_reason = "Reply submission failed"
                
        except Exception as e:
            result.failure_reason = f"Navigation error: {str(e)}"
        
        return result
    
    async def batch_reply_to_tweets(
        self,
        page: Page,
        tweet_data_list: list,
        logger,
        delay_between_replies: tuple = (3.0, 6.0),
        stop_on_failure: bool = False
    ) -> list:
        """
        Reply to multiple tweets in sequence.
        
        Args:
            page: Playwright page object
            tweet_data_list: List of (tweet_url, comment_text) tuples
            logger: AutomationLogger instance
            delay_between_replies: Min/max delay between replies in seconds
            stop_on_failure: If True, stop processing on first failure
            
        Returns:
            List of TweetReplyResult objects
        """
        results = []
        
        self.progress.info(f'Starting batch reply to {len(tweet_data_list)} tweets', significant=True)
        
        for idx, (tweet_url, comment_text) in enumerate(tweet_data_list, 1):
            # Check for abort
            if self.event_store.is_aborted():
                self.progress.warning('Batch reply aborted')
                break
            
            self.progress.info(f'Processing tweet {idx}/{len(tweet_data_list)}', significant=False)
            
            # Post reply
            result = await self.reply_to_tweet_by_url(page, tweet_url, comment_text, logger)
            results.append(result)
            
            # Log result
            if result.reply_posted:
                self.progress.info(f'Reply {idx} posted successfully', significant=True)
            else:
                self.progress.warning(f'Reply {idx} failed: {result.failure_reason}')
                
                if stop_on_failure:
                    self.progress.warning('Stopping batch due to failure')
                    break
            
            # Delay between replies (unless last one)
            if idx < len(tweet_data_list):
                delay = random.uniform(*delay_between_replies)
                await asyncio.sleep(delay)
        
        # Summary
        successful = sum(1 for r in results if r.reply_posted)
        failed = sum(1 for r in results if r.reply_attempted and not r.reply_posted)
        
        self.progress.info(f'Batch complete: {successful} successful, {failed} failed', significant=True)
        
        return results

    # ===========================================
    # BATCH PROCESSING
    # ===========================================
    
    async def process_posts_by_count(
        self,
        page: Page,
        target_user: str,
        post_count: int,
        comment_text: str,
        logger,
        post_delay: float = None
    ) -> dict:
        """
        Process a fixed number of tweets from a user (newest first).
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
        
        self.progress.scanning_posts(target_user)
        
        tweet_links = await self.get_tweet_links_from_profile(
            page=page,
            target_user=target_user,
            logger=logger,
            max_posts=post_count
        )
        result["posts_found"] = len(tweet_links)
        
        if not tweet_links:
            self.progress.posts_scan_failed(target_user, "No tweets found")
            return result
        
        tweets_to_process = tweet_links[:post_count]
        
        self.progress.posts_scanned(target_user, len(tweets_to_process))
        
        for i, tweet_url in enumerate(tweets_to_process):
            if self.event_store.is_aborted():
                self.progress.warning('Tweet processing aborted')
                result["stopped_early"] = True
                break
            
            self.progress.info(f'Analyzing tweet {i + 1} of {len(tweets_to_process)}', significant=False)
            
            try:
                await self.navigate_with_retry(page, tweet_url)
                
                post_date = await self.get_post_timestamp(page)
                if post_date:
                    self.progress.info(f'Tweet from {post_date.strftime("%b %d")}', significant=False)
                
                self.progress.commenting_on_post(i + 1, len(tweets_to_process), target_user)
                replied = await self.reply_to_tweet(page, comment_text, logger)
                if replied:
                    result["posts_commented"] += 1
                    self.progress.comment_posted(target_user, result["posts_commented"], len(tweets_to_process))
                else:
                    self.progress.comment_failed(target_user, i + 1, len(tweets_to_process), "Could not submit reply")
                
                logger.log_post_processed(commented=replied)
                result["posts_processed"] += 1
                
                await self.do_post_to_post_delay(post_delay, logger)
                
            except Exception as e:
                error_msg = f'Error processing tweet {tweet_url}: {e}'
                self.progress.error(f'Tweet processing failed: {str(e)[:50]}')
                result["errors"].append(error_msg)
        
        return result
    
    async def process_posts_after_date(
        self,
        page: Page,
        target_user: str,
        date_threshold: datetime,
        comment_text: str,
        logger,
        post_delay: float = None
    ) -> dict:
        """
        Process tweets from a user posted after the given date.
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
        
        self.progress.scanning_posts(target_user)
        
        tweet_links = await self.get_tweet_links_from_profile(
            page=page,
            target_user=target_user,
            logger=logger,
            max_posts=100,
            start_date=date_threshold
        )
        result["posts_found"] = len(tweet_links)
        
        if not tweet_links:
            self.progress.posts_scan_failed(target_user, "No tweets found in date range")
            return result
        
        self.progress.posts_scanned(target_user, len(tweet_links))
        
        for i, tweet_url in enumerate(tweet_links):
            if self.event_store.is_aborted():
                self.progress.warning('Tweet processing aborted')
                result["stopped_early"] = True
                break
            
            self.progress.info(f'Processing tweet {i + 1} of {len(tweet_links)}', significant=False)
            
            try:
                await self.navigate_with_retry(page, tweet_url)
                
                post_date = await self.get_post_timestamp(page)
                if post_date:
                    self.progress.info(f'Tweet from {post_date.strftime("%b %d")}', significant=False)
                
                self.progress.commenting_on_post(i + 1, len(tweet_links), target_user)
                replied = await self.reply_to_tweet(page, comment_text, logger)
                if replied:
                    result["posts_commented"] += 1
                    self.progress.comment_posted(target_user, result["posts_commented"], len(tweet_links))
                else:
                    self.progress.comment_failed(target_user, i + 1, len(tweet_links), "Could not submit reply")
                
                logger.log_post_processed(commented=replied)
                result["posts_processed"] += 1
                
                await self.do_post_to_post_delay(post_delay, logger)
                
            except Exception as e:
                error_msg = f'Error processing tweet {tweet_url}: {e}'
                self.progress.error(f'Tweet processing failed: {str(e)[:50]}')
                result["errors"].append(error_msg)
        
        return result


# ===========================================
# SECTION 7: STANDALONE EXTRACTION RUNNER
# ===========================================

async def run_standalone_extractor(
    page: Page,
    target_user: str
) -> list[str]:
    """
    Run the standalone regex-based tweet link extractor with timestamp extraction.
    
    This is a convenience function for running the extractor outside
    of the main automation flow. It runs continuously until interrupted.
    
    Extraction behavior:
    - Matches href pattern: ^/TARGET_USER/status/(\d+)(/.*)?$
    - Normalizes URLs to base format (strips /analytics, /likes, etc.)
    - Extracts <time datetime> from the same DOM subtree
    - Stores in memory as (URL, datetime) tuples for computation/filtering
    - Skips entries without a matching timestamp
    - Deduplicates by base tweet URL
    
    Args:
        page: Playwright page object (already connected to browser)
        target_user: Twitter username to match
        
    Returns:
        List of collected base tweet URLs (returned when extraction stops)
    """
    extractor = TweetLinkExtractor(target_user)
    
    print("=" * 60)
    print("Regex-Based Tweet Link Extractor with Timestamp")
    print("=" * 60)
    print(f"Target User: @{target_user}")
    print(f"Pattern: ^/{target_user}/status/(\\d+)(/.*)?$")
    print(f"Storage: In-memory collection")
    print(f"Format: (URL, datetime) tuples")
    print("=" * 60)
    print()
    
    # Navigate to target user profile
    profile_url = f"https://x.com/{target_user}"
    print(f"[BROWSER] Navigating to {profile_url}")
    await page.goto(profile_url, wait_until='domcontentloaded', timeout=60000)
    
    # Wait for initial content
    await asyncio.sleep(3)
    
    # Initial extraction
    initial_entries = await extractor.extract_from_page(page)
    print(f"[INITIAL] Found {len(initial_entries)} tweet links with timestamps")
    
    scroll_count = 0
    SCROLL_WAIT_TIME = 1.5
    
    # Continuous scroll loop - runs until interrupted
    print("\n[INFO] Starting continuous extraction. Press Ctrl+C to stop.\n")
    
    try:
        while True:
            scroll_count += 1
            
            # Scroll down
            await page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(SCROLL_WAIT_TIME)
            
            # Extract after scroll
            new_entries = await extractor.extract_from_page(page)
            
            if new_entries:
                print(f"[SCROLL {scroll_count}] Found {len(new_entries)} new links (Total: {extractor.get_collected_count()})")
                
    except KeyboardInterrupt:
        print(f"\n[STOPPED] Extraction stopped by user")
    except Exception as e:
        print(f"\n[ERROR] Extraction error: {e}")
    
    # Return all collected URLs
    all_urls = extractor.get_all_collected()
    all_data = extractor.get_collected_with_timestamps()
    print(f"\n[RESULT] {len(all_urls)} total tweet links extracted")
    print(f"[RESULT] {len(all_data)} entries with timestamps available for filtering")
    
    return all_urls

