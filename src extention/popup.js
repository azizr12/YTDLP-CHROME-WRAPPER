console.log("[POPUP 1] popup.js loaded and DOM is ready.");

document.addEventListener('DOMContentLoaded', () => {
    const archiveCheck = document.getElementById('archive');
    const noPlaylistCheck = document.getElementById('no-playlist');
    const audioOnlyCheck = document.getElementById('audio-only');
    const downloadBtn = document.getElementById('download-btn');
    const statusText = document.getElementById('status');

    console.log("[POPUP 2] Loading saved settings from Chrome Storage...");
    
    // 1. Load saved settings
    chrome.storage.local.get(['archive', 'noPlaylist', 'audioOnly'], (result) => {
        console.log("[POPUP 3] Storage result received:", result);
        
        archiveCheck.checked = result.archive !== false; 
        noPlaylistCheck.checked = result.noPlaylist !== false; 
        audioOnlyCheck.checked = result.audioOnly === true; 
        
        console.log("[POPUP 4] Checkboxes set. Archive:", archiveCheck.checked, "NoPlaylist:", noPlaylistCheck.checked, "Audio:", audioOnlyCheck.checked);
    });

    // 2. Auto-save settings
    const saveSettings = () => {
        console.log("[POPUP 5] Checkbox clicked. Saving new state to storage...");
        chrome.storage.local.set({
            archive: archiveCheck.checked,
            noPlaylist: noPlaylistCheck.checked,
            audioOnly: audioOnlyCheck.checked
        });
    };

    archiveCheck.addEventListener('change', saveSettings);
    noPlaylistCheck.addEventListener('change', saveSettings);
    audioOnlyCheck.addEventListener('change', saveSettings);

    // 3. Handle Download Button
    downloadBtn.addEventListener('click', async () => {
        console.log("[POPUP 6] >>> DOWNLOAD BUTTON CLICKED <<<");
        statusText.textContent = "Starting...";
        
        console.log("[POPUP 7] Querying Chrome for the active tab...");
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        
        if (!tab || !tab.url) {
            console.error("[POPUP ERROR] Could not find active tab or URL is invalid.");
            statusText.textContent = "Error: No active tab.";
            return;
        }
        
        console.log("[POPUP 8] Active tab found. URL:", tab.url);

        // Build options array
        const options = [];
        if (archiveCheck.checked) options.push("--download-archive", "archive.txt");
        if (noPlaylistCheck.checked) options.push("--no-playlist");
        if (audioOnlyCheck.checked) options.push("-f", "bestaudio");
        
        console.log("[POPUP 9] Built options array:", options);
        console.log("[POPUP 10] Sending message to background.js...");

        // Send to background
        chrome.runtime.sendMessage({
            action: "START_DOWNLOAD",
            url: tab.url,
            options: options
        }, (response) => {
            if (chrome.runtime.lastError) {
                console.error("[POPUP ERROR] Failed to reach background.js:", chrome.runtime.lastError.message);
                statusText.textContent = "Error: " + chrome.runtime.lastError.message;
            } else {
                console.log("[POPUP 11] SUCCESS: Message delivered to background.js. Response:", response);
                statusText.textContent = "Sent to Receiver!";
                setTimeout(() => window.close(), 1500);
            }
        });
    });
});