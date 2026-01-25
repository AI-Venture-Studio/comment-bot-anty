# Windows VPS Installation Guide

## Complete Setup Instructions for Social Media Comment Bot

This guide provides step-by-step instructions for deploying the comment bot server on a Windows VPS.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [VPS Requirements](#vps-requirements)
3. [Initial Windows Setup](#initial-windows-setup)
4. [Install Python](#install-python)
5. [Install Git](#install-git)
6. [Clone the Repository](#clone-the-repository)
7. [Configure Python Environment](#configure-python-environment)
8. [Install Dependencies](#install-dependencies)
9. [Install Playwright Browsers](#install-playwright-browsers)
10. [Install Dolphin Anty](#install-dolphin-anty)
11. [Configure Environment Variables](#configure-environment-variables)
12. [Configure Windows Firewall](#configure-windows-firewall)
13. [Test the Installation](#test-the-installation)
14. [Run as Windows Service](#run-as-windows-service)
15. [Monitoring and Logs](#monitoring-and-logs)
16. [Troubleshooting](#troubleshooting)

---

## Prerequisites

Before starting, ensure you have:

- [ ] Windows VPS with RDP access
- [ ] Administrator account credentials
- [ ] Static public IP address
- [ ] Supabase project with database configured
- [ ] Dolphin Anty account with API token
- [ ] Domain name (optional, for HTTPS)

---

## VPS Requirements

### Minimum Specifications
| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 2 cores | 4 cores |
| RAM | 4 GB | 8 GB |
| Storage | 50 GB SSD | 100 GB SSD |
| OS | Windows Server 2019 | Windows Server 2022 |
| Network | 100 Mbps | 1 Gbps |

### Recommended Providers
- AWS Lightsail (Windows)
- DigitalOcean (Windows Droplet)
- Vultr (Windows VPS)
- Contabo (Windows VPS)

---

## Initial Windows Setup

### 1. Connect via RDP

```
Remote Desktop Connection
Computer: YOUR_VPS_IP
Username: Administrator
Password: YOUR_PASSWORD
```

### 2. Update Windows

Open PowerShell as Administrator:

```powershell
# Check for updates
Get-WindowsUpdate

# Install updates (if Windows Update module is installed)
Install-WindowsUpdate -AcceptAll -AutoReboot
```

Or use Windows Settings → Update & Security → Windows Update.

### 3. Set Timezone

```powershell
# Set to UTC (recommended for servers)
Set-TimeZone -Id "UTC"

# Or set to your preferred timezone
# Set-TimeZone -Id "Eastern Standard Time"
```

### 4. Disable IE Enhanced Security (for easier downloads)

Open Server Manager → Local Server → IE Enhanced Security Configuration → Off (for Administrators)

---

## Install Python

### 1. Download Python

Open PowerShell as Administrator:

```powershell
# Download Python 3.11 installer
Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -OutFile "$env:TEMP\python-installer.exe"

# Or download manually from https://www.python.org/downloads/
```

### 2. Install Python

```powershell
# Run installer with recommended options
Start-Process -FilePath "$env:TEMP\python-installer.exe" -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
```

Or run the installer manually with these options:
- ✅ Add Python to PATH
- ✅ Install for all users
- ✅ pip included

### 3. Verify Installation

Open a **new** PowerShell window:

```powershell
# Check Python version
python --version
# Expected: Python 3.11.x

# Check pip version
pip --version
# Expected: pip 24.x.x

# Check Python path
where python
# Expected: C:\Program Files\Python311\python.exe
```

### 4. Upgrade pip

```powershell
python -m pip install --upgrade pip
```

---

## Install Git

### 1. Download Git

```powershell
Invoke-WebRequest -Uri "https://github.com/git-for-windows/git/releases/download/v2.43.0.windows.1/Git-2.43.0-64-bit.exe" -OutFile "$env:TEMP\git-installer.exe"
```

### 2. Install Git

```powershell
Start-Process -FilePath "$env:TEMP\git-installer.exe" -ArgumentList "/VERYSILENT /NORESTART" -Wait
```

Or run installer manually with defaults.

### 3. Verify Installation

Open a **new** PowerShell window:

```powershell
git --version
# Expected: git version 2.43.0.windows.1
```

---

## Clone the Repository

### 1. Create Project Directory

```powershell
# Create directory for projects
New-Item -ItemType Directory -Path "C:\Projects" -Force
cd C:\Projects
```

### 2. Clone Repository

```powershell
# Clone the repository
git clone https://github.com/YOUR_USERNAME/social-media-comment-bot.git

# Navigate to server directory
cd social-media-comment-bot\comment-bot-server
```

### 3. Verify Files

```powershell
# List files
Get-ChildItem

# Expected files:
# - app.py
# - requirements.txt
# - .env.example
# - instagram.py
# - twitter.py
# - etc.
```

---

## Configure Python Environment

### 1. Create Virtual Environment

```powershell
# Navigate to project directory
cd C:\Projects\social-media-comment-bot\comment-bot-server

# Create virtual environment
python -m venv venv

# Activate virtual environment
.\venv\Scripts\Activate.ps1
```

**Note:** If you get an execution policy error:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 2. Verify Virtual Environment

```powershell
# Check that (venv) appears in prompt
# (venv) PS C:\Projects\...>

# Verify Python is from venv
where python
# Expected: C:\Projects\social-media-comment-bot\comment-bot-server\venv\Scripts\python.exe
```

---

## Install Dependencies

### 1. Install Requirements

```powershell
# Ensure venv is activated
.\venv\Scripts\Activate.ps1

# Install all dependencies
pip install -r requirements.txt

# Install waitress (Windows WSGI server)
pip install waitress
```

### 2. Verify Key Packages

```powershell
# Check Flask
pip show flask
# Expected: Version: 3.0.0

# Check Playwright
pip show playwright
# Expected: Version: 1.49.1

# Check Waitress
pip show waitress
# Expected: Version: 2.1.x
```

---

## Install Playwright Browsers

### 1. Install Chromium

```powershell
# Install Playwright browsers
playwright install chromium

# Or install all browsers
playwright install
```

### 2. Install System Dependencies

```powershell
# Install system dependencies (may require admin)
playwright install-deps
```

### 3. Verify Installation

```powershell
# Test Playwright
python -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"
```

---

## Install Dolphin Anty

### 1. Download Dolphin Anty

1. Go to https://dolphin-anty.com/
2. Download the Windows installer
3. Run the installer

### 2. Configure Dolphin Anty

1. Launch Dolphin Anty
2. Log in with your account
3. Go to Settings → API
4. Generate an API token (copy and save it)

### 3. Configure Local API

1. In Dolphin Anty, go to Settings → API
2. Ensure "Enable local API" is checked
3. Note the port (default: 3001)
4. Enable "Allow connections from remote hosts" if needed

### 4. Create Browser Profiles

1. Create browser profiles for each social account
2. Name them descriptively (e.g., "instagram_user1", "x_user2")
3. Configure fingerprint settings as needed

### 5. Set Dolphin Anty to Auto-Start

1. Press `Win + R`
2. Type `shell:startup`
3. Create a shortcut to Dolphin Anty in this folder

---

## Configure Environment Variables

### 1. Create .env File

```powershell
# Navigate to project directory
cd C:\Projects\social-media-comment-bot\comment-bot-server

# Copy example file
Copy-Item .env.example .env

# Edit with notepad
notepad .env
```

### 2. Configure .env File

```env
# ===========================================
# PRODUCTION SETTINGS (REQUIRED FOR WINDOWS VPS)
# ===========================================
PRODUCTION=true
PORT=5001

# ===========================================
# DOLPHIN ANTY CONFIGURATION
# ===========================================
DOLPHIN_API_TOKEN=your_dolphin_api_token_here
DOLPHIN_LOCAL_API_URL=http://localhost:3001

# ===========================================
# SUPABASE CONFIGURATION
# ===========================================
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_supabase_anon_key_here

# ===========================================
# CORS CONFIGURATION
# ===========================================
# Comma-separated list of allowed origins
ALLOWED_ORIGINS=https://your-frontend.com,http://localhost:3000

# ===========================================
# OPTIONAL: DEFAULT CAMPAIGN SETTINGS
# ===========================================
# COMMENT_TEXT=Great post!
# DATE_FILTER=2025-12-01
```

### 3. Verify Environment Variables

```powershell
# Test loading .env
python -c "import dotenv; dotenv.load_dotenv(); import os; print(f'PRODUCTION={os.getenv(\"PRODUCTION\")}')"
# Expected: PRODUCTION=true
```

---

## Configure Windows Firewall

### 1. Open Port for API

Open PowerShell as Administrator:

```powershell
# Allow inbound connections on port 5001
New-NetFirewallRule -DisplayName "Comment Bot API" -Direction Inbound -Port 5001 -Protocol TCP -Action Allow

# Verify rule was created
Get-NetFirewallRule -DisplayName "Comment Bot API"
```

### 2. Allow Dolphin Anty (if remote)

```powershell
# If accessing Dolphin Anty from remote (not needed if on same machine)
New-NetFirewallRule -DisplayName "Dolphin Anty API" -Direction Inbound -Port 3001 -Protocol TCP -Action Allow
```

### 3. Verify Firewall Rules

```powershell
# List all custom rules
Get-NetFirewallRule | Where-Object { $_.DisplayName -like "*Comment*" -or $_.DisplayName -like "*Dolphin*" } | Format-Table Name, DisplayName, Enabled, Direction, Action
```

---

## Test the Installation

### 1. Start Dolphin Anty

1. Launch Dolphin Anty application
2. Verify it's running (check system tray)
3. Verify local API is accessible

### 2. Test Dolphin Anty Connection

```powershell
# Activate venv
cd C:\Projects\social-media-comment-bot\comment-bot-server
.\venv\Scripts\Activate.ps1

# Test connection
python -c "import requests; r = requests.get('http://localhost:3001/v1.0/browser_profiles'); print(f'Dolphin Anty: {\"OK\" if r.status_code == 200 else \"FAILED\"}')"
```

### 3. Start the Server

```powershell
# Start API server
python app.py api
```

Expected output:
```
[SERVER] Starting Social Media Comment Bot API Server...
[API] Documentation: http://localhost:5001/api/docs
[WORKER] Background polling worker started
[SERVER] Running with Waitress on 0.0.0.0:5001
[SERVER] Production mode - debug disabled
```

### 4. Test API Endpoints

Open a new PowerShell window:

```powershell
# Test health endpoint
Invoke-RestMethod -Uri "http://localhost:5001/health" | ConvertTo-Json

# Expected response includes:
# "status": "healthy"
# "environment": "production"
# "workers_started": true
```

### 5. Test from External

From your local machine (not the VPS):

```bash
# Replace YOUR_VPS_IP with actual IP
curl http://YOUR_VPS_IP:5001/health
```

---

## Run as Windows Service

### Option A: Using NSSM (Recommended)

#### 1. Download NSSM

```powershell
# Download NSSM
Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile "$env:TEMP\nssm.zip"

# Extract
Expand-Archive -Path "$env:TEMP\nssm.zip" -DestinationPath "C:\Tools\nssm" -Force

# Add to PATH
$env:PATH += ";C:\Tools\nssm\nssm-2.24\win64"
```

#### 2. Create Service

```powershell
# Create service
nssm install CommentBotAPI "C:\Projects\social-media-comment-bot\comment-bot-server\venv\Scripts\python.exe"

# Configure arguments
nssm set CommentBotAPI AppParameters "app.py api"

# Set working directory
nssm set CommentBotAPI AppDirectory "C:\Projects\social-media-comment-bot\comment-bot-server"

# Configure startup
nssm set CommentBotAPI Start SERVICE_AUTO_START

# Configure logging
nssm set CommentBotAPI AppStdout "C:\Projects\social-media-comment-bot\comment-bot-server\logs\stdout.log"
nssm set CommentBotAPI AppStderr "C:\Projects\social-media-comment-bot\comment-bot-server\logs\stderr.log"

# Create logs directory
New-Item -ItemType Directory -Path "C:\Projects\social-media-comment-bot\comment-bot-server\logs" -Force
```

#### 3. Start Service

```powershell
# Start the service
nssm start CommentBotAPI

# Check status
nssm status CommentBotAPI

# View in Services
services.msc
```

#### 4. Manage Service

```powershell
# Stop service
nssm stop CommentBotAPI

# Restart service
nssm restart CommentBotAPI

# Remove service (if needed)
nssm remove CommentBotAPI confirm
```

### Option B: Using Task Scheduler

#### 1. Create Startup Script

Create `C:\Projects\social-media-comment-bot\comment-bot-server\start-server.bat`:

```batch
@echo off
cd /d C:\Projects\social-media-comment-bot\comment-bot-server
call venv\Scripts\activate.bat
python app.py api
```

#### 2. Create Scheduled Task

```powershell
# Create task that runs at startup
$action = New-ScheduledTaskAction -Execute "C:\Projects\social-media-comment-bot\comment-bot-server\start-server.bat"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName "CommentBotAPI" -Action $action -Trigger $trigger -Principal $principal -Settings $settings
```

#### 3. Start Task

```powershell
Start-ScheduledTask -TaskName "CommentBotAPI"
```

---

## Monitoring and Logs

### 1. View Real-Time Logs

If running in console:
```powershell
# Logs appear in console
python app.py api
```

If running as service:
```powershell
# View stdout log
Get-Content -Path "C:\Projects\social-media-comment-bot\comment-bot-server\logs\stdout.log" -Tail 100 -Wait

# View stderr log
Get-Content -Path "C:\Projects\social-media-comment-bot\comment-bot-server\logs\stderr.log" -Tail 100 -Wait
```

### 2. Check Service Status

```powershell
# Check if service is running
Get-Service -Name "CommentBotAPI"

# Check if port is listening
netstat -ano | findstr :5001
```

### 3. Monitor API Health

Create a simple health check script `check-health.ps1`:

```powershell
# Health check script
$response = Invoke-RestMethod -Uri "http://localhost:5001/health" -TimeoutSec 5

if ($response.status -eq "healthy") {
    Write-Host "✅ API is healthy" -ForegroundColor Green
    Write-Host "  Environment: $($response.environment)"
    Write-Host "  Workers: $($response.workers_started)"
    Write-Host "  Automation: $($response.automation_status)"
} else {
    Write-Host "❌ API is unhealthy" -ForegroundColor Red
}
```

### 4. Set Up Log Rotation

Create a log rotation script `rotate-logs.ps1`:

```powershell
$logDir = "C:\Projects\social-media-comment-bot\comment-bot-server\logs"
$maxSize = 10MB
$maxFiles = 5

Get-ChildItem -Path $logDir -Filter "*.log" | ForEach-Object {
    if ($_.Length -gt $maxSize) {
        $baseName = $_.BaseName
        $extension = $_.Extension
        
        # Rotate existing files
        for ($i = $maxFiles - 1; $i -ge 1; $i--) {
            $old = Join-Path $logDir "$baseName.$i$extension"
            $new = Join-Path $logDir "$baseName.$($i + 1)$extension"
            if (Test-Path $old) {
                Move-Item -Path $old -Destination $new -Force
            }
        }
        
        # Move current log
        Move-Item -Path $_.FullName -Destination (Join-Path $logDir "$baseName.1$extension") -Force
    }
}
```

---

## Troubleshooting

### Issue: "PRODUCTION not detected"

**Symptom:** Health endpoint shows `"environment": "development"`

**Solution:**
1. Verify `.env` file contains `PRODUCTION=true`
2. Check there are no spaces: `PRODUCTION=true` (not `PRODUCTION = true`)
3. Restart the server

### Issue: "Cannot connect to Dolphin Anty"

**Symptom:** Health check shows `"dolphin_anty": "error"`

**Solution:**
1. Verify Dolphin Anty is running (check system tray)
2. Verify local API is enabled in Dolphin Anty settings
3. Test connection: `curl http://localhost:3001/v1.0/browser_profiles`
4. Check if port 3001 is blocked

### Issue: "Workers not started"

**Symptom:** Campaigns not being detected automatically

**Solution:**
1. Verify running with `python app.py api` (not just `python app.py`)
2. Check logs for `[WORKER] Background polling worker started`
3. Restart the server

### Issue: "Port 5001 already in use"

**Symptom:** Server fails to start with port error

**Solution:**
```powershell
# Find process using port
netstat -ano | findstr :5001

# Kill process (replace PID with actual process ID)
taskkill /PID 1234 /F

# Or change port in .env
# PORT=5002
```

### Issue: "Frontend cannot reach API"

**Symptom:** CORS errors or connection refused

**Solution:**
1. Verify firewall rule is active:
   ```powershell
   Get-NetFirewallRule -DisplayName "Comment Bot API"
   ```
2. Verify server is listening on 0.0.0.0:
   ```powershell
   netstat -ano | findstr :5001
   # Should show 0.0.0.0:5001 LISTENING
   ```
3. Verify ALLOWED_ORIGINS in `.env` includes frontend URL
4. Test from VPS itself first

### Issue: "Playwright browser not found"

**Symptom:** Error about missing browser

**Solution:**
```powershell
# Reinstall browsers
.\venv\Scripts\Activate.ps1
playwright install chromium
playwright install-deps
```

### Issue: "Service won't start"

**Symptom:** NSSM service fails to start

**Solution:**
1. Check logs in `logs\stderr.log`
2. Try running manually first:
   ```powershell
   cd C:\Projects\social-media-comment-bot\comment-bot-server
   .\venv\Scripts\Activate.ps1
   python app.py api
   ```
3. Verify paths in NSSM configuration:
   ```powershell
   nssm dump CommentBotAPI
   ```

---

## Quick Reference Commands

```powershell
# Navigate to project
cd C:\Projects\social-media-comment-bot\comment-bot-server

# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Start server (development)
python app.py api

# Check health
Invoke-RestMethod -Uri "http://localhost:5001/health" | ConvertTo-Json

# View logs
Get-Content -Path "logs\stdout.log" -Tail 50

# Restart service
nssm restart CommentBotAPI

# Check if port is listening
netstat -ano | findstr :5001

# Check firewall rules
Get-NetFirewallRule -DisplayName "Comment Bot*"
```

---

## Security Recommendations

1. **Change default RDP port** (3389 → something else)
2. **Enable Windows Defender**
3. **Use strong passwords** for all accounts
4. **Set up HTTPS** with a reverse proxy (nginx/IIS)
5. **Restrict ALLOWED_ORIGINS** to your frontend only
6. **Regular Windows Updates**
7. **Backup Dolphin Anty profiles** regularly

---

## Next Steps

After successful installation:

1. ✅ Configure social accounts in Supabase
2. ✅ Assign browser profiles to accounts
3. ✅ Create your first campaign
4. ✅ Monitor via `/api/progress/current`
5. ✅ Set up frontend to connect to VPS IP

---

*Guide Version: 1.0*
*Last Updated: January 2026*
*Compatible with: Python 3.11+, Windows Server 2019/2022*
