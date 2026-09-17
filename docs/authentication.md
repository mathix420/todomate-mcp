# How TodoMate authentication works

These research notes were written for the original issue [#1](https://github.com/LYJ0304/todomate-mcp/issues/1). They informed the Python `FirebaseAuthSession` implementation planned in issue [#2](https://github.com/LYJ0304/todomate-mcp/issues/2).

- Research date: 2026-09-05
- Reference repository: `3x-haust/todomate-api`
- Reviewed commit: [`987b3fe7bf8e580794e4d7b27ac5ced05426b068`](https://github.com/3x-haust/todomate-api/tree/987b3fe7bf8e580794e4d7b27ac5ced05426b068)
- **Reference code** describes behavior found at that commit. **Firebase documentation** describes the documented API. **Proposed design** describes the approach recommended for this project at the time of the review.

The review did not test a real account login or make live API calls. All values in the request examples are placeholders.

## Authentication flow

**Reference code:** `TodomateClient` creates a `FirebaseAuthSession` and passes it to `FirestoreRestClient`. Creating the client does not send an authentication request. Authentication happens when `idToken()`, `userId()`, or `snapshot()` needs a session. A session can start with an email and password or a refresh token. [Client setup][client] · [Authentication session][auth]

```text
Email and password + Firebase API key
  -> Sign in through Firebase Auth
  -> Store the ID token, refresh token, UID, and expiration time
  -> Request a valid ID token, refreshing it when needed
  -> Access Firestore with Authorization: Bearer <ID_TOKEN>
```

**Firebase documentation:** The Firestore REST API accepts a Firebase ID token in the `Authorization: Bearer <ID_TOKEN>` header. Firestore Security Rules determine which data the user can access. A successful login does not grant access to all data. [Firestore REST authentication][firestore-auth]

**Reference code:** Each Firestore request gets an ID token from the authentication session and builds this header. The reference server's `/auth/login` route issues its own session token, which is different from the Firebase ID token. That session token encodes the refresh token, UID, issue time, and expiration time. Requests to Firestore use the Firebase ID token. [Firestore requests][firestore] · [Login route][routes]

## Sign-in request

**Firebase documentation:** [Sign in with an email and password][sign-in]

```http
POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=<FIREBASE_API_KEY>
Content-Type: application/json

{"email":"<EMAIL>","password":"<PASSWORD>","returnSecureToken":true}
```

| Value | Type | Meaning |
| --- | --- | --- |
| `key` (query parameter) | string | The Firebase project's Web API key |
| `email` | string | The email address used to sign in |
| `password` | string | The account password |
| `returnSecureToken` | boolean | Requests an ID token and refresh token. Use `true` |

**Reference code:** Uses this endpoint and these JSON fields. It checks the HTTP status and returns `AUTH_FAILED` if the request fails. [Authentication session][auth]

## Sign-in response

**Firebase documentation:** A successful request returns HTTP 200 and a JSON response. The session uses these fields. [Sign-in response][sign-in]

| Field | Type | Meaning |
| --- | --- | --- |
| `idToken` | string | The ID token used to authenticate user requests |
| `refreshToken` | string | The refresh token used to obtain a new ID token |
| `localId` | string | The user's UID |
| `expiresIn` | string | The number of seconds until the ID token expires |

**Reference code:** Checks that all four fields are strings and maps `localId` to `uid`. It calculates the expiration time as `current_time_ms + Number(expiresIn) * 1000`. A response that does not match the expected schema produces `AUTH_RESPONSE_INVALID`. It does not separately check whether the expiration value is a valid positive number. [Authentication session][auth]

**Proposed design:** Require nonempty strings and check that the expiration value is a valid positive number. Use seconds throughout the session and calculate `expires_at = current_time_seconds + seconds_until_expiry`. Use the lifetime returned by Firebase instead of assuming a fixed value.

## Token refresh flow

**Firebase documentation:** [Refresh an ID token][refresh]

```http
POST https://securetoken.googleapis.com/v1/token?key=<FIREBASE_API_KEY>
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&refresh_token=<URL_ENCODED_REFRESH_TOKEN>
```

| Request value | Type | Meaning |
| --- | --- | --- |
| `key` (query parameter) | string | The Firebase API key |
| `grant_type` | string | Always `refresh_token` |
| `refresh_token` | string | The existing refresh token |

| Response field | Type | Meaning |
| --- | --- | --- |
| `id_token` | string | The new ID token |
| `refresh_token` | string | The existing or replacement refresh token. Store the returned value |
| `user_id` | string | The user's UID |
| `expires_in` | string | The number of seconds until the ID token expires |

**Reference code:** Uses this endpoint and these field names, but sends the request body as JSON. It uses the HTTP client's `json` option instead of the form format in the Firebase documentation. The review did not test whether Firebase accepts this JSON request. [Authentication session][auth] · [HTTP client][http]

**Reference code:** Reuses the token in memory when it has more than 60 seconds left before it expires. Otherwise, it refreshes the token the next time the session is used. There is no background refresh. A successful response replaces the stored tokens, UID, and expiration time. The 60-second margin is a choice made by the reference implementation, not a Firebase requirement.

**Proposed design:** Use the documented form format and let the HTTP client encode it. Keep the existing approach of refreshing on demand with a 60-second margin. Replace the session state only after validating the entire response. If a refresh fails, do not return an expired token to the caller.

### Handling failures

**Firebase documentation:** API errors include a reason in `error.message`. Refresh errors include `TOKEN_EXPIRED`, `INVALID_REFRESH_TOKEN`, `USER_DISABLED`, `USER_NOT_FOUND`, and `PROJECT_NUMBER_MISMATCH`. [Error responses][errors] · [Refresh errors][refresh]

**Reference code:**

- A failed sign-in HTTP request produces `AUTH_FAILED`. An unexpected response schema produces `AUTH_RESPONSE_INVALID`.
- If a refresh HTTP request fails or its response does not match the expected schema, a session started with an email and password tries to sign in again.
- A session started with a refresh token instead returns `AUTH_REFRESH_FAILED` or `AUTH_REFRESH_RESPONSE_INVALID`, respectively.
- Network errors from the default HTTP client are passed on as `UPSTREAM_REQUEST_FAILED` and do not trigger another sign-in attempt. JSON parsing errors also occur before the schema check, so they do not enter that retry path.
- The code does not inspect Firebase's detailed error body to distinguish the cause.

Sources: [Authentication session][auth] · [HTTP client][http]

**Proposed design:** Let callers distinguish sign-in failures, refresh failures, invalid responses, and network failures. Report a failed refresh instead of automatically signing in again with a password. Separate failures that require a new login from temporary connection problems. Keep raw response bodies, passwords, and tokens out of error messages. Do not retain the password in the session for future login attempts.

## Required configuration values

**Reference code:** Reads the `TODOMATE_FIREBASE_API_KEY` environment variable. The email and password or refresh token are passed to the constructor. The Firestore project ID comes from `firebaseConfig.projectId` in `config.ts`, and the database ID is `(default)`. [Configuration][config] · [Client][client] · [Firestore requests][firestore]

**Proposed design:** Keep configuration, sign-in input, and session state separate as shown below. These names describe each value's purpose; they do not define new environment variables.

| Category | Values | Responsible component |
| --- | --- | --- |
| Authentication configuration | Firebase API key | Authentication client |
| Sign-in input | Email and password | Sign-in call |
| Session state | ID token, refresh token, UID, expiration time | Authentication session, stored in memory |
| Firestore configuration | Project ID, database ID | Firestore client |

The API key does not replace the user's ID token. The reference server's CORS, port, and custom session-encryption settings are outside the Firebase session work in issue #2. Do not put real credentials in code or documentation.

## Planned responsibilities of the MCP authentication session

**Proposed design:** The `FirebaseAuthSession` planned in issue #2 has five responsibilities:

1. Sign in with an email and password and validate the response.
2. Keep the ID token, refresh token, UID, and expiration time in memory.
3. Return a valid ID token when requested, refreshing it when needed.
4. Update the session after a successful refresh.
5. Report authentication, response, and connection failures separately without exposing secrets.

The Firestore client sends Firestore requests and builds the Bearer header. The MCP tool layer validates tool inputs. Firestore paths and MCP protocol handling do not belong in the authentication session.

Restoring a session from a saved refresh token, storing credentials on disk, and managing environment variables and secrets were assigned to issue [#12](https://github.com/LYJ0304/todomate-mcp/issues/12). This research did not include writing the Python implementation, adding dependencies, using the Firebase Admin SDK, or signing in to a real account.

## Limitations and open questions

- The reference code's refresh request format differs from the Firebase documentation. The Python implementation should use form encoding.
- The reference code does not fully validate expiration values. The follow-up implementation should add these checks.
- Do not copy the automatic sign-in retry and broad error handling unchanged. Use the failure-handling approach described above.
- The review did not verify whether email-and-password sign-in and token refresh work for a real TodoMate account. It also did not check the project's current authentication settings or Firestore Security Rules.
- These notes do not confirm successful live API calls or support for JSON refresh requests. They record findings from the reference code and Firebase documentation to guide implementation.

## Sources

- [Reference authentication session][auth]
- [Client setup][client]
- [Firestore requests][firestore]
- [HTTP client][http]
- [Runtime configuration][config]
- [Server login route][routes]
- [Firebase email-and-password sign-in][sign-in]
- [Firebase token refresh][refresh]
- [Firebase error responses][errors]
- [Firestore REST authentication][firestore-auth]

[auth]: https://github.com/3x-haust/todomate-api/blob/987b3fe7bf8e580794e4d7b27ac5ced05426b068/src/firebase-auth.ts
[client]: https://github.com/3x-haust/todomate-api/blob/987b3fe7bf8e580794e4d7b27ac5ced05426b068/src/todomate-client.ts
[firestore]: https://github.com/3x-haust/todomate-api/blob/987b3fe7bf8e580794e4d7b27ac5ced05426b068/src/firestore-client.ts
[http]: https://github.com/3x-haust/todomate-api/blob/987b3fe7bf8e580794e4d7b27ac5ced05426b068/src/http.ts
[config]: https://github.com/3x-haust/todomate-api/blob/987b3fe7bf8e580794e4d7b27ac5ced05426b068/src/config.ts
[routes]: https://github.com/3x-haust/todomate-api/blob/987b3fe7bf8e580794e4d7b27ac5ced05426b068/src/server/auth-routes.ts
[sign-in]: https://firebase.google.com/docs/reference/rest/auth#section-sign-in-email-password
[refresh]: https://firebase.google.com/docs/reference/rest/auth#section-refresh-token
[errors]: https://firebase.google.com/docs/reference/rest/auth#section-error-format
[firestore-auth]: https://firebase.google.com/docs/firestore/use-rest-api#authentication_and_authorization
