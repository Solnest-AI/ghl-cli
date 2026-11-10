# Getting your GHL workflow token

You only need this to **build, edit or clone workflows** (the `--experimental` workflow
commands). Everything else works with just your GHL key.

GoHighLevel's public API can't build workflows, so these commands use the same login token
GoHighLevel's own website uses. That's unofficial, and it can break when GHL updates. If you'd
rather not, skip this and every other command still works.

The token comes from your own logged-in GoHighLevel tab. No extension, no install, and nothing
leaves your browser: you paste a short snippet into the browser's DevTools console and it copies
the token to your clipboard. Then the CLI saves it without ever showing it.

## Steps

1. In Chrome (or Edge, Brave, any Chromium browser), open GoHighLevel and log in. Use the same
   address you set up the CLI with: `https://app.gohighlevel.com`, or your agency's own address
   if it has one. Stay on a normal page inside the app, like the dashboard.
2. Open DevTools straight to the Console: **Cmd+Option+J** (Mac) or **Ctrl+Shift+J**
   (Windows/Linux).
3. Paste this and press Enter:

   ```js
   var _copy = copy;   // capture BEFORE the first await - see note below
   (async () => {
     const db = await new Promise((res, rej) => {
       const r = indexedDB.open("firebaseLocalStorageDb");
       r.onsuccess = e => res(e.target.result);
       r.onerror = () => rej("Cannot open IndexedDB");
     });
     const entries = await new Promise((res, rej) => {
       const tx = db.transaction("firebaseLocalStorage", "readonly");
       const all = tx.objectStore("firebaseLocalStorage").getAll();
       all.onsuccess = () => res(all.result);
       all.onerror = () => rej("Failed to read store");
     });
     for (const e of entries) {
       const stm = (e?.value || e)?.stsTokenManager;
       if (stm?.refreshToken) {
         _copy(stm.refreshToken);
         console.log("\u2713 Copied " + stm.refreshToken.length + " chars. Now go back to Claude and say copied.");
         return;
       }
     }
     console.warn("No refresh token found \u2014 make sure you're logged into GHL on this tab.");
   })();
   ```

4. The console prints `✓ Copied ... chars. Now go back to Claude and say copied.` The token is
   now on your clipboard. Don't paste it anywhere. In Claude Code, just say "copied", and Claude
   runs:

   ```bash
   ghl keys paste workflow-token
   ```

   It reads the clipboard, checks the token looks right, saves it in `~/.ghl-cli/keys.env`,
   clears the clipboard and checks it works. It prints `saved, works` or says exactly what's
   wrong. It never prints the token.

> **`Uncaught ReferenceError: copy is not defined`** means you are running an older
> copy of this snippet. `copy()` is a DevTools console helper that exists only during
> *synchronous* evaluation: Chrome injects it as a temporary scope around your pasted
> code, and that scope is gone once the function hits its first `await`. That is why
> the first line captures it as `_copy` before any awaiting happens. Use the snippet
> exactly as written above.

> **Chrome may block the paste** the first time. If it warns you, type `allow pasting`
> in the console, press Enter, then paste again.

> If the console shows `No refresh token found`, make sure you ran the snippet on a
> GoHighLevel app tab where you're logged in (not a marketing page).

## Notes

- The token is sensitive: it's your whole GoHighLevel login. Treat it like a password. Never
  paste it into a chat, a doc or an email.
- The snippet only **reads** from your own browser's storage (IndexedDB) and uses the built-in
  DevTools `copy()` helper. It makes no network calls.
- Once saved, the CLI keeps itself logged in. Get a fresh token only when `ghl doctor` says the
  workflow token stopped working (a password change does that, for example).
