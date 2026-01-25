# Social Media Comment Bot Server - Production Documentation & Issues

## Project Overview

This is a **Python Flask** backend server for automating social media commenting on **Instagram** and **X/Twitter**. The server uses **Playwright** for browser automation via **Dolphin Anty** anti-detect browser profiles.

### Core Functionality

1. **Campaign Management** - Queue-based campaign processing from Supabase
2. **Browser Automation** - Playwright-based automation with human-like behavior simulation
3. **Multi-Platform Support** - Instagram and X/Twitter commenting
4. **Anti-Detection** - Dolphin Anty integration for anti-detect browser profiles
5. **Progress Tracking** - Real-time event streaming for client UI
6. **Webhook Integration** - Supabase webhook triggers for new campaigns

### Architecture

- **Framework**: Flask with Flask-CORS and Flasgger (Swagger docs)
- **Automation**: Playwright async API
- **Anti-Detect**: Dolphin Anty browser profiles (local/remote)
- **Database**: Supabase (PostgreSQL)
- **Process Model**: Gunicorn with threading for background automation

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | API info |
| `/health` | GET | Health check |
| `/api/docs` | GET | Swagger documentation |
| `/api/start` | POST | Start automation |
| `/api/abort` | POST | Abort running automation |
| `/api/progress/current` | GET | Get current progress state |
| `/api/progress/events` | GET | Get event feed |
| `/api/progress/checkpoints` | GET | Get checkpoint feed |
| `/api/progress/emit` | POST | Emit progress event (internal) |
| `/api/webhook/campaign-added` | POST | Supabase webhook for new campaigns |

---

## 🔴 CRITICAL Issues for Production

### 1. **No API Authentication**
**Severity**: CRITICAL  
**Description**: All API endpoints are publicly accessible without authentication.  
**Risk**: Anyone can start/stop automations, access progress data, trigger webhooks.

**Recommendation**:
```python
# Add API key authentication middleware
API_KEY = os.environ.get('API_SECRET_KEY')

@app.before_request
def verify_api_key():
    if request.endpoint in ['health_check', 'index']:  # Public endpoints
        return
    api_key = request.headers.get('X-API-Key')
    if api_key != API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
```

### 2. **Webhook Endpoint Has No Signature Verification**
**Severity**: CRITICAL  
**Description**: `/api/webhook/campaign-added` accepts any payload without verifying it's from Supabase.  
**Risk**: Attackers can trigger arbitrary campaign starts.

**Recommendation**:
- Implement Supabase webhook signature verification
- Use HMAC signature validation with a shared secret
- Add IP whitelist for Supabase webhook IPs

### 3. **Hardcoded Credentials in `.env` Without Encryption**
**Severity**: HIGH  
**Description**: Instagram/Twitter passwords stored in plain text in Supabase `social_accounts` table.  
**Risk**: Database breach exposes all account credentials.

**Recommendation**:
- Encrypt passwords at rest using AES-256
- Use a secrets manager (AWS Secrets Manager, Vault)
- Implement password encryption before storing

### 4. **Single Worker Concurrency Issue**
**Severity**: HIGH  
**Description**: `gunicorn --workers 1` means only one automation can run at a time. The `event_store` is a global singleton that gets cleared on new campaigns.  
**Risk**: Concurrent campaign starts will corrupt state.

**Recommendation**:
- Implement proper job queue (Redis Queue, Celery)
- Use database-backed event storage instead of in-memory
- Add campaign locking mechanism

### 5. **No Rate Limiting**
**Severity**: HIGH  
**Description**: No rate limiting on any endpoints.  
**Risk**: API abuse, DoS attacks, excessive resource consumption.

**Recommendation**:
```python
from flask_limiter import Limiter
limiter = Limiter(app, key_func=get_remote_address)

@app.route('/api/start', methods=['POST'])
@limiter.limit("5 per hour")
def start_automation():
    ...
```

---

## 🟠 Security Concerns

### 6. **CORS Configuration in Production**
**Severity**: MEDIUM  
**Location**: Lines 35-41 in `app.py`

**Issue**: If `ALLOWED_ORIGINS` is not set, CORS allows all origins (`*`).

