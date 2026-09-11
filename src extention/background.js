const HOST_NAME = "com.wrapper.ytdlp_host"; 

// ---------------------------------------------------------
// 1. Helper: Desktop Notifications
// ---------------------------------------------------------
function showNotification(id, title, message) {
    chrome.notifications.create(id, {
        type: 'basic',
        iconUrl: 'icon128.png',
        title: title,
        message: message,
        priority: 2
    }, () => {
        setTimeout(() => chrome.notifications.clear(id), 7000);
    });
}

// ---------------------------------------------------------
// 2. Helper: Update Toolbar Icon (Badge)
// ---------------------------------------------------------
function updateBadge(status) {
    if (status === "success") {
        chrome.action.setBadgeText({ text: "✓" });
        chrome.action.setBadgeBackgroundColor({ color: "#4CAF50" }); // Green
    } else if (status === "error") {
        chrome.action.setBadgeText({ text: "!" });
        chrome.action.setBadgeBackgroundColor({ color: "#F44336" }); // Red
    } else if (status === "downloading") {
        chrome.action.setBadgeText({ text: "⏳" });
        chrome.action.setBadgeBackgroundColor({ color: "#FF9800" }); // Orange
    }
    
    // Clear the badge after 15 seconds
    setTimeout(() => {
        chrome.action.setBadgeText({ text: "" });
    }, 15000);
}

// ---------------------------------------------------------
// 3. Main Click Handler (Using Persistent Ports)
// ---------------------------------------------------------
chrome.action.onClicked.addListener(async (tab) => {
    if (!tab.url || tab.url.startsWith('chrome') || tab.url.startsWith('edge')) {
        showNotification('error', 'Downloader', 'FAILED !!');
        return;
    }

    showNotification('start', 'Downloader', 'Start downloading...');
    updateBadge("downloading");

    try {
        // Extract Cookies
        const cookies = await chrome.cookies.getAll({ url: tab.url });
        let cookieString = "# Netscape HTTP Cookie File\n";
        cookies.forEach(cookie => {
            const domain = cookie.domain.startsWith('.') ? cookie.domain : '.' + cookie.domain;
            const flag = cookie.domain.startsWith('.') ? 'TRUE' : 'FALSE';
            const path = cookie.path || '/';
            const secure = cookie.secure ? 'TRUE' : 'FALSE';
            const expiration = Math.floor(cookie.expirationDate || 0);
            cookieString += `${domain}\t${flag}\t${path}\t${secure}\t${expiration}\t${cookie.name}\t${cookie.value}\n`;
        });

        // OPEN A PERSISTENT PORT TO PYTHON
        // This prevents Chrome from killing the connection during long downloads
        const port = chrome.runtime.connectNative(HOST_NAME);
        
        // Listen for messages coming BACK from Python
        port.onMessage.addListener((response) => {
            console.log("Received message from Python:", response);
            
            if (response && response.status === "success") {
                // SUCCESS MESSAGE HANDLING
                showNotification('success', 'Download Complete!', 'Your video has been saved to the Downloads folder.');
                updateBadge("success");
                
            } else if (response && response.status === "error") {
                // ERROR MESSAGE HANDLING
                showNotification('error', 'Download Failed', response.message || 'Check downloader.log for details.');
                updateBadge("error");
            }
        });

        // Handle unexpected disconnections
        port.onDisconnect.addListener(() => {
            if (chrome.runtime.lastError) {
                console.error("Native host disconnected:", chrome.runtime.lastError.message);
                showNotification('error', 'Connection Lost', 'The Python background process crashed. Check downloader.log.');
                updateBadge("error");
            }
        });

        // Send the payload to Python
        port.postMessage({ 
            action: "download", 
            url: tab.url, 
            cookies: cookieString 
        });

    } catch (error) {
        showNotification('error', 'Extension Error', 'Failed to extract cookies: ' + error.message);
        updateBadge("error");
    }
});