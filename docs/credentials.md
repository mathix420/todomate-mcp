# Managing TodoMate credentials

This guide covers local authentication and refresh-token storage, as described in the original issue #12. Keep real credentials out of documentation and Git.

## First-time setup

Copy `.env.example` to `.env` and enter your Firebase API key:

```dotenv
TODOMATE_FIREBASE_API_KEY=<FIREBASE_WEB_API_KEY>
```

Then sign in interactively to save your credentials in the operating system's Keyring or Credential Manager:

```sh
todomate-mcp auth login
```

In Docker, the image instead saves credentials to `/data/credentials.json`; mount a persistent volume at `/data`. The file contains the refresh token and UID, has owner-only permissions, and is replaced atomically when the token rotates. Set `TODOMATE_CREDENTIALS_FILE` to select a different path, or leave it unset outside Docker to use the OS keyring. See the [stack setup and login instructions](deployment.md#add-to-the-hermes-and-duplicacy-stack).

## Restoring a session and updating refresh tokens

The stdio MCP server reads the saved credentials at startup. On the first Todo request, it uses the stored refresh token to restore the Firebase session.

Later runs can obtain an ID token from the saved refresh token without asking for your email or password. When the ID token has 60 seconds or less left before it expires, the next request refreshes it automatically. If Firebase returns a new refresh token, the server updates the saved credentials.

If a refresh request fails with an HTTP authentication error, the credentials may no longer be valid. The server removes them from the keyring. Run `todomate-mcp auth login` to sign in again. A network error does not mean the token is invalid; check your connection and try again.

## Keeping credentials private

- Keep `.env` and keyring credentials on your own device. Do not share them, commit them, or print them in logs.
- When passing secrets through environment variables, keep their values out of shell history, CI logs, and error messages.
- If you lose access to the account or suspect a token has been exposed, remove the `todomate-mcp` / `firebase` entry from your operating system's Keyring or Credential Manager, then sign in again.
