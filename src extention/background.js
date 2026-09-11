const HOST_NAME = "com.company.ytdlp_host"; 

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

function updateBadge(status) {
    if (status === "success") {
        chrome.action.setBadgeText({ text: "✓" });
        chrome.action.setBadgeBackgroundColor({ color: "#4CAF50" }); 
    } else if (status === "error") {
        chrome.action.setBadgeText({ text: "!" });
        chrome.action.setBadgeBackgroundColor({ color: "#F44336" }); 
    } else if (status === "downloading") {
        chrome.action.setBadgeText({ text: "⏳" });
        chrome.action.setBadgeBackgroundColor({ color: "#FF9800" }); 
    }
    setTimeout(() => chrome.action.setBadgeText({ text: "" }), 15000);
}

// Listen for messages from the popup.html
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "START_DOWNLOAD") {
        startDownloadProcess(request.url, request.options);
        sendResponse({ message: "Download process initiated." });
        return true; 
    }
});

async function startDownloadProcess(url, options) {
    if (!url || url.startsWith('chrome') || url.startsWith('edge')) {
        showNotification('error', 'Downloader', 'Cannot download from internal browser pages.');
        return;
    }

    showNotification('start', 'Downloader', 'Extracting cookies and starting download...');
    updateBadge("downloading");

    try {
        const cookies = await chrome.cookies.getAll({ url: url });
        let cookieString = "# Netscape HTTP Cookie File\n";
        cookies.forEach(cookie => {
            const domain = cookie.domain.startsWith('.') ? cookie.domain : '.' + cookie.domain;
            const flag = cookie.domain.startsWith('.') ? 'TRUE' : 'FALSE';
            const path = cookie.path || '/';
            const secure = cookie.secure ? 'TRUE' : 'FALSE';
            const expiration = Math.floor(cookie.expirationDate || 0);
            cookieString += `${domain}\t${flag}\t${path}\t${secure}\t${expiration}\t${cookie.name}\t${cookie.value}\n`;
        });

        const port = chrome.runtime.connectNative(HOST_NAME);
        
        port.onMessage.addListener((response) => {
            if (response && response.status === "success") {
                showNotification('success', 'Download Complete!', response.message || 'Saved to folder.');
                updateBadge("success");
            } else if (response && response.status === "error") {
                showNotification('error', 'Download Failed', response.message || 'Check downloader.log.');
                updateBadge("error");
            }
        });

        port.onDisconnect.addListener(() => {
            if (chrome.runtime.lastError) {
                showNotification('error', 'Connection Lost', 'Python process crashed.');
                updateBadge("error");
            }
        });

        // Send URL, Cookies, AND the UI Options to Python
        port.postMessage({ 
            action: "download", 
            url: url, 
            cookies: cookieString,
            options: options 
        });

    } catch (error) {
        showNotification('error', 'Extension Error', 'Failed to extract cookies: ' + error.message);
        updateBadge("error");
    }
}