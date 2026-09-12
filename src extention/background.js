// --- Configuration ---
const HOST_NAME = "com.wrapper.ytdlp_host"; // MUST match the "name" in native manifest JSON
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 1500; // 1.5 seconds between attempts

// --- Core Logic ---
async function startDownloadProcess(url, options, attempt = 1) {
    if (!url || url.startsWith('chrome') || url.startsWith('edge')) {
        console.error("[BG ERROR] Internal browser URL detected. Aborting.");
        showNotification('error', 'Downloader', 'Cannot download internal browser pages.');
        return;
    }

    console.log(`[BG] Starting download process (Attempt ${attempt}/${MAX_RETRIES}) for: ${url}`);
    showNotification('start', 'Downloader', `Preparing download (Attempt ${attempt}/${MAX_RETRIES})...`);
    updateBadge("retry");

    try {
        // 1. Extract Cookies
        console.log("[BG] Extracting cookies for URL:", url);
        const cookies = await chrome.cookies.getAll({ url: url });
        console.log(`[BG] Found ${cookies.length} cookies.`);
        
        let cookieString = "# Netscape HTTP Cookie File\n";
        cookies.forEach(cookie => {
            const domain = cookie.domain.startsWith('.') ? cookie.domain : '.' + cookie.domain;
            const flag = cookie.domain.startsWith('.') ? 'TRUE' : 'FALSE';
            const path = cookie.path || '/';
            const secure = cookie.secure ? 'TRUE' : 'FALSE';
            const expiration = Math.floor(cookie.expirationDate || 0);
            cookieString += `${domain}\t${flag}\t${path}\t${secure}\t${expiration}\t${cookie.name}\t${cookie.value}\n`;
        });

        // 2. Establish Native Messaging Port
        console.log(`[BG] Opening Native Messaging Port to Python (Attempt ${attempt})...`);
        const port = chrome.runtime.connectNative(HOST_NAME);
        let isResolved = false; // Flag to prevent race conditions between onMessage and onDisconnect

        // 3. Handle Incoming Messages
        port.onMessage.addListener((response) => {
            if (isResolved) return;
            isResolved = true;
            console.log("[BG] >>> MESSAGE RECEIVED FROM PYTHON <<<", response);
            
            if (response && response.status === "success") {
                showNotification('success', 'Download Complete!', response.message);
                updateBadge("success");
            } else if (response && response.status === "error") {
                showNotification('error', 'Download Failed', response.message);
                updateBadge("error");
            }
        });

        // 4. Handle Disconnection (Success, Failure, or Host Not Found)
        port.onDisconnect.addListener(() => {
            if (isResolved) return; // Already handled successfully or with a final error by onMessage
            
            if (chrome.runtime.lastError) {
                console.error(`[BG ERROR] Native Port Disconnected unexpectedly (Attempt ${attempt}):`, chrome.runtime.lastError.message);
                
                // RETRY LOGIC
                if (attempt < MAX_RETRIES) {
                    console.log(`[BG] Retrying in ${RETRY_DELAY_MS}ms...`);
                    showNotification('start', 'Downloader', `Host unreachable. Retrying (${attempt}/${MAX_RETRIES})...`);
                    
                    setTimeout(() => {
                        startDownloadProcess(url, options, attempt + 1);
                    }, RETRY_DELAY_MS);
                } else {
                    console.error("[BG FATAL] Max retries reached. Giving up.");
                    showNotification('error', 'Connection Failed', `Could not connect to the native host after ${MAX_RETRIES} attempts. Please ensure the Python application is running.`);
                    updateBadge("error");
                }
            } else {
                console.log("[BG] Native Port closed gracefully.");
                if (!isResolved) {
                    isResolved = true;
                    updateBadge("default");
                }
            }
        });

        // 5. Send Payload
        const payload = { action: "download", url: url, cookies: cookieString, options: options };
        console.log("[BG] Sending payload to Python:", payload);
        
        try {
            port.postMessage(payload);
        } catch (postError) {
            console.error("[BG ERROR] Failed to post message (Port may be dead):", postError);
            // Manually trigger the disconnect error handling to initiate retry
            if (!isResolved && attempt < MAX_RETRIES) {
                setTimeout(() => {
                    startDownloadProcess(url, options, attempt + 1);
                }, RETRY_DELAY_MS);
            } else if (!isResolved) {
                showNotification('error', 'Connection Failed', 'Failed to send data to the native host.');
                updateBadge("error");
            }
        }

    } catch (error) {
        console.error("[BG FATAL ERROR] Exception in startDownloadProcess:", error);
        if (attempt < MAX_RETRIES) {
            setTimeout(() => {
                startDownloadProcess(url, options, attempt + 1);
            }, RETRY_DELAY_MS);
        } else {
            showNotification('error', 'Extension Error', error.message);
            updateBadge("error");
        }
    }
}

// --- Helper Functions ---
function showNotification(type, title, message) {
    chrome.notifications.create({
        type: 'basic',
        iconUrl: 'icon128.png',
        title: title,
        message: message
    });
}

function updateBadge(status) {
    const colors = {
        'downloading': '#FF0000',
        'success': '#00FF00',
        'error': '#FF0000',
        'retry': '#FFA500', // Orange for retry state
        'default': '#555555'
    };
    chrome.action.setBadgeBackgroundColor({ color: colors[status] || colors['default'] });
    chrome.action.setBadgeText({ text: status === 'default' ? '' : status.charAt(0).toUpperCase() });
}

// --- Message Listener from Popup ---
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "START_DOWNLOAD") {
        console.log("[BG] Received START_DOWNLOAD request from popup.");
        startDownloadProcess(request.url, request.options);
        sendResponse({ status: "received" });
    }
    return true; // Keep the message channel open for async sendResponse
});