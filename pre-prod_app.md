# Pre-Production State Documentation

## Document Purpose
This document captures the state of `app.py` **BEFORE** the Windows VPS production fixes were applied. Use this as a reference to understand what was changed and why.

---

## Current Issues Identified

### 1. Production Environment Detection (Critical)

**Location:** Lines 31-32

```python
# Current detection - FAILS on Windows VPS
IS_PRODUCTION = os.environ.get('RENDER') or os.environ.get('GUNICORN_CMD_ARGS') or 'gunicorn' in os.environ.get('SERVER_SOFTWARE', '')
```

**Problem:**
- `RENDER` - Only set on Render.com platform
- `GUNICORN_CMD_ARGS` - Gunicorn doesn't run on Windows
- `SERVER_SOFTWARE` - Not set for Flask dev server

**Impact:** 
- `IS_PRODUCTION` is always `False` on Windows VPS
- Background workers never auto-start
- CORS uses development settings
- Debug mode runs in "production"

---

### 2. CORS Configuration (Medium)

**Location:** Lines 45-49

```python
# Configure CORS
if IS_PRODUCTION and ALLOWED_ORIGINS != ['*']:
    CORS(app, origins=ALLOWED_ORIGINS)
else:
    CORS(app)
```

**Problem:**
- Since `IS_PRODUCTION` is `False`, CORS allows all origins
- No explicit method/header configuration
- Missing `supports_credentials` for cookie-based auth

---

### 3. Flask Development Server in Production (Critical)

**Location:** Lines 3308-3328

```python
if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'api':
        # ...
        app.run(debug=True, host='0.0.0.0', port=PORT, threaded=True)
```

**Problems:**
1. `debug=True` in production is a security risk
2. Flask's dev server is single-process, not production-grade
3. `threaded=True` helps but is still inadequate for production
4. No production WSGI server (like Waitress for Windows)

---

### 4. Background Workers Not Starting (Critical)

**Location:** Lines 3282-3287

```python
# Auto-start workers when running under gunicorn or on Render
if IS_PRODUCTION:
    start_background_workers()
```

**Problem:**
- Workers only start if `IS_PRODUCTION` is `True`
- On Windows VPS, `IS_PRODUCTION` is `False`
- Campaign polling never starts
- New campaigns are never auto-detected

---

### 5. Health Check Endpoint (Minor)

**Location:** Lines 559-583

```python
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'service': 'social-media-comment-bot'
    })
```

**Problem:**
- Minimal health check
- No dependency status (Dolphin Anty, Supabase)
- No environment information for debugging

---

### 6. Abort Endpoint Race Condition (Medium)

**Location:** Lines 960-1023

```python
@app.route('/api/abort', methods=['POST'])
def abort_automation():
    # Allow abort during running or recently completed states
    if event_store.status not in ['running', 'completed', 'idle']:
        return jsonify({...}), 400
    
    if event_store.status in ['completed', 'idle']:
        return jsonify({...}), 200
```

**Problem:**
- If automation completes between status check and abort signal set, race condition occurs
- Status codes inconsistent (400 for not running, 200 for already stopped)
- No immediate database update for campaign status

---

### 7. Requirements.txt Missing Windows WSGI Server

**Current Contents:**
```
Flask==3.0.0
flask-cors==4.0.0
flasgger==0.9.7.1
playwright==1.49.1
python-dotenv==1.0.0
supabase==2.10.0
requests==2.32.3
gunicorn==23.0.0
greenlet==3.1.1
```

**Problem:**
- `gunicorn` doesn't work on Windows
- No Windows-compatible WSGI server like `waitress`

---

## Environment Variables State

**Current `.env.example`:**
```env
# No PRODUCTION flag
# No explicit environment detection
PORT=5001  # Optional
DOLPHIN_LOCAL_API_URL=http://localhost:3001  # Optional
```

**Missing:**
- `PRODUCTION=true` for explicit production mode
- `WAITRESS_THREADS` for worker detection
- Clear Windows VPS configuration section

---

## Main Entry Point State

**Location:** Lines 3289-3328

The current main block:
1. Only checks for `api` argument
2. Always uses Flask dev server with `debug=True`
3. Workers only start if `WERKZEUG_RUN_MAIN == 'true'` (Flask reloader check)
4. No production server fallback

---

## API Endpoints Summary

| Endpoint | Method | Status |
|----------|--------|--------|
| `/` | GET | Works |
| `/health` | GET | Works (basic) |
| `/api/progress/emit` | POST | Works |
| `/api/progress/current` | GET | Works |
| `/api/progress/events` | GET | Works |
| `/api/progress/checkpoints` | GET | Works |
| `/api/start` | POST | Works |
| `/api/abort` | POST | **Has race conditions** |
| `/api/webhook/campaign-added` | POST | Works |
| `/api/docs` | GET | Works (Swagger UI) |

---

## Background Services State

| Service | Status |
|---------|--------|
| Campaign Polling Worker | **Not starting on Windows VPS** |
| Auto-campaign detection | **Not working** |
| Webhook handling | Works (if reachable) |

---

## Thread Safety Assessment

| Component | Thread Safe? |
|-----------|--------------|
| EventStore | Yes (uses `threading.Lock`) |
| ProgressEmitter | Yes (singleton, uses EventStore) |
| Abort Signal | Yes (uses EventStore lock) |
| Campaign Status Updates | Yes (Supabase handles) |

---

## Summary of Required Fixes

1. **Add explicit production detection** via `PRODUCTION` env var
2. **Add Waitress** as Windows-compatible WSGI server
3. **Fix worker auto-start** to not depend solely on `IS_PRODUCTION`
4. **Improve abort endpoint** with better race condition handling
5. **Enhance health check** with dependency status
6. **Update requirements.txt** with `waitress`
7. **Update .env.example** with Windows VPS configuration

---

## File Checksums (for verification)

- **app.py**: 3328 lines
- **requirements.txt**: 9 packages
- **.env.example**: 58 lines

---

*Document generated: January 2026*
*Purpose: Pre-production state capture for Windows VPS migration*
