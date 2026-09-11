   const HOST_NAME = "com.company.ytdlp_host";

   chrome.action.onClicked.addListener(async (tab) => {
       // Ignore internal browser pages
       if (!tab.url || tab.url.startsWith('chrome') || tab.url.startsWith('edge')) {
           console.error("Cannot download from internal browser pages.");
           return;
       }

       try {
           // 1. Extract cookies for the current tab's URL
           const cookies = await chrome.cookies.getAll({ url: tab.url });
           
           // 2. Format cookies into Netscape format
           let cookieString = "# Netscape HTTP Cookie File\n";
           cookies.forEach(cookie => {
               const domain = cookie.domain.startsWith('.') ? cookie.domain : '.' + cookie.domain;
               const flag = cookie.domain.startsWith('.') ? 'TRUE' : 'FALSE';
               const path = cookie.path || '/';
               const secure = cookie.secure ? 'TRUE' : 'FALSE';
               const expiration = Math.floor(cookie.expirationDate || 0);
               
               cookieString += `${domain}\t${flag}\t${path}\t${secure}\t${expiration}\t${cookie.name}\t${cookie.value}\n`;
           });

           // 3. Send payload to the Python Native Host
           chrome.runtime.sendNativeMessage(
               HOST_NAME,
               { 
                   action: "download", 
                   url: tab.url, 
                   cookies: cookieString 
               },
               (response) => {
                   if (chrome.runtime.lastError) {
                       console.error("Native Messaging Error:", chrome.runtime.lastError.message);
                   } else if (response && response.status === "success") {
                       console.log("Download initiated successfully.");
                   } else {
                       console.error("Download failed:", response ? response.message : "Unknown error");
                   }
               }
           );
       } catch (error) {
           console.error("Error extracting cookies:", error);
       }
   });