**Recommendation**:
- Always set `ALLOWED_ORIGINS` explicitly in production
- Never default to `*` in production

### 7. **Swagger UI Exposed in Production**
**Severity**: MEDIUM  
**Description**: API documentation at `/api/docs` is publicly accessible.

**Recommendation**:
```python
if not IS_PRODUCTION:
    swagger = Swagger(app, config=swagger_config, template=swagger_template)
```

### 8. **Cookie Storage on Server**
**Severity**: MEDIUM  
**Location**: `CookieManager` class (~Line 1525)

**Issue**: Browser cookies stored in local files on the server filesystem.

**Risks**:
- Cookies not encrypted at rest
- Lost on server restart/redeploy
- No secure cleanup mechanism

**Recommendation**:
- Store cookies in database (encrypted)
- Or use Dolphin Anty's built-in cookie persistence

### 9. **Error Messages Leak Internal Details**
**Severity**: LOW  
**Description**: Error responses include stack traces and internal paths.

**Recommendation**:
- Implement generic error handler
- Log full errors server-side, return sanitized messages to client

---

## 🟡 Stability & Reliability Issues

### 10. **In-Memory Event Store**
**Severity**: HIGH  
**Location**: `EventStore` class (~Line 95)

**Issue**: All progress events stored in memory. Lost on server restart.

**Recommendation**:
- Use Redis for event storage
- Or persist events to Supabase

### 11. **No Graceful Shutdown Handling**
**Severity**: MEDIUM  
**Description**: No SIGTERM handler to gracefully stop running automations.

**Risk**: Server restart during automation = orphaned browser processes, incomplete campaigns.

**Recommendation**:
```python
import signal

def handle_shutdown(signum, frame):
    event_store.set_abort()
    # Wait for cleanup
    time.sleep(10)
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_shutdown)
```

### 12. **Dolphin Anty Dependency - Single Point of Failure**
**Severity**: HIGH  
**Description**: Server depends on external Dolphin Anty service running on a separate machine.

**Risks**:
- Dolphin Anty down = all automation fails
- Network issues between servers cause timeouts
- No health check for Dolphin Anty availability

**Recommendation**:
- Add `/health` check for Dolphin Anty connectivity
- Implement circuit breaker pattern
- Add fallback or queue retry mechanism

### 13. **Browser Profile Cleanup on Errors**
**Severity**: MEDIUM  
**Description**: If automation crashes, browser profiles may remain running.

**Recommendation**:
- Always wrap automation in try/finally
- Force stop browser profiles in cleanup
- Add periodic stale profile cleanup job

### 14. **No Retry for Failed Campaigns**
**Severity**: MEDIUM  
**Description**: Failed campaigns are marked as `failed` with no automatic retry.

**Recommendation**:
- Add `retry_count` field to campaigns
- Implement exponential backoff retry
- Max retry limit before permanent failure

---

## 🔵 Missing Features for Production

### 15. **No Logging to External Service**
**Description**: Logs only go to stdout. Lost on server restart.

**Recommendation**:
- Integrate with Papertrail, Logtail, or CloudWatch
- Add structured logging (JSON format)

### 16. **No Metrics/Monitoring**
**Description**: No Prometheus metrics, no APM integration.

**Recommendation**:
- Add `/metrics` endpoint for Prometheus
- Integrate with DataDog/New Relic APM
- Track: campaign success rate, comment latency, error rates

### 17. **No Health Check for Dependencies**
**Description**: `/health` only returns "healthy" without checking:
- Supabase connectivity
- Dolphin Anty availability
- Browser profile status

**Recommendation**:
```python
@app.route('/health', methods=['GET'])
def health_check():
    checks = {
        'supabase': check_supabase(),
        'dolphin_anty': check_dolphin_anty()
    }
    healthy = all(checks.values())
    return jsonify({
        'status': 'healthy' if healthy else 'degraded',
        'checks': checks
    }), 200 if healthy else 503
```

### 18. **No Campaign Scheduling**
**Description**: Campaigns execute immediately when added.

**Recommendation**:
- Add `scheduled_at` field to campaigns
- Only process campaigns where `scheduled_at <= now()`

