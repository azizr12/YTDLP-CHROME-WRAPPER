const HOST_NAME = "com.company.ytdlp_host"; 
console.log("[BG 1] background.js initialized. Host Name:", HOST_NAME);

// --- Helpers ---
function showNotification(id, title, message) {
    console.log(`[BG NOTIFY] Showing notification: ${title} - ${message}`);
    chrome.notifications.create(id, {
        type: 'basic', iconUrl: 'icon128.png', title: title, message: message, priority: 2
    }, () => setTimeout(() => chrome.notifications.clear(id), 7000));
}

function updateBadge(status) {
    if (status === "success") { chrome.action.setBadgeText({ text: "✓" }); chrome.action.setBadgeBackgroundColor({ color: "#4CAF50" }); } 
    else if (status === "error") { chrome.action.setBadgeText({ text: "!" }); chrome.action.setBadgeBackgroundColor({ color: "#F44336" }); } 
    else if (status === "downloading") { chrome.action.setBadgeText({ text: "⏳" }); chrome.action.setBadgeBackgroundColor({ color: "#FF9800" }); }
    setTimeout(() => chrome.action.setBadgeText({ text: "" }), 15000);
}

// --- Message Listener ---
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    console.log("[BG 2] >>> MESSAGE RECEIVED FROM POPUP <<<", request);
    
    if (request.action === "START_DOWNLOAD") {
        console.log("[BG 3] Triggering startDownloadProcess...");
        startDownloadProcess(request.url, request.options);
        sendResponse({ message: "Download process initiated." });
        return true; // Keep the message channel open for async response
    }
});

// --- Core Logic ---
async function startDownloadProcess(url, options) {
    if (!url || url.startsWith('chrome') || url.startsWith('edge')) {
        console.error("[BG ERROR] Internal browser URL detected. Aborting.");
        showNotification('error', 'Downloader', 'Cannot download internal pages.');
        return;
    }

    showNotification('start', 'Downloader', 'Extracting cookies...');
    updateBadge("downloading");

    try {
        console.log("[BG 4] Extracting cookies for URL:", url);
        const cookies = await chrome.cookies.getAll({ url: url });
        console.log(`[BG 5] Found ${cookies.length} cookies.`);
        
        let cookieString = "# Netscape HTTP Cookie File\n";
        cookies.forEach(cookie => {
            const domain = cookie.domain.startsWith('.') ? cookie.domain : '.' + cookie.domain;
            const flag = cookie.domain.startsWith('.') ? 'TRUE' : 'FALSE';
            const path = cookie.path || '/';
            const secure = cookie.secure ? 'TRUE' : 'FALSE';
            const expiration = Math.floor(cookie.expirationDate || 0);
            cookieString += `${domain}\t${flag}\t${path}\t${secure}\t${expiration}\t${cookie.name}\t${cookie.value}\n`;
        });

        console.log("[BG 6] Opening Native Messaging Port to Python...");
        const port = chrome.runtime.connectNative(HOST_NAME);
        
        port.onMessage.addListener((response) => {
            console.log("[BG 7] >>> MESSAGE RECEIVED FROM PYTHON <<<", response);
            if (response && response.status === "success") {
                showNotification('success', 'Download Complete!', response.message);
                updateBadge("success");
            } else if (response && response.status === "error") {
                showNotification('error', 'Download Failed', response.message);
                updateBadge("error");
            }
        });

        port.onDisconnect.addListener(() => {
            if (chrome.runtime.lastError) {
                console.error("[BG ERROR] Native Port Disconnected unexpectedly:", chrome.runtime.lastError.message);
                showNotification('error', 'Connection Lost', chrome.runtime.lastError.message);
                updateBadge("error");
            } else {
                console.log("[BG 8] Native Port closed gracefully.");
            }
        });

        const payload = { action: "download", url: url, cookies: cookieString, options: options };
        console.log("[BG 9] Sending payload to Python:", payload);
        port.postMessage(payload);

    } catch (error) {
        console.error("[BG FATAL ERROR] Exception in startDownloadProcess:", error);
        showNotification('error', 'Extension Error', error.message);
        updateBadge("error");
    }
}