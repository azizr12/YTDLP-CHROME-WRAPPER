document.addEventListener('DOMContentLoaded', () => {
    const archiveCheck = document.getElementById('archive');
    const noPlaylistCheck = document.getElementById('no-playlist');
    const audioOnlyCheck = document.getElementById('audio-only');
    const downloadBtn = document.getElementById('download-btn');
    const statusText = document.getElementById('status');

    // 1. Load saved settings when popup opens
    chrome.storage.local.get(['archive', 'noPlaylist', 'audioOnly'], (result) => {
        // Default to true for archive and no-playlist if not set yet
        archiveCheck.checked = result.archive !== false; 
        noPlaylistCheck.checked = result.noPlaylist !== false; 
        audioOnlyCheck.checked = result.audioOnly === true; 
    });

    // 2. Auto-save settings whenever a box is clicked
    const saveSettings = () => {
        chrome.storage.local.set({
            archive: archiveCheck.checked,
            noPlaylist: noPlaylistCheck.checked,
            audioOnly: audioOnlyCheck.checked
        });
    };

    archiveCheck.addEventListener('change', saveSettings);
    noPlaylistCheck.addEventListener('change', saveSettings);
    audioOnlyCheck.addEventListener('change', saveSettings);

    // 3. Handle the "Download" button click
    downloadBtn.addEventListener('click', async () => {
        statusText.textContent = "Starting...";
        
        // Get the currently active tab
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab || !tab.url) {
            statusText.textContent = "Error: No active tab.";
            return;
        }

        // Build the options array based on the checkboxes
        const options = [];
        if (archiveCheck.checked) {
            options.push("--download-archive", "archive.txt");
        }
        if (noPlaylistCheck.checked) {
            options.push("--no-playlist");
        }
        if (audioOnlyCheck.checked) {
            options.push("-x", "--audio-format", "mp3");
        }

        // Send the URL and Options to the background script
        chrome.runtime.sendMessage({
            action: "START_DOWNLOAD",
            url: tab.url,
            options: options
        }, (response) => {
            if (chrome.runtime.lastError) {
                statusText.textContent = "Error communicating with background.";
            } else {
                statusText.textContent = "Sent to Python!";
                // Close the popup after 1.5 seconds so you can watch the desktop notification
                setTimeout(() => window.close(), 1500);
            }
        });
    });
});