/**
 * @file app_login.js
 * @description
 * Handles the login flow for Universal-Translator. 
 * - Reads nickname and interface language from form inputs.
 * - Generates a unique client ID (cid).
 * - Sends a registration request to the server.
 * - Stores cid, nick, and lang in sessionStorage.
 * - Redirects to the chat page on success.
 */

(() => {
    'use strict';
  
    /** IDs of DOM elements */
    const ENTER_BTN_ID = 'enterBtn';
    const NICK_INPUT_ID = 'nick';
    const LANG_SELECT_ID = 'lang';
  
    /** API endpoint for registration */
    const REGISTER_ENDPOINT = '/api/register';
  
    /** Page to navigate to after successful login */
    const CHAT_PAGE = '/chat.html';
  
    // DOM references
    const enterBtn = document.getElementById(ENTER_BTN_ID);
    const nickInput = document.getElementById(NICK_INPUT_ID);
    const langSelect = document.getElementById(LANG_SELECT_ID);
  
    if (!enterBtn || !nickInput || !langSelect) {
      console.error('app_login.js: Required DOM elements not found');
      return;
    }
  
    /**
     * Handle the "Enter chat" button click.
     * @param {MouseEvent} event
     */
    async function handleLogin(event) {
      event.preventDefault();
  
      // Generate a cryptographically secure UUID for this client
      const cid = crypto.randomUUID();
  
      // Read and sanitize user inputs
      const userNick = nickInput.value.trim() || 'anon';
      const userLang = langSelect.value;
  
      // Prepare registration payload
      const payload = {
        cid,
        nick: userNick,
        lang: userLang
      };
  
      try {
        // Send registration request
        const res = await fetch(REGISTER_ENDPOINT, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
  
        if (!res.ok) {
          // Registration failed; log and alert the user
          console.error('Registration failed with status', res.status);
          alert('Registration failed. Please try again.');
          return;
        }
  
        // Persist session data
        sessionStorage.setItem('cid', cid);
        sessionStorage.setItem('nick', userNick);
        sessionStorage.setItem('lang', userLang);
  
        // Navigate to chat interface
        window.location.href = CHAT_PAGE;
  
      } catch (error) {
        console.error('Network error during registration:', error);
        alert('Unable to register due to network error. Please check your connection and try again.');
      }
    }
  
    // Attach event listener
    enterBtn.addEventListener('click', handleLogin);
  })();
  