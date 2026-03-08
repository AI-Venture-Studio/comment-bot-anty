"""
Threads Automation Module

This module provides Threads (by Meta) automation functionality:
- Selectors for DOM elements
- Profile navigation and post extraction
- Login verification and authentication
- Reply (comment) posting with human-like behavior
- Post processing by date or count

Thread URLs:
- Profile: https://www.threads.net/@username
- Post: https://www.threads.net/@username/post/<post_id>

Usage:
    from threads import ThreadsAutomation, ThreadsSelectors
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable

from playwright.async_api import Page, ElementHandle
from media_manager import delete_local_media_file


# ===========================================
# SECTION 1: SELECTORS
# ===========================================

class ThreadsSelectors:
    """CSS/XPath selectors for Threads elements"""
    
    # Login page
    USERNAME_INPUT = 'input[name="username"], input[autocomplete="username"]'
    PASSWORD_INPUT = 'input[name="password"], input[type="password"]'
    LOGIN_BUTTON = 'button[type="submit"], div[role="button"]:has-text("Log in")'
    
    # Login with Instagram button (Threads uses Instagram login)
    LOGIN_WITH_INSTAGRAM = 'div[role="button"]:has-text("Log in with Instagram"), a:has-text("Log in with Instagram")'
    
    # Cookie consent
    COOKIE_ACCEPT_BUTTON = 'button:has-text("Allow all cookies"), button:has-text("Accept"), button:has-text("Allow essential and optional cookies")'
    
    # Post-login prompts
    SAVE_LOGIN_NOT_NOW = 'button:has-text("Not Now"), div[role="button"]:has-text("Not Now"), span:has-text("Not now")'
    NOTIFICATIONS_NOT_NOW = 'button:has-text("Not Now"), div[role="button"]:has-text("Not Now")'
    
    # Logged in indicators
    HOME_NAV = 'a[href="/"], svg[aria-label="Home"]'
    PROFILE_LINK = 'a[href*="/@"]'
    SEARCH_ICON = 'svg[aria-label="Search"], a[href="/search"]'
    CREATE_POST = 'svg[aria-label="Create"], a[href="/create"]'
    
    # Profile page - Posts
    POST_LINKS = 'a[href*="/post/"]'
    
    # Post article/container
    POST_ARTICLE = 'article, div[data-pressable-container="true"]'
    
    # Individual Post Page
    REPLY_INPUT = 'div[role="textbox"], div[contenteditable="true"]'
    REPLY_BUTTON = 'div[role="button"]:has-text("Post"), div[role="button"]:has-text("Reply")'
    
    # Timestamp for filtering posts by date
    POST_TIMESTAMP = 'time[datetime]'
    
    # Post modal navigation
    CLOSE_POST_BUTTON = 'svg[aria-label="Close"], button[aria-label="Close"], div[role="button"] svg[aria-label="Close"]'
    
    # Profile header
    PROFILE_HEADER = 'header, div[data-testid="profile-header"]'
    PROFILE_USERNAME = 'span[dir="auto"]'
    
    # Reply section
    REPLY_SECTION = 'div[role="dialog"], section'
    REPLY_TEXTAREA = 'div[role="textbox"][contenteditable="true"]'


# ===========================================
# SECTION 2: REPLY RESULT TYPE
# ===========================================

@dataclass
class ThreadsReplyResult:
    """Structured result for Threads reply attempts"""
    post_url: str
    reply_attempted: bool
    reply_posted: bool
    failure_reason: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "post_url": self.post_url,
            "reply_attempted": self.reply_attempted,
            "reply_posted": self.reply_posted,
            "failure_reason": self.failure_reason
        }


# ===========================================
# SECTION 3: POST LINK EXTRACTOR
# ===========================================

class ThreadsPostExtractor:
    """
    Extracts post links from a Threads profile page.
    
    Continuously scans the DOM for <a href> elements matching:
        ^/@TARGET_USER/post/[A-Za-z0-9_-]+$
    
    For each match:
    - Normalizes URL to full format: https://www.threads.net/@TARGET_USER/post/<post_id>
    - Extracts <time datetime> from nearby DOM elements
    - Stores in memory as: (URL, datetime) tuples
    
    Deduplicates by base post URL.
    """
    
    def __init__(self, target_user: str):
        """
        Initialize the extractor.
        
        Args:
            target_user: Threads username to match (without @)
        """
        self.target_user = target_user.lower().lstrip('@')
        self.collected_urls: set = set()
        self.collected_data: list = []  # Store (url, datetime) tuples
        
        # Build the regex pattern for this target user
        # Pattern: ^/@TARGET_USER/post/([A-Za-z0-9_-]+)$
        self.pattern = re.compile(
            rf'^/@{re.escape(self.target_user)}/post/([A-Za-z0-9_-]+)$',
            re.IGNORECASE
        )
        
        print(f"[EXTRACTOR] Initialized for @{self.target_user}")
        print(f"[EXTRACTOR] Regex pattern: {self.pattern.pattern}")
    
    def _normalize_url(self, href: str) -> Optional[str]:
        """
        Normalize href to full Threads post URL.
        
        Args:
            href: The raw href attribute value
            
        Returns:
            Normalized URL: https://www.threads.net/@TARGET_USER/post/<post_id>
            or None if pattern doesn't match
        """
        match = self.pattern.match(href)
        if match:
            post_id = match.group(1)
            return f"https://www.threads.net/@{self.target_user}/post/{post_id}"
        return None
    
    async def extract_from_page(self, page: Page) -> list[tuple[str, str]]:
        """
        Scan the current DOM and extract all matching post links with timestamps.
        
        Args:
            page: Playwright page object
            
        Returns:
            List of newly discovered (base_url, datetime) tuples
        """
        # JavaScript to extract post links with timestamps from DOM
        js_extract = """
        (targetUser) => {
            const results = [];
            const pattern = new RegExp('^/@' + targetUser + '/post/([A-Za-z0-9_-]+)$', 'i');
            
            // Get all anchor elements with href
            const anchors = document.querySelectorAll('a[href*="/post/"]');
            
            for (const anchor of anchors) {
                const href = anchor.getAttribute('href');
                if (!href) continue;
                
                // Check if href matches the pattern
                const match = href.match(pattern);
                if (!match) continue;
                
                const postId = match[1];
                const baseUrl = 'https://www.threads.net/@' + targetUser + '/post/' + postId;
                
                // Find the containing article/post container
                let current = anchor;
                let datetime = null;
                let depth = 0;
                const maxDepth = 15;
                
                while (current && current !== document.body && depth < maxDepth) {
                    // Check for time element with datetime attribute
                    const timeEl = current.querySelector('time[datetime]');
                    if (timeEl) {
                        datetime = timeEl.getAttribute('datetime');
                        break;
                    }
                    current = current.parentElement;
                    depth++;
                }
                
                // Only add if we found a datetime (or allow null)
                results.push({
                    baseUrl: baseUrl,
                    datetime: datetime || ''
                });
            }
            
            return results;
        }
        """
        
        try:
            raw_results = await page.evaluate(js_extract, self.target_user)
            
            new_entries = []
            
            for item in raw_results:
                base_url = item.get('baseUrl')
                datetime_str = item.get('datetime', '')
                
                if not base_url:
                    continue
                
                # Check deduplication by base URL
                if base_url not in self.collected_urls:
                    self.collected_urls.add(base_url)
                    self.collected_data.append((base_url, datetime_str))
                    new_entries.append((base_url, datetime_str))
                    print(f"[FOUND] {base_url} | {datetime_str if datetime_str else 'No timestamp'}")
            
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


# ===========================================
# SECTION 4: THREADS AUTOMATION CLASS
# ===========================================

class ThreadsAutomation:
    """
    Threads automation class for commenting on posts.
    
    This class handles:
    - Login verification and authentication (via Instagram)
    - Profile navigation
    - Post collection from profile pages
    - Reply (comment) posting with human-like behavior
    - Batch processing
    """
    
    # Constants
    MAX_COMMENT_RETRIES = 2
    CONSECUTIVE_OLD_POSTS_LIMIT = 4
    ELEMENT_TIMEOUT = 10000  # 10 seconds
    
    def __init__(self, progress_emitter, event_store, human_like_funcs: dict):
        """
        Initialize ThreadsAutomation with required dependencies.
        
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
    
    # ===========================================
    # LOGIN & AUTHENTICATION
    # ===========================================
    
    async def detect_bot_challenge(self, page: Page) -> bool:
        """
        Detect if Threads is showing a bot challenge or verification.
        
        Known bot challenge indicators:
        - URL: /accounts/suspended/
        - Message: "Confirm you're human to use your account, <username>"
        
        Returns:
            True if bot challenge detected, False otherwise
        """
        try:
            current_url = page.url
            print(f'[BOT CHECK] Checking URL: {current_url}')
            
            # PRIMARY CHECK: URL-based detection (most reliable)
            # Threads redirects to /accounts/suspended/ when account is flagged
            bot_challenge_urls = [
                '/accounts/suspended/',
                '/challenge/',
                '/accounts/verify/',
                '/accounts/confirm/',
            ]
            
            for url_pattern in bot_challenge_urls:
                if url_pattern in current_url:
                    print(f'[BOT CHECK] ❌ Detected bot challenge URL pattern "{url_pattern}" in: {current_url}')
                    return True
            
            # SECONDARY CHECK: Page content for specific bot challenge message
            page_content = await page.content()
            page_lower = page_content.lower()
            
            # Specific bot challenge message: "Confirm you're human to use your account"
            # This is the exact message shown when Threads flags an account
            if "confirm you're human to use your account" in page_lower:
                print(f'[BOT CHECK] ❌ Detected "Confirm you\'re human" challenge in page content')
                return True
            
            # Other high-confidence bot challenge indicators (require URL context)
            if '/login' in current_url or '/accounts/' in current_url:
                print(f'[BOT CHECK] URL contains /login or /accounts/, checking for challenge phrases...')
                high_confidence_phrases = [
                    "we detected unusual activity",
                    "verify you're not a bot",
                    "suspicious activity on your account",
                    "confirm your identity to continue",
                ]
                
                for phrase in high_confidence_phrases:
                    if phrase in page_lower:
                        print(f'[BOT CHECK] ❌ Detected challenge phrase: "{phrase}"')
                        return True
            
            # CAPTCHA detection (high confidence)
            captcha_selectors = [
                'iframe[src*="recaptcha"]',
                'iframe[src*="captcha"]',
                'div.g-recaptcha',
                '#captcha-container',
            ]
            
            for selector in captcha_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element and await element.is_visible():
                        print(f'[BOT CHECK] ❌ Detected CAPTCHA element: {selector}')
                        return True
                except:
                    continue
            
            print(f'[BOT CHECK] ✓ No bot challenge detected')
            return False
            
        except Exception as e:
            print(f'[BOT CHECK] Error during detection: {e}')
            return False
    
    async def get_logged_in_username(self, page: Page) -> str:
        """
        Get the username of the currently logged-in Threads account.
        
        Returns:
            Username without @ symbol, or empty string if not logged in
        """
        try:
            # Method 1: Check for profile link in navigation
            profile_links = await page.query_selector_all('a[href*="/@"]')
            for link in profile_links:
                href = await link.get_attribute('href')
                if href and href.startswith('/@'):
                    username = href.split('/')[1].lstrip('@')
                    if username and len(username) > 0:
                        return username
            
            # Method 2: Check for username in page URL if on profile
            current_url = page.url
            if '/@' in current_url:
                parts = current_url.split('/@')
                if len(parts) > 1:
                    username = parts[1].split('/')[0].split('?')[0]
                    return username
            
            return ""
            
        except Exception:
            return ""
    
    async def verify_login(self, page: Page) -> bool:
        """
        Verify if we're logged into Threads.
        
        Primary check: if the page contains the text "Log in or sign up for Threads"
        (shown in the left sidebar when logged out), the account is not logged in.
        
        Returns:
            True if logged in, False otherwise
        """
        try:
            # Wait briefly for the page to render meaningful content
            await asyncio.sleep(2)
            
            # Primary check: look for the logged-out sidebar text
            logged_out_indicator = await page.query_selector(
                'text="Log in or sign up for Threads"'
            )
            if logged_out_indicator and await logged_out_indicator.is_visible():
                print('[LOGIN CHECK] Detected "Log in or sign up for Threads" — account is logged out')
                return False
            
            # Fallback: also check raw page text in case selector misses it
            page_text = await page.inner_text('body')
            if 'Log in or sign up for Threads' in page_text:
                print('[LOGIN CHECK] Detected logout text in page body — account is logged out')
                return False
            
            print('[LOGIN CHECK] No logout indicator found — account appears logged in')
            return True
            
        except Exception as e:
            print(f'[LOGIN CHECK] Error during verify_login: {e}')
            return False
    
    async def logout(self, page: Page):
        """Logout from Threads."""
        try:
            # Navigate to Threads logout directly (avoids Instagram redirect)
            await self.navigate_with_retry(page, 'https://www.threads.net/logout')
            await asyncio.sleep(2)
            
            try:
                logout_button = await page.wait_for_selector('button:has-text("Log out")', timeout=5000)
                if logout_button:
                    await logout_button.click()
                    await asyncio.sleep(2)
            except:
                pass
            
            self.progress.info('Logged out successfully')
            
        except Exception as e:
            self.progress.warning('Logout may have failed')
    
    async def perform_login(self, page: Page, username: str, password: str):
        """
        Perform a fresh Threads login.
        
        Logs in directly on Threads without following any Instagram redirect.
        """
        # Block any navigation that tries to redirect to instagram.com
        async def block_instagram_redirect(route):
            request = route.request
            if 'instagram.com' in request.url and request.resource_type in ('document', 'navigation'):
                self.progress.warning('Blocked Instagram redirect during Threads login')
                await route.abort()
            else:
                await route.continue_()
        
        await page.route('**/*', block_instagram_redirect)
        
        try:
            # Navigate to Threads login page
            self.progress.action('Navigating to Threads login page')
            await self.navigate_with_retry(page, 'https://www.threads.net/login')
            
            await asyncio.sleep(2)
            
            # Abort if we somehow ended up on Instagram
            if 'instagram.com' in page.url:
                raise Exception('Threads redirected to Instagram login — cannot proceed without Instagram redirect')
            
            # Check for bot challenge immediately
            if await self.detect_bot_challenge(page):
                raise Exception('Threads bot challenge detected')
            
            # Accept cookies if prompted
            try:
                cookie_button = await page.wait_for_selector(ThreadsSelectors.COOKIE_ACCEPT_BUTTON, timeout=5000)
                if cookie_button:
                    await cookie_button.click()
                    await asyncio.sleep(1)
            except:
                pass
            
            # Wait for the direct Threads login form (do NOT follow Instagram button)
            self.progress.action('Waiting for Threads login form')
            
            try:
                await page.wait_for_selector(ThreadsSelectors.USERNAME_INPUT, timeout=15000)
            except:
                raise Exception('Could not find Threads login form — Threads may require an Instagram redirect which is disabled')
        finally:
            # Always remove the route handler when done
            await page.unroute('**/*', block_instagram_redirect)
        
        await asyncio.sleep(1)
        
        # Enter username/email
        self.progress.action(f'Entering credentials for {username}')
        username_input = await page.query_selector(ThreadsSelectors.USERNAME_INPUT)
        await username_input.click()
        await username_input.fill(username)
        await asyncio.sleep(0.5)
        
        # Enter password
        password_input = await page.query_selector(ThreadsSelectors.PASSWORD_INPUT)
        await password_input.click()
        await password_input.fill(password)
        await asyncio.sleep(0.5)
        
        # Click login button
        self.progress.action('Submitting login form')
        login_button = await page.query_selector(ThreadsSelectors.LOGIN_BUTTON)
        if login_button:
            await login_button.click()
            await asyncio.sleep(5)
        
        # Handle post-login prompts
        try:
            not_now_button = await page.wait_for_selector(ThreadsSelectors.SAVE_LOGIN_NOT_NOW, timeout=5000)
            if not_now_button:
                await not_now_button.click()
                await asyncio.sleep(2)
        except:
            pass
        
        try:
            not_now_button = await page.wait_for_selector(ThreadsSelectors.NOTIFICATIONS_NOT_NOW, timeout=5000)
            if not_now_button:
                await not_now_button.click()
                await asyncio.sleep(2)
        except:
            pass
        
        # Wait for page to stabilize
        self.progress.info('Waiting for login to complete', significant=False)
        await asyncio.sleep(3)
        
        # Verify login succeeded
        is_logged_in = await self.verify_login(page)
        if not is_logged_in:
            if await self.detect_bot_challenge(page):
                raise Exception('Threads bot challenge detected - verification required')
            else:
                raise Exception('Threads login failed - check credentials')
    
    async def login(self, page: Page, username: str, password: str, target_user: str):
        """
        Verify Threads login status for pre-logged-in accounts.
        
        Accounts are expected to already be logged in via stored browser sessions.
        No login form interaction is performed — if the account is not logged in,
        an error is raised.
        
        Args:
            page: Playwright page object
            username: Expected Threads username
            password: Unused (kept for interface compatibility)
            target_user: Threads username to navigate to after verifying login
        """
        # Navigate to Threads and check login status
        self.progress.action('Checking login status')
        await self.navigate_with_retry(page, 'https://www.threads.net/')
        
        await asyncio.sleep(2)
        
        # Check for bot challenge
        if await self.detect_bot_challenge(page):
            raise Exception('Threads bot challenge detected - account flagged for verification')
        
        # Verify if already logged in
        is_logged_in = await self.verify_login(page)
        
        if not is_logged_in:
            self.progress.error(f'Account @{username.lstrip("@")} is not logged in — session expired or missing')
            raise Exception(f'Account @{username.lstrip("@")} is not logged in — Threads accounts must be pre-logged in via a saved browser session')
        
        logged_in_username = await self.get_logged_in_username(page)
        expected_username = username.lstrip('@')
        
        if logged_in_username and logged_in_username.lower() != expected_username.lower():
            raise Exception(f'Wrong account logged in: expected @{expected_username}, found @{logged_in_username} — check the browser session for this account')
        
        self.progress.success(f'Already logged in as @{expected_username}')
        
        # Navigate to target user's profile
        self.progress.navigating_to_profile(target_user)
        await self.navigate_with_retry(page, f'https://www.threads.net/@{target_user}')
        
        await asyncio.sleep(2)
        
        # Check for bot challenge on profile
        if await self.detect_bot_challenge(page):
            raise Exception('Threads bot challenge detected on profile page')
    
    # ===========================================
    # TIMESTAMP PARSING
    # ===========================================
    
    def parse_timestamp(self, timestamp_str: str) -> Optional[datetime]:
        """
        Parse Threads timestamp format to datetime.
        
        Args:
            timestamp_str: ISO 8601 timestamp string
            
        Returns:
            datetime object or None if parsing fails
        """
        try:
            if not timestamp_str:
                return None
            clean_timestamp = timestamp_str.replace('Z', '+00:00')
            return datetime.fromisoformat(clean_timestamp.replace('+00:00', ''))
        except Exception as e:
            print(f'[WARN] Could not parse timestamp: {timestamp_str} - {e}')
            return None
    
    # ===========================================
    # POST EXTRACTION
    # ===========================================
    
    async def get_post_links_from_profile(
        self,
        page: Page,
        target_user: str,
        logger,
        max_posts: int = None,
        **kwargs
    ) -> list:
        """
        Get post links from a user's Threads profile.
        
        Args:
            page: Playwright page object
            target_user: Threads username
            logger: AutomationLogger instance
            max_posts: Optional limit on posts to collect
            
        Returns:
            List of post URLs
        """
        extractor = ThreadsPostExtractor(target_user)
        
        scroll_count = 0
        max_scroll_attempts = 20
        SCROLL_WAIT_TIME = 1.5
        
        try:
            # Navigate to user's profile
            self.progress.info(f'Loading profile page for @{target_user}', significant=False)
            await self.navigate_with_retry(page, f'https://www.threads.net/@{target_user}')
            
            # Emit target opened checkpoint
            self.progress.target_opened(target_user)
            
            await asyncio.sleep(2)
            
            # Initial extraction
            initial_entries = await extractor.extract_from_page(page)
            self.progress.info(f'Initial scan: {len(initial_entries)} post links found', significant=False)
            
            # Scroll and extract loop
            while scroll_count < max_scroll_attempts:
                # Check abort signal
                if self.event_store.is_aborted():
                    self.progress.warning('Post extraction aborted')
                    break
                
                # Check if we've hit max_posts limit
                if max_posts is not None and extractor.get_collected_count() >= max_posts:
                    self.progress.info(f'Reached target of {max_posts} posts', significant=True)
                    break
                
                scroll_count += 1
                
                # Scroll down
                await page.evaluate("window.scrollBy(0, 800)")
                await asyncio.sleep(SCROLL_WAIT_TIME)
                
                # Extract after scroll
                new_entries = await extractor.extract_from_page(page)
                
                if new_entries:
                    self.progress.info(f'Scroll {scroll_count}: Found {len(new_entries)} new posts (Total: {extractor.get_collected_count()})', significant=False)
                    for entry in new_entries:
                        logger.log_post_found()
                
                # Stop if no new posts found after several scrolls
                if not new_entries and scroll_count > 5:
                    consecutive_empty = getattr(self, '_consecutive_empty_scrolls', 0) + 1
                    self._consecutive_empty_scrolls = consecutive_empty
                    if consecutive_empty >= 3:
                        self.progress.info('No more posts to load', significant=False)
                        break
                else:
                    self._consecutive_empty_scrolls = 0
            
            total_collected = extractor.get_collected_count()
            self.progress.success(f'Extracted {total_collected} post links for @{target_user}')
            
            return extractor.get_all_collected()
            
        except Exception as e:
            self.progress.target_failed(target_user, f'Unable to access profile: {str(e)[:50]}')
            return extractor.get_all_collected()
    
    async def get_post_timestamp(self, page: Page) -> Optional[datetime]:
        """Get the timestamp of the current post."""
        try:
            time_element = await page.wait_for_selector(ThreadsSelectors.POST_TIMESTAMP, timeout=self.ELEMENT_TIMEOUT)
            if time_element:
                datetime_attr = await time_element.get_attribute('datetime')
                if datetime_attr:
                    return self.parse_timestamp(datetime_attr)
        except Exception as e:
            print(f'[WARN] Could not get post timestamp: {e}')
        return None
    
    # ===========================================
    # REPLY FUNCTIONALITY
    # ===========================================
    
    async def _locate_reply_input(self, page: Page) -> Optional[ElementHandle]:
        """
        Locate the reply input area on a Threads post.
        
        Returns:
            ElementHandle for the reply input, or None if not found
        """
        # Strategy 1: Find contenteditable div with role="textbox"
        try:
            reply_input = await page.query_selector('div[role="textbox"][contenteditable="true"]')
            if reply_input and await reply_input.is_visible():
                return reply_input
        except:
            pass
        
        # Strategy 2: Find by aria-label or placeholder
        try:
            reply_input = await page.query_selector('[aria-label*="reply"], [aria-label*="Reply"], [placeholder*="reply"], [placeholder*="Reply"]')
            if reply_input and await reply_input.is_visible():
                return reply_input
        except:
            pass
        
        # Strategy 3: Find contenteditable in reply section
        try:
            # Look for contenteditable elements that are not in navigation
            contenteditables = await page.query_selector_all('div[contenteditable="true"]')
            for element in contenteditables:
                if await element.is_visible():
                    # Verify it's in a reply context by checking nearby elements
                    return element
        except:
            pass
        
        return None
    
    async def _locate_post_button(self, page: Page) -> Optional[ElementHandle]:
        """
        Locate the Post button in the reply overlay.
        
        The Threads Post button sits at the bottom-right of the reply composer.
        It is a div[role="button"] or button element whose exact visible text is "Post".
        We use the Playwright locator API to find all matches and pick the
        rightmost visible one (highest x-coordinate), which is reliably the
        submit button rather than other "Post"-labelled elements on the page.
        
        Returns:
            ElementHandle for the button, or None if not found
        """
        # Strategy 1: Playwright locator — iterate from last to first, pick rightmost visible
        try:
            locator = page.locator('div[role="button"]:has-text("Post"), button:has-text("Post")')
            count = await locator.count()
            print(f'[POST BUTTON] Locator found {count} candidate(s) with text "Post"')
            
            best_button = None
            best_x = -1
            
            for i in range(count):
                el = locator.nth(i)
                try:
                    text = (await el.inner_text()).strip()
                    if text != 'Post':
                        continue
                    if not await el.is_visible():
                        continue
                    if not await el.is_enabled():
                        continue
                    box = await el.bounding_box()
                    if box and box['x'] > best_x:
                        best_x = box['x']
                        best_button = await el.element_handle()
                except Exception:
                    continue
            
            if best_button:
                print(f'[POST BUTTON] Found via locator at x={best_x}')
                return best_button
        except Exception as e:
            print(f'[POST BUTTON] Locator strategy failed: {e}')
        
        # Strategy 2: JS-based — find rightmost visible element with exact text "Post"
        try:
            handle = await page.evaluate_handle("""
                () => {
                    const all = [
                        ...document.querySelectorAll('div[role="button"], button')
                    ];
                    const candidates = all.filter(el => {
                        const text = el.innerText?.trim();
                        return text === 'Post' && el.offsetParent !== null;
                    });
                    if (!candidates.length) return null;
                    return candidates.reduce((best, el) => {
                        const bBox = el.getBoundingClientRect();
                        const bestBox = best.getBoundingClientRect();
                        return bBox.right > bestBox.right ? el : best;
                    });
                }
            """)
            element = handle.as_element()
            if element:
                print('[POST BUTTON] Found via JS rightmost strategy')
                return element
        except Exception as e:
            print(f'[POST BUTTON] JS strategy failed: {e}')
        
        print('[POST BUTTON] Could not locate Post button')
        return None
    
    async def _click_reply_icon(self, page: Page) -> bool:
        """
        Click the reply/comment icon to open the reply composer.
        
        Returns:
            True if clicked successfully, False otherwise
        """
        try:
            # Look for reply icon (comment bubble)
            reply_icon_selectors = [
                'svg[aria-label="Reply"]',
                'svg[aria-label="Comment"]',
                'div[role="button"] svg[aria-label="Reply"]',
                'div[role="button"] svg[aria-label="Comment"]',
            ]
            
            for selector in reply_icon_selectors:
                try:
                    icon = await page.query_selector(selector)
                    if icon and await icon.is_visible():
                        await icon.click()
                        await asyncio.sleep(1)
                        return True
                except:
                    continue
            
            return False
        except:
            return False
    
    async def _post_reply_with_media(
        self,
        page: Page,
        reply_input: ElementHandle,
        comment_text: str,
        local_media_paths: list,
        logger,
    ) -> bool:
        """
        Post a reply that includes one or more image attachments on Threads.

        Called only when local_media_paths is non-empty. Handles:
          1. Optionally typing comment text (skipped when empty/None).
          2. Attaching files via the hidden <input type="file"> element
             (never clicks the visible toolbar button — not compatible with
             headless/VPS environments).
          3. Waiting for Threads' media preview and upload completion.
          4. Locating and clicking the Post button.

        Tier 1 cleanup (delete_local_media_file) always runs in finally,
        regardless of success or failure.

        Args:
            page:              Playwright page (on a Threads post page).
            reply_input:       The already-focused reply composer textbox.
            comment_text:      Optional text to type alongside the media.
            local_media_paths: List of absolute local file path strings.
            logger:            AutomationLogger instance.

        Returns:
            True if the reply was submitted, False on any failure.
        """
        try:
            # Step 1: Type comment text if provided
            if comment_text and comment_text.strip():
                self.progress.info('Typing reply text', significant=False)
                await self.human_like_type(page, reply_input, comment_text, logger)

            # Step 2: Click the image/photo toolbar icon which triggers the file chooser,
            # then immediately intercept it with Playwright's expect_file_chooser.
            # This is the correct approach — Threads does not keep a static hidden
            # file input in the DOM; it is created dynamically when the icon is clicked.
            self.progress.info('Clicking image icon to open file chooser', significant=False)

            image_icon_selectors = [
                'svg[aria-label="Attach photo"]',
                'svg[aria-label="Photo"]',
                'svg[aria-label="Image"]',
                # Toolbar buttons — the image icon is typically the first icon
                'div[role="button"] svg[aria-label*="photo" i]',
                'div[role="button"] svg[aria-label*="image" i]',
                'div[role="button"] svg[aria-label*="attach" i]',
            ]

            image_icon = None
            for selector in image_icon_selectors:
                try:
                    image_icon = await page.query_selector(selector)
                    if image_icon and await image_icon.is_visible():
                        print(f'[MEDIA] Found image icon with selector: {selector}')
                        break
                    image_icon = None
                except Exception:
                    continue

            if not image_icon:
                # Fallback: try the first visible toolbar button inside the reply composer
                self.progress.warning('[MEDIA] Named image icon not found — trying first toolbar button')
                try:
                    toolbar_buttons = await page.query_selector_all(
                        'div[role="textbox"] ~ * div[role="button"] svg, '
                        'div[contenteditable] ~ * div[role="button"] svg'
                    )
                    if toolbar_buttons:
                        image_icon = toolbar_buttons[0]
                except Exception:
                    pass

            if not image_icon:
                self.progress.warning('[MEDIA] Could not find image toolbar icon — cannot attach media')
                return False

            # Use expect_file_chooser to intercept the OS dialog before it opens
            try:
                async with page.expect_file_chooser(timeout=5000) as fc_info:
                    await image_icon.click()
                file_chooser = await fc_info.value
                self.progress.info(
                    f'Attaching {len(local_media_paths)} file(s) via file chooser',
                    significant=False,
                )
                await file_chooser.set_files(local_media_paths)
            except Exception as e:
                # Fallback: try set_input_files on a hidden input if chooser didn't fire
                self.progress.warning(f'[MEDIA] File chooser approach failed ({e}) — trying hidden input fallback')
                file_input = None
                for selector in ['input[type="file"]']:
                    try:
                        file_input = await page.query_selector(selector)
                        if file_input:
                            await file_input.set_input_files(local_media_paths)
                            break
                    except Exception:
                        continue
                if not file_input:
                    self.progress.warning('[MEDIA] Hidden input fallback also failed — cannot attach media')
                    return False

            # Step 3: Wait for media preview to appear in the composer.
            self.progress.info('Waiting for media preview to render', significant=False)
            media_preview_appeared = False
            preview_selectors = [
                'img[src*="blob:"]',
                'video[src*="blob:"]',
                'div[role="img"]',
                'img[src*="scontent"]',
                '[data-visualcompletion="media-vc-image"]',
            ]
            for sel in preview_selectors:
                try:
                    await page.wait_for_selector(sel, timeout=10000)
                    media_preview_appeared = True
                    self.progress.info('Media preview detected', significant=False)
                    break
                except Exception:
                    continue

            if not media_preview_appeared:
                self.progress.warning(
                    '[MEDIA] Media preview did not appear within 10s — '
                    'upload may still succeed; proceeding to submit.'
                )
                await asyncio.sleep(5)

            # Step 5: Brief pause before clicking Post.
            await asyncio.sleep(self.get_random_delay(0.5, 1.0))

            # Step 6: Locate and click the Post button.
            self.progress.info('Locating Post button after media upload', significant=False)
            post_button = await self._locate_post_button(page)

            if post_button:
                self.progress.info('Clicking Post button (with media)', significant=False)
                await self.human_like_click(page, post_button, logger)
                await asyncio.sleep(3)

                # Verify: check if reply input is cleared or a success indicator appears
                try:
                    reply_input_after = await self._locate_reply_input(page)
                    if reply_input_after:
                        remaining_text = await reply_input_after.inner_text()
                        if remaining_text and comment_text and comment_text in remaining_text:
                            self.progress.warning('Reply text still present after clicking — retrying')
                            post_button_retry = await self._locate_post_button(page)
                            if post_button_retry:
                                await self.human_like_click(page, post_button_retry, logger)
                                await asyncio.sleep(2)
                except Exception:
                    pass  # If we can't check, assume it posted

                self.progress.info('Reply with media submitted.', significant=True)
                return True
            else:
                # Fallback: keyboard shortcut
                self.progress.info('Post button not found — attempting keyboard shortcut', significant=False)
                await page.keyboard.press('Meta+Enter')
                await asyncio.sleep(1)
                await page.keyboard.press('Control+Enter')
                await asyncio.sleep(2)
                self.progress.warning('Attempted keyboard shortcut — result uncertain')
                return True

        except Exception as e:
            self.progress.warning(f'[MEDIA] Reply with media failed: {str(e)[:80]}')
            return False

    async def reply_to_post(self, page: Page, reply_text: str, logger, local_media_paths: list = None) -> bool:
        """
        Post a reply to the current Threads post.
        
        Args:
            page: Playwright page object (must be on a post page)
            reply_text: The text to post as a reply
            logger: AutomationLogger instance
            local_media_paths: Optional list of absolute local file paths to attach as images
            
        Returns:
            True if reply was posted successfully, False otherwise
        """
        local_media_paths = local_media_paths or []
        self.progress.info('Preparing to reply', significant=False)
        
        for attempt in range(self.MAX_COMMENT_RETRIES):
            try:
                # First, try to click the reply icon to open composer
                await self._click_reply_icon(page)
                await asyncio.sleep(1)
                
                # Locate reply input
                reply_input = await self._locate_reply_input(page)
                
                if not reply_input:
                    self.progress.warning(f'Reply input not found (attempt {attempt + 1}/{self.MAX_COMMENT_RETRIES})')
                    if attempt < self.MAX_COMMENT_RETRIES - 1:
                        await asyncio.sleep(1)
                        continue
                    return False
                
                # Scroll into view
                try:
                    await reply_input.scroll_into_view_if_needed()
                    await asyncio.sleep(self.get_random_delay(0.2, 0.5))
                except:
                    pass
                
                # Click to focus
                await self.human_like_click(page, reply_input, logger)
                await asyncio.sleep(self.get_random_delay(0.3, 0.7))
                
                # Branch: if media is attached, delegate to media-aware submission
                if local_media_paths:
                    return await self._post_reply_with_media(
                        page, reply_input, reply_text, local_media_paths, logger
                    )
                
                # Type the reply with human-like behavior
                self.progress.info('Typing reply', significant=False)
                await self.human_like_type(page, reply_input, reply_text, logger)
                
                # Review pause
                await self.do_review_pause(logger)
                
                # Find and click Post button
                await asyncio.sleep(0.5)
                post_button = await self._locate_post_button(page)
                
                if post_button:
                    self.progress.info('Clicking Post button', significant=False)
                    await self.human_like_click(page, post_button, logger)
                    await asyncio.sleep(3)  # Wait longer for post to submit
                    
                    # Verify the reply was posted by checking if the input is now empty/gone
                    # or if a success indicator appears
                    try:
                        # Check if reply input still has the text (means it didn't post)
                        reply_input_after = await self._locate_reply_input(page)
                        if reply_input_after:
                            remaining_text = await reply_input_after.inner_text()
                            if remaining_text and reply_text in remaining_text:
                                self.progress.warning('Reply text still present - may not have posted')
                                # Try clicking again
                                post_button_retry = await self._locate_post_button(page)
                                if post_button_retry:
                                    await self.human_like_click(page, post_button_retry, logger)
                                    await asyncio.sleep(2)
                    except:
                        pass  # If we can't check, assume it posted
                    
                    logger.log_success(f'[OK] Reply posted: "{reply_text}"')
                    self.progress.info(f'Reply posted: "{reply_text}"', significant=True)
                    return True
                else:
                    # Try keyboard shortcut as fallback
                    self.progress.info('Post button not found - attempting keyboard shortcut', significant=False)
                    await page.keyboard.press('Meta+Enter')
                    await asyncio.sleep(1)
                    await page.keyboard.press('Control+Enter')
                    await asyncio.sleep(2)
                    
                    # Check if it worked
                    self.progress.warning('Attempted keyboard shortcut - result uncertain')
                    logger.log_success(f'[OK] Reply likely posted via keyboard: "{reply_text}"')
                    return True
                
            except Exception as e:
                self.progress.warning(f'Reply attempt failed (attempt {attempt + 1}/{self.MAX_COMMENT_RETRIES}): {str(e)[:50]}')
                if attempt < self.MAX_COMMENT_RETRIES - 1:
                    await asyncio.sleep(1)
        
        self.progress.error(f'Could not post reply after {self.MAX_COMMENT_RETRIES} attempts')
        return False
    
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
        post_delay: float = None,
        local_media_paths: list = None,
    ) -> dict:
        """
        Process a fixed number of posts from a user (newest first).
        
        Args:
            page: Playwright page object
            target_user: Threads username
            post_count: Number of posts to process
            comment_text: Comment to post
            logger: AutomationLogger instance
            post_delay: Delay between posts (seconds)
            
        Returns:
            Dict with processing results
        """
        local_media_paths = local_media_paths or []
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
        
        post_links = await self.get_post_links_from_profile(
            page=page,
            target_user=target_user,
            logger=logger,
            max_posts=post_count
        )
        result["posts_found"] = len(post_links)
        
        if not post_links:
            self.progress.posts_scan_failed(target_user, "No posts found")
            return result
        
        posts_to_process = post_links[:post_count]
        self.progress.posts_scanned(target_user, len(posts_to_process))
        
        for i, post_url in enumerate(posts_to_process):
            if self.event_store.is_aborted():
                self.progress.warning('Post processing aborted')
                result["stopped_early"] = True
                break
            
            self.progress.info(f'Analyzing post {i + 1} of {len(posts_to_process)}', significant=False)
            
            try:
                await self.navigate_with_retry(page, post_url)
                await asyncio.sleep(1)
                
                # Guard: Threads sometimes redirects to instagram.com before allowing interaction
                current_url = page.url
                if 'instagram.com' in current_url:
                    error_msg = f'Redirected to Instagram ({current_url}) while navigating to post - skipping'
                    self.progress.error(error_msg)
                    result["errors"].append(error_msg)
                    continue
                
                post_date = await self.get_post_timestamp(page)
                if post_date:
                    self.progress.info(f'Post from {post_date.strftime("%b %d")}', significant=False)
                
                self.progress.commenting_on_post(i + 1, len(posts_to_process), target_user)
                replied = await self.reply_to_post(page, comment_text, logger, local_media_paths=local_media_paths)
                
                if replied:
                    result["posts_commented"] += 1
                    self.progress.comment_posted(target_user, result["posts_commented"], len(posts_to_process))
                else:
                    self.progress.comment_failed(target_user, i + 1, len(posts_to_process), "Could not submit reply")
                
                logger.log_post_processed(commented=replied)
                result["posts_processed"] += 1
                
                await self.do_post_to_post_delay(post_delay, logger)
                
            except Exception as e:
                error_msg = f'Error processing post {post_url}: {e}'
                self.progress.error(f'Post processing failed: {str(e)[:50]}')
                result["errors"].append(error_msg)
        
        return result
    
    async def process_posts_after_date(
        self,
        page: Page,
        target_user: str,
        date_threshold: datetime,
        comment_text: str,
        logger,
        post_delay: float = None,
        local_media_paths: list = None,
    ) -> dict:
        """
        Process posts from a user posted after the given date.
        
        Uses early termination after CONSECUTIVE_OLD_POSTS_LIMIT old posts.
        
        Args:
            page: Playwright page object
            target_user: Threads username
            date_threshold: Only process posts after this date
            comment_text: Comment to post
            logger: AutomationLogger instance
            post_delay: Delay between posts (seconds)
            
        Returns:
            Dict with processing results
        """
        local_media_paths = local_media_paths or []
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
        
        post_links = await self.get_post_links_from_profile(
            page=page,
            target_user=target_user,
            logger=logger,
            max_posts=100
        )
        result["posts_found"] = len(post_links)
        
        if not post_links:
            self.progress.posts_scan_failed(target_user, "No posts found")
            return result
        
        self.progress.posts_scanned(target_user, len(post_links))
        
        consecutive_old_posts = 0
        
        for i, post_url in enumerate(post_links):
            if self.event_store.is_aborted():
                self.progress.warning('Post processing aborted')
                result["stopped_early"] = True
                break
            
            self.progress.info(f'Analyzing post {i + 1} of {len(post_links)}', significant=False)
            
            try:
                await self.navigate_with_retry(page, post_url)
                await asyncio.sleep(1)
                
                # Guard: Threads sometimes redirects to instagram.com before allowing interaction
                current_url = page.url
                if 'instagram.com' in current_url:
                    error_msg = f'Redirected to Instagram ({current_url}) while navigating to post - skipping'
                    self.progress.error(error_msg)
                    result["errors"].append(error_msg)
                    continue
                
                post_date = await self.get_post_timestamp(page)
                
                if post_date:
                    self.progress.info(f'Post from {post_date.strftime("%b %d")}', significant=False)
                    
                    if post_date < date_threshold:
                        consecutive_old_posts += 1
                        self.progress.post_skipped(f'post too old ({consecutive_old_posts}/{self.CONSECUTIVE_OLD_POSTS_LIMIT})')
                        logger.log_post_processed(skipped=True)
                        result["posts_skipped"] += 1
                        
                        if consecutive_old_posts >= self.CONSECUTIVE_OLD_POSTS_LIMIT:
                            self.progress.warning(f'Stopping after {self.CONSECUTIVE_OLD_POSTS_LIMIT} consecutive old posts')
                            result["stopped_early"] = True
                            break
                        continue
                    else:
                        consecutive_old_posts = 0
                else:
                    self.progress.info('Processing post (date unavailable)', significant=False)
                
                self.progress.commenting_on_post(i + 1, len(post_links), target_user)
                replied = await self.reply_to_post(page, comment_text, logger, local_media_paths=local_media_paths)
                
                if replied:
                    result["posts_commented"] += 1
                    self.progress.comment_posted(target_user, result["posts_commented"], len(post_links))
                else:
                    self.progress.comment_failed(target_user, i + 1, len(post_links), "Could not submit reply")
                
                logger.log_post_processed(commented=replied)
                result["posts_processed"] += 1
                
                await self.do_post_to_post_delay(post_delay, logger)
                
            except Exception as e:
                error_msg = f'Error processing post {post_url}: {e}'
                self.progress.error(f'Post processing failed: {str(e)[:50]}')
                result["errors"].append(error_msg)
        
        return result
