"""
Instagram Automation Module

This module contains all Instagram-specific logic for the comment bot:
- Selectors for Instagram DOM elements
- Login/logout functionality
- Bot challenge detection
- Profile navigation
- Post extraction and commenting
- Post processing (by date or count)

This module is designed to be used by the main app.py orchestrator.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional
from playwright.async_api import Page


# ===========================================
# INSTAGRAM SELECTORS
# ===========================================

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


# ===========================================
# INSTAGRAM AUTOMATION CLASS
# ===========================================

class InstagramAutomation:
    """
    Instagram automation class that handles all Instagram-specific logic.
    
    This class is designed to work with the app.py orchestrator and follows
    the same pattern as TwitterAutomation for consistency.
    """
    
    # Constants
    MAX_COMMENT_RETRIES = 2
    ELEMENT_TIMEOUT = 10000  # 10 seconds for interactive elements
    CONSECUTIVE_OLD_POSTS_LIMIT = 4  # Stop after this many consecutive old posts
    
    def __init__(self, progress_emitter, event_store, human_like_funcs: dict):
        """
        Initialize InstagramAutomation.
        
        Args:
            progress_emitter: ProgressEmitter instance for logging checkpoints
            event_store: EventStore instance for abort signal checking
            human_like_funcs: Dictionary of human-like behavior functions:
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
        
        # Extract human-like functions
        self.human_like_click = human_like_funcs['human_like_click']
        self.human_like_type = human_like_funcs['human_like_type']
        self.human_like_mouse_move = human_like_funcs['human_like_mouse_move']
        self.get_random_delay = human_like_funcs['get_random_delay']
        self.do_review_pause = human_like_funcs['do_review_pause']
        self.do_post_to_post_delay = human_like_funcs['do_post_to_post_delay']
        self.do_profile_to_profile_delay = human_like_funcs['do_profile_to_profile_delay']
        self.navigate_with_retry = human_like_funcs['navigate_with_retry']
    
    # ===========================================
    # BOT CHALLENGE DETECTION
    # ===========================================
    
    async def detect_bot_challenge(self, page: Page) -> bool:
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
                "Unusual activity",
                "Enter your mobile number",
                "You'll need to confirm this mobile number",
                "confirm this mobile number with a code",
                "Phone number verification",
                "Add a phone number"
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
    
    # ===========================================
    # LOGIN HELPERS
    # ===========================================
    
    async def get_logged_in_username(self, page: Page) -> str:
        """
        Get the username of the currently logged-in Instagram account.
        
        Args:
            page: Playwright page object
        
        Returns:
            Username without @ symbol, or empty string if not logged in
        """
        try:
            # Method 1: Check profile link in navigation
            profile_link = await page.query_selector('a[href*="/"][href*="/"][role="link"]')
            if profile_link:
                href = await profile_link.get_attribute('href')
                if href and href.count('/') >= 2:
                    username = href.strip('/').split('/')[-1]
                    if username and username not in ['explore', 'direct', 'reels', 'accounts', 'challenge']:
                        return username.lstrip('@')
            
            # Method 2: Check for username in page content via aria-label
            username_elements = await page.query_selector_all('[aria-label*="profile"]')
            for element in username_elements:
                aria_label = await element.get_attribute('aria-label')
                if aria_label and 'profile picture' in aria_label.lower():
                    # Extract username from "username's profile picture"
                    username = aria_label.replace("'s profile picture", "").replace("'s", "").strip()
                    if username:
                        return username.lstrip('@')
            
            # Method 3: Look for the profile switcher username
            try:
                username_span = await page.query_selector('span.x1lliihq.x1plvlek.xryxfnj')
                if username_span:
                    username_text = await username_span.inner_text()
                    if username_text and '@' not in username_text:
                        return username_text.strip().lstrip('@')
            except:
                pass
            
            return ""
            
        except Exception as e:
            return ""
    
    async def verify_login(self, page: Page) -> bool:
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
                        return True
                except:
                    continue
            
            # Also check if login form is NOT present (means we're logged in)
            try:
                login_form = await page.query_selector(InstagramSelectors.USERNAME_INPUT)
                if login_form is None:
                    # No login form found, might be logged in
                    current_url = page.url
                    if 'login' not in current_url and 'accounts' not in current_url:
                        return True
            except:
                pass
            
            return False
            
        except Exception as e:
            return False
    
    async def logout(self, page: Page):
        """
        Logout from Instagram.
        
        Args:
            page: Playwright page object
        """
        try:
            await self.navigate_with_retry(page, 'https://www.instagram.com/accounts/logout/')
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
            self.progress.warning(f'Logout may have failed: {str(e)[:50]}')
    
    async def perform_login(self, page: Page, username: str, password: str):
        """
        Perform a fresh Instagram login using Playwright.
        
        Args:
            page: Playwright page object
            username: Instagram username/email/phone
            password: Instagram password
        """
        # Navigate to Instagram login page with retry logic
        self.progress.action('Navigating to login page')
        await self.navigate_with_retry(page, 'https://www.instagram.com/accounts/login/?hl=en')
        
        # Check for bot challenge/verification screen after navigation
        await asyncio.sleep(2)
        if await self.detect_bot_challenge(page):
            raise Exception('Instagram account suspended - bot challenge or phone verification required')
        
        # Accept cookies if prompted
        try:
            cookie_button = await page.wait_for_selector(InstagramSelectors.COOKIE_ACCEPT_BUTTON, timeout=5000)
            if cookie_button:
                await cookie_button.click()
                await asyncio.sleep(1)
                self.progress.info('Cookie consent accepted', significant=False)
        except:
            pass
        
        # Wait for login form
        self.progress.action('Waiting for login form')
        await page.wait_for_selector(InstagramSelectors.USERNAME_INPUT, timeout=15000)
        await asyncio.sleep(1)
        
        # Enter username/email
        self.progress.action(f'Entering credentials for {username}')
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
        self.progress.action('Submitting login form')
        login_button = await page.query_selector(InstagramSelectors.LOGIN_BUTTON)
        await login_button.click()
        await asyncio.sleep(5)
        
        # Handle "Save Your Login Info?" prompt
        try:
            not_now_button = await page.wait_for_selector(InstagramSelectors.SAVE_LOGIN_NOT_NOW, timeout=5000)
            if not_now_button:
                await not_now_button.click()
                await asyncio.sleep(2)
                self.progress.info('Declined to save login info', significant=False)
        except:
            pass
        
        # Handle "Turn on Notifications?" prompt
        try:
            not_now_button = await page.wait_for_selector(InstagramSelectors.NOTIFICATIONS_NOT_NOW, timeout=5000)
            if not_now_button:
                await not_now_button.click()
                await asyncio.sleep(2)
                self.progress.info('Declined notifications', significant=False)
        except:
            pass
        
        # Wait for page to stabilize
        self.progress.info('Waiting for login to complete', significant=False)
        await asyncio.sleep(3)
        
        # Check for bot challenge
        if await self.detect_bot_challenge(page):
            raise Exception('Instagram bot challenge detected - human verification required')
    
    async def login(self, page: Page, username: str, password: str, target_user: str):
        """
        Check Instagram login status and login if needed, then navigate to target.
        
        Args:
            page: Playwright page object
            username: Instagram username/email/phone
            password: Instagram password
            target_user: Instagram username to navigate to
        
        Raises:
            Exception: If bot challenge is detected
        """
        # Navigate to Instagram and check if already logged in
        self.progress.action('Checking login status')
        await self.navigate_with_retry(page, 'https://www.instagram.com/?hl=en')
        
        # Check for bot challenge immediately
        if await self.detect_bot_challenge(page):
            raise Exception('Instagram bot challenge detected - account flagged for verification')
        
        # Verify if we're already logged in
        is_logged_in = await self.verify_login(page)
        
        if is_logged_in:
            # Check if we're logged in as the CORRECT account
            logged_in_username = await self.get_logged_in_username(page)
            expected_username = username.lstrip('@')
            
            if logged_in_username and logged_in_username.lower() == expected_username.lower():
                self.progress.success(f'Already logged in as @{expected_username}')
            elif logged_in_username:
                self.progress.warning(f'Logged in as @{logged_in_username}, but need @{expected_username}. Logging out...')
                await self.logout(page)
                self.progress.warning('Not logged in, logging in now')
                await self.perform_login(page, username, password)
            else:
                self.progress.success(f'Already logged in as @{username}')
        else:
            self.progress.warning('Not logged in, logging in now')
            await self.perform_login(page, username, password)
            
            if await self.detect_bot_challenge(page):
                raise Exception('Instagram bot challenge detected - human verification required')
        
        # Navigate to target user's profile
        self.progress.navigating_to_profile(target_user)
        await self.navigate_with_retry(page, f'https://www.instagram.com/{target_user}/?hl=en')
        
        # Final bot challenge check
        if await self.detect_bot_challenge(page):
            raise Exception('Instagram bot challenge detected on profile page')
    
    # ===========================================
    # TIMESTAMP PARSING
    # ===========================================
    
    def parse_timestamp(self, timestamp_str: str) -> datetime | None:
        """
        Parse Instagram's timestamp format to datetime.
        Instagram uses ISO 8601 format in the datetime attribute.
        
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
    # POST EXTRACTION
    # ===========================================
    
    async def get_post_links_from_profile(self, page: Page, target_user: str, logger, max_posts: int = 50) -> list[str]:
        """
        Get post links from a user's profile page in chronological order (newest first).
        
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
            self.progress.info(f'Loading profile page for @{target_user}', significant=False)
            await self.navigate_with_retry(page, f'https://www.instagram.com/{target_user}/?hl=en')
            
            # Emit target opened checkpoint
            self.progress.target_opened(target_user)
            
            await asyncio.sleep(2)
            
            # Collect posts while scrolling
            last_count = 0
            scroll_attempts = 0
            max_scroll_attempts = 10
            
            while len(post_links) < max_posts and scroll_attempts < max_scroll_attempts:
                post_elements = await page.query_selector_all(InstagramSelectors.POST_LINKS)
                
                for element in post_elements:
                    href = await element.get_attribute('href')
                    if href and ('/p/' in href or '/reel/' in href):
                        full_url = f'https://www.instagram.com{href}' if href.startswith('/') else href
                        if full_url not in post_links:
                            post_links.append(full_url)
                            logger.log_post_found()
                
                if len(post_links) == last_count:
                    scroll_attempts += 1
                else:
                    scroll_attempts = 0
                    last_count = len(post_links)
                
                if len(post_links) < max_posts:
                    await page.evaluate('window.scrollBy(0, window.innerHeight)')
                    await asyncio.sleep(1.5)
            
            await page.evaluate('window.scrollTo(0, 0)')
            await asyncio.sleep(1)
            
            logger.log_success(f'Found {len(post_links)} posts on @{target_user} profile')
            
        except Exception as e:
            self.progress.target_failed(target_user, f'Unable to access profile: {str(e)[:50]}')
        
        return post_links
    
    async def get_post_timestamp(self, page: Page) -> datetime | None:
        """
        Get the timestamp of the current post.
        
        Args:
            page: Playwright page object (should be on a post page)
            
        Returns:
            datetime object or None
        """
        try:
            time_element = await page.wait_for_selector(InstagramSelectors.POST_TIMESTAMP, timeout=self.ELEMENT_TIMEOUT)
            if time_element:
                datetime_attr = await time_element.get_attribute('datetime')
                if datetime_attr:
                    return self.parse_timestamp(datetime_attr)
        except Exception as e:
            print(f'[WARN] Could not get post timestamp: {e}')
        return None
    
    # ===========================================
    # COMMENTING
    # ===========================================
    
    async def _find_comment_input(self, page: Page):
        """Find the main comment input at the bottom of the post."""
        # Strategy 1: Find textarea with specific aria-label within a form
        try:
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
        
        # Strategy 3: Find via JavaScript
        try:
            input_el = await page.evaluate_handle('''() => {
                const textareas = document.querySelectorAll('textarea[aria-label="Add a comment…"], textarea[placeholder="Add a comment…"]');
                if (textareas.length === 0) return null;
                
                for (const ta of textareas) {
                    const form = ta.closest('form');
                    if (form) {
                        return ta;
                    }
                }
                
                return textareas[textareas.length - 1];
            }''')
            if input_el:
                return input_el
        except:
            pass
        
        # Strategy 4: Generic selector
        try:
            input_el = await page.query_selector(InstagramSelectors.COMMENT_INPUT)
            if input_el:
                return input_el
        except:
            pass
        
        return None
    
    async def _find_post_button(self, page: Page):
        """Find the POST button that appears after typing."""
        # Strategy 1: Find by text content
        try:
            btn = await page.query_selector('form div[role="button"]:has-text("Post"), form button:has-text("Post")')
            if btn:
                return btn
        except:
            pass
        
        # Strategy 2: Use JavaScript
        try:
            btn = await page.evaluate_handle('''() => {
                const elements = document.querySelectorAll('div[role="button"], button');
                for (const el of elements) {
                    if (el.textContent.trim() === 'Post') {
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
        
        # Strategy 3: Use selector constant
        try:
            btn = await page.query_selector(InstagramSelectors.POST_COMMENT_BUTTON)
            if btn:
                return btn
        except:
            pass
        
        return None
    
    async def comment_on_post(self, page: Page, comment_text: str, logger) -> bool:
        """
        Add a comment to the current post.
        
        Args:
            page: Playwright page object (should be on a post page)
            comment_text: The comment to post
            logger: AutomationLogger instance
            
        Returns:
            True if comment was posted, False otherwise
        """
        for attempt in range(self.MAX_COMMENT_RETRIES):
            try:
                self.progress.info('Preparing to comment', significant=False)
                
                comment_input = await self._find_comment_input(page)
                
                if not comment_input:
                    self.progress.warning(f'Comment box not found, retrying ({attempt + 1}/{self.MAX_COMMENT_RETRIES})')
                    if attempt < self.MAX_COMMENT_RETRIES - 1:
                        await asyncio.sleep(1)
                        continue
                    else:
                        self.progress.error('Comment box not available after all retries')
                        return False
                
                # Scroll into view
                try:
                    await comment_input.scroll_into_view_if_needed()
                    await asyncio.sleep(self.get_random_delay(0.2, 0.5))
                except:
                    pass
                
                # Human-like click
                await self.human_like_click(page, comment_input, logger)
                await asyncio.sleep(self.get_random_delay(0.3, 0.7))
                
                # Clear existing text
                try:
                    await comment_input.fill('')
                    await asyncio.sleep(0.2)
                except:
                    await comment_input.press('Meta+a')
                    await comment_input.press('Backspace')
                    await asyncio.sleep(0.2)
                
                # Type comment with human-like patterns
                self.progress.info('Typing comment naturally', significant=False)
                await self.human_like_type(page, comment_input, comment_text, logger)
                
                # Review pause
                await self.do_review_pause(logger)
                
                # Submit
                self.progress.info('Submitting comment', significant=False)
                await asyncio.sleep(self.get_random_delay(0.3, 0.6))
                
                post_button = await self._find_post_button(page)
                
                if post_button:
                    await self.human_like_click(page, post_button, logger)
                    await asyncio.sleep(self.get_random_delay(1.5, 2.5))
                    
                    # Verify comment was posted
                    try:
                        comment_input = await self._find_comment_input(page)
                        if comment_input:
                            current_value = await comment_input.input_value()
                            if not current_value or len(current_value) < len(comment_text):
                                logger.log_success(f'[OK] Comment posted successfully: "{comment_text}"')
                                self.progress.info(f'Comment submitted: "{comment_text}"', significant=True)
                                return True
                    except:
                        logger.log_success(f'[OK] Comment likely posted: "{comment_text}"')
                        self.progress.info(f'Comment submitted: "{comment_text}"', significant=True)
                        return True
                    
                    self.progress.warning('Post button clicked but waiting for confirmation')
                else:
                    self.progress.warning('Post button did not appear')
                    
                    # Fallback: Try Enter key
                    await comment_input.press('Enter')
                    await asyncio.sleep(2)
                    
                    try:
                        comment_input = await self._find_comment_input(page)
                        if comment_input:
                            current_value = await comment_input.input_value()
                            if not current_value or len(current_value) < len(comment_text):
                                logger.log_success(f'[OK] Comment posted via Enter: "{comment_text}"')
                                return True
                    except:
                        logger.log_success(f'[OK] Comment likely posted via Enter: "{comment_text}"')
                        return True
                
            except Exception as e:
                self.progress.warning(f'Comment attempt failed, retrying ({attempt + 1}/{self.MAX_COMMENT_RETRIES})')
                if attempt < self.MAX_COMMENT_RETRIES - 1:
                    await asyncio.sleep(1)
        
        self.progress.error(f'Could not submit comment after {self.MAX_COMMENT_RETRIES} attempts')
        return False
    
    # ===========================================
    # POST PROCESSING
    # ===========================================
    
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
        Process posts from a user that were posted after the given date.
        
        Uses early termination after CONSECUTIVE_OLD_POSTS_LIMIT old posts.
        
        Args:
            page: Playwright page object
            target_user: Instagram username to process posts from
            date_threshold: Only process posts after this date
            comment_text: Comment to post on each post
            logger: AutomationLogger instance
            post_delay: User-configured delay between posts (seconds)
            
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
        
        self.progress.scanning_posts(target_user)
        
        post_links = await self.get_post_links_from_profile(page, target_user, logger)
        result["posts_found"] = len(post_links)
        
        if not post_links:
            self.progress.posts_scan_failed(target_user, "No posts found")
            return result
        
        self.progress.posts_scanned(target_user, len(post_links))
        
        consecutive_old_posts = 0
        
        for i, post_url in enumerate(post_links):
            if self.event_store.is_aborted():
                self.progress.warning('Post processing aborted by user')
                result["stopped_early"] = True
                break
            
            self.progress.info(f'Analyzing post {i + 1} of {len(post_links)}', significant=False)
            
            try:
                await self.navigate_with_retry(page, post_url)
                
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
                commented = await self.comment_on_post(page, comment_text, logger)
                
                if commented:
                    result["posts_commented"] += 1
                    self.progress.comment_posted(target_user, result["posts_commented"], len(post_links))
                else:
                    self.progress.comment_failed(target_user, i + 1, len(post_links), "Could not submit comment")
                
                logger.log_post_processed(commented=commented)
                result["posts_processed"] += 1
                
                await self.do_post_to_post_delay(post_delay, logger)
                
            except Exception as e:
                error_msg = f'Error processing post {post_url}: {e}'
                self.progress.error(f'Post processing failed: {str(e)[:50]}')
                result["errors"].append(error_msg)
        
        return result
    
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
        Process a fixed number of posts from a user (newest first).
        
        Args:
            page: Playwright page object
            target_user: Instagram username to process posts from
            post_count: Number of posts to process
            comment_text: Comment to post on each post
            logger: AutomationLogger instance
            post_delay: User-configured delay between posts (seconds)
            
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
        
        self.progress.scanning_posts(target_user)
        
        post_links = await self.get_post_links_from_profile(page, target_user, logger)
        result["posts_found"] = len(post_links)
        
        if not post_links:
            self.progress.posts_scan_failed(target_user, "No posts found")
            return result
        
        posts_to_process = post_links[:post_count]
        self.progress.posts_scanned(target_user, len(posts_to_process))
        
        for i, post_url in enumerate(posts_to_process):
            if self.event_store.is_aborted():
                self.progress.warning('Post processing aborted by user')
                result["stopped_early"] = True
                break
            
            self.progress.info(f'Analyzing post {i + 1} of {len(posts_to_process)}', significant=False)
            
            try:
                await self.navigate_with_retry(page, post_url)
                
                post_date = await self.get_post_timestamp(page)
                if post_date:
                    self.progress.info(f'Post from {post_date.strftime("%b %d")}', significant=False)
                
                self.progress.commenting_on_post(i + 1, len(posts_to_process), target_user)
                commented = await self.comment_on_post(page, comment_text, logger)
                
                if commented:
                    result["posts_commented"] += 1
                    self.progress.comment_posted(target_user, result["posts_commented"], len(posts_to_process))
                else:
                    self.progress.comment_failed(target_user, i + 1, len(posts_to_process), "Could not submit comment")
                
                logger.log_post_processed(commented=commented)
                result["posts_processed"] += 1
                
                await self.do_post_to_post_delay(post_delay, logger)
                
            except Exception as e:
                error_msg = f'Error processing post {post_url}: {e}'
                self.progress.error(f'Post processing failed: {str(e)[:50]}')
                result["errors"].append(error_msg)
        
        return result