### 19. **No Duplicate Detection**
**Description**: No check if a comment was already posted on a target post.

**Risk**: Same comment posted multiple times on same post.

**Recommendation**:
- Track commented posts in database
- Check before commenting

### 20. **No Account Rotation/Load Balancing**
**Description**: All campaigns use specified accounts sequentially.

**Recommendation**:
- Implement account pools
- Automatic rotation on rate limit
- Load balance across multiple accounts

---

## 🟣 Platform-Specific Risks

### 21. **Instagram Bot Detection**
**Severity**: HIGH

**Current Mitigations**:
- Human-like typing delays ✓
- Mouse movement simulation ✓
- Bot challenge detection ✓
- Session cookie persistence ✓

**Missing**:
- No proxy rotation
- No fingerprint randomization (relies on Dolphin Anty)
- No action variety (only comments, no likes/views)

### 22. **X/Twitter API Changes**
**Severity**: MEDIUM

**Risk**: X frequently changes their DOM structure. Selectors may break without notice.

**Recommendation**:
- Add selector version checking
- Implement selector fallbacks
- Monitor for selector failures

### 23. **Account Suspension Handling**
**Severity**: MEDIUM  
**Location**: `deactivate_account()` function

**Issue**: Suspended accounts are deactivated but campaigns using them fail silently.

**Recommendation**:
- Notify user when account is deactivated
- Remove suspended accounts from active campaigns
- Implement account health monitoring

---

## 📋 Pre-Production Checklist

### Environment Variables Required
```bash
# REQUIRED
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_key_here
DOLPHIN_API_TOKEN=your_token
DOLPHIN_LOCAL_API_URL=http://dolphin-server:3001

# RECOMMENDED FOR PRODUCTION
ALLOWED_ORIGINS=https://your-client-domain.com
API_SECRET_KEY=generate_random_key
PORT=5001

# OPTIONAL
RENDER=true  # Auto-detected on Render
```

### Pre-Deploy Steps
- [ ] Set all environment variables
- [ ] Verify Dolphin Anty server is accessible from VPS
- [ ] Test Supabase connection
- [ ] Configure CORS whitelist
- [ ] Set up webhook in Supabase with signature
- [ ] Configure firewall (only allow necessary ports)
- [ ] Set up SSL/TLS termination
- [ ] Configure log aggregation
- [ ] Set up uptime monitoring

### Post-Deploy Verification
- [ ] `/health` returns 200
- [ ] Swagger docs accessible (if intended)
- [ ] Can start/stop automation
- [ ] Webhook triggers correctly
- [ ] Progress events stream properly
- [ ] Browser profiles connect successfully

---

## Recommended Priority Fixes

| Priority | Issue | Effort | Impact |
|----------|-------|--------|--------|
| P0 | Add API authentication | Medium | Critical |
| P0 | Webhook signature verification | Low | Critical |
| P0 | Set ALLOWED_ORIGINS | Low | High |
| P1 | Add rate limiting | Low | High |
| P1 | Health check dependencies | Low | Medium |
| P1 | Graceful shutdown handler | Low | Medium |
| P2 | External logging | Medium | Medium |
| P2 | Redis event storage | Medium | Medium |
| P3 | Encrypt stored credentials | High | High |
| P3 | Job queue implementation | High | High |

---

## Files to Review Before Production

1. `app.py` - Main server logic, security middleware
2. `.env` - Ensure no secrets in repository
3. `render.yaml` - Deployment configuration
4. `build.sh` - Build script verification
5. `requirements.txt` - Dependency versions locked

---

## Architecture Diagram

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Next.js       │────▶│   Flask Server   │────▶│   Supabase      │
│   Client        │     │   (This VPS)     │     │   (Database)    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               │ HTTP/WebSocket
                               ▼
                        ┌──────────────────┐
                        │  Dolphin Anty    │
                        │  (Windows VPS)   │
                        │  + Playwright    │
                        └──────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │  Instagram/X     │
                        │  (Target Sites)  │
                        └──────────────────┘
```

**Note**: The architecture depends on Dolphin Anty running on a separate Windows machine. Ensure network connectivity and firewall rules allow communication between the Flask server VPS and Dolphin Anty server.
