# How TodoMate uses Firestore

These research notes were written for the original issue [#3](https://github.com/LYJ0304/todomate-mcp/issues/3). They were intended to guide the Firestore client and Todo adapter implementations.

- Research date: 2026-09-05
- Reference repository: `3x-haust/todomate-api`
- Reviewed commit: [`987b3fe7bf8e580794e4d7b27ac5ced05426b068`](https://github.com/3x-haust/todomate-api/tree/987b3fe7bf8e580794e4d7b27ac5ced05426b068)
- **Reference code** describes findings from that commit. **Firebase documentation** describes the documented API. **Unverified** means the behavior was not tested against real TodoMate data.

The review did not use or record real accounts, Todo data, API keys, or ID tokens. User IDs, document IDs, and tokens in the examples are placeholders.

## Live verification update — 2026-09-17

Subsequent testing compared three TodoMate web-app creations with this MCP implementation. All three browser writes used the `TodoItem` collection, a nonempty `goalID`, and the same UTC-midnight date encoding documented below. The MCP's previous optional group argument sent `goalID: null`, which failed with HTTP 403. Supplying a valid group ID with the existing MCP payload succeeded and returned the created todo.

The `Goal` collection uses `userID` for ownership (unlike `TodoItem.writerID`). Goal documents expose `id`, `title`, and a numeric `priority`. The `list_goals` tool queries the connected user's groups and sorts by priority. `create_todo` now requires `goal_id`; see the [MCP usage guide](mcp-usage.md).

The browser initialized `hasPhoto`, `isMemoPublic`, and `likesTotalCount` to null, whereas the reference-based MCP payload initializes them to false/false/zero. The MCP payload was accepted once a valid group ID was supplied, so those differences did not cause the observed failure. These observations apply to the tested account and app version; they do not establish every Firestore Security Rule. No captured account data or request traces are included in this repository.

Additional captured updates established these operations:

| User action | Write |
| --- | --- |
| Edit text | Update `content` with a `content` field mask |
| Change date | Update `date` to UTC-midnight milliseconds with a `date` field mask |
| Remove date | Update `date` to null with the same field mask |
| Restore date | Update the null `date` back to UTC-midnight milliseconds |
| Set private/public memo | Update `memo` and `isMemoPublic` together with both fields in the mask |
| Delete todo | Delete the `TodoItem` document |

Thus an existing todo's `date` can be null. The MCP supports this state with `list_todos(unscheduled=true)` and `schedule_todo(day=null)`. `set_todo_memo` follows the observed field masks and defaults to private. The browser also writes account UI preferences and notification-read timestamps under `UserData`; these incidental UI changes are not side effects of the MCP's todo operations. Routine and timer write formats were not captured.

Undated queries use the Firestore `unaryFilter` operator `IS_NULL`, combined with the ownership filter, rather than a field equality comparison to a null value. See the [StructuredQuery reference](https://firebase.google.com/docs/firestore/reference/rest/v1/StructuredQuery#unaryfilter).

Later captures also showed:

- Group creation in `Goal`, with a random 20-character ID, owner `userID`, `title`, numeric ARGB `color`, integer `priority`, `createTime`, `finishType: null`, `crewId: null`, and visibility fields. Smaller priority values sort first. Changing status writes only `finishType`; the web application's shipped `GoalFinishType` enum maps `done=0`, `end=1`, `stop=2`, with null for active. Deletion removes the `Goal` document. The MCP refuses deletion while owned todos reference the group.
- Diary creation in `Diary`, with a random 20-character ID, `writerID`, UTC-midnight `date`, `body`, `emoji`, `createTime`, `isDraft: false`, `likesTotalCount: 0`, and null `sticker`, `color`, `imageURL`, `temperature`, `likes`, and `likesTotalSenderIDs`. Deletion removes the `Diary` document. Updates use masks for the fields being changed.
- Group and diary sharing is described by all three of `isPublic`, `viewerIDs`, and `isViewerIDsFollowers`. `isPublic: false` alone does **not** mean owner-only. The observed follower sharing populated `viewerIDs` from the owner's `UserData.followerIds`. The MCP's private mode writes `isPublic: false`, `viewerIDs: []`, and `isViewerIDsFollowers: false`; follower/public modes must be requested explicitly.

Default MCP dates use the standard `TZ` environment variable, with UTC as fallback. This changes the choice of calendar date, not the UTC-midnight encoding of that selected date in Firestore.

## Native reminder field — 2026-09-17

Read-only inspection of the [shipped TodoMate web application](https://www.todomate.net/main.dart.js) confirmed that the alarm picker updates `TodoItem.remindAt` with integer Unix milliseconds, and clearing an alarm writes null. The app converts this field through its milliseconds-since-epoch date constructor. Its alarm picker combines the todo's date with the selected local time. Its date-moving UI also adjusts an existing alarm to the new date, preserving local hours and minutes.

The MCP exposes `set_todo_reminder(todo_id, remind_at)`, requiring an explicit timezone offset on non-null timestamps and encoding the instant as Unix milliseconds (sub-millisecond precision is discarded). It checks ownership and patches only `remindAt`. Todo results decode the field as `remind_at` in UTC. Existing MCP date operations still update only `date`; callers must change the reminder separately if desired. Missing/null reminders remain null. Tests cover conversion, clearing, ownership, preservation of other fields, tool discovery, and the authenticated adapter wrapper. This inspection did not perform a live reminder write or verify device notification delivery.

## Database and document paths

**Reference code:** The Firebase project ID is `mate-914f3`, and the database ID is `(default)`. Todos are stored in the shared `TodoItem` collection.

```text
projects/mate-914f3/databases/(default)/documents
└── TodoItem
    └── <TODO_ID>
```

Todos are not stored in a user-specific subcollection such as `users/<uid>/todos`. To list a user's todos, the code queries `TodoItem` with `writerID == <UID>` and `date == <DATE_AT_UTC_MIDNIGHT>`. It then sorts the results by `createTime`, from oldest to newest. [Configuration][config] · [Todo queries][readers]

**Firebase documentation:** Firestore REST document paths use the format `projects/{projectId}/databases/{databaseId}/documents/{document_path}`. [REST API guide][rest]

## Authentication

**Reference code:** Each request obtains a Firebase ID token and sends it in this header:

```http
Authorization: Bearer <FIREBASE_ID_TOKEN>
```

**Firebase documentation:** Firestore Security Rules determine the user's permissions when a request uses a Firebase ID token. An API key or refresh token cannot serve as the Bearer token for a Firestore request. [REST authentication][rest]

## Todo document schema

`createTodo` always writes the following fields. The types shown are the REST `Value` types produced by the reference code's Firestore encoder. This is the schema used when that code creates a Todo; existing Todo documents may have different fields.

| Field | REST Value type | Initial value or meaning |
| --- | --- | --- |
| `id` | `stringValue` | The Todo document ID |
| `writerID` | `stringValue` | The author's Firebase UID, used to filter queries by user |
| `content` | `stringValue` | The Todo's text |
| `date` | `integerValue` | Midnight UTC on the selected date, in Unix epoch milliseconds |
| `createTime` | `integerValue` | The creation time, in Unix epoch milliseconds |
| `isDone` | `booleanValue` | Whether the Todo is complete. Initially `false` |
| `doneTime` | `nullValue` | Initially `null`; changed to a time in milliseconds when completed |
| `goalID` | `stringValue` | The ID of the linked Goal document |
| `remindAt` | `integerValue` or `nullValue` | The reminder time, or `null` |
| `spentTime` | `nullValue` | Time spent on the task. Initially `null` |
| `hasPhoto`, `hasTimer`, `isMemoPublic` | `booleanValue` | All initially `false` |
| `likesTotalCount` | `integerValue` | Initially `0` |
| `likes`, `likesTotalSenderIDs`, `memo`, `photoURL`, `routineID`, `timer` | `nullValue` | All initially `null` |

Sources: [Todo creation][client] · [Firestore value encoder][firestore-codec]

The creation input requires a nonempty `goalID`. The review did not verify whether that value must be a document ID in the `Goal` collection, whether the document must exist, or whether it can belong to another user. [Input schemas][schemas]

`date` is not an ISO date string or a Firestore `timestampValue`. The reference code converts `YYYYMMDD` with `Date.UTC(year, month - 1, day)` and uses the resulting number for storage and queries. For example, `20260905` becomes `1788566400000`. The review did not verify the app's date and timezone behavior against real TodoMate data. [Date conversion][dates]

## Listing todos

**Reference code:** Sends a structured query to this Firestore `runQuery` endpoint:

```http
POST https://firestore.googleapis.com/v1/projects/mate-914f3/databases/(default)/documents:runQuery
Authorization: Bearer <FIREBASE_ID_TOKEN>
Content-Type: application/json
```

```json
{
  "structuredQuery": {
    "from": [{"collectionId": "TodoItem"}],
    "where": {
      "compositeFilter": {
        "op": "AND",
        "filters": [
          {
            "fieldFilter": {
              "field": {"fieldPath": "writerID"},
              "op": "EQUAL",
              "value": {"stringValue": "<UID>"}
            }
          },
          {
            "fieldFilter": {
              "field": {"fieldPath": "date"},
              "op": "EQUAL",
              "value": {"integerValue": "1788566400000"}
            }
          }
        ]
      }
    }
  }
}
```

The response is an array of rows, some of which may not contain a `document`. The reference code keeps only rows with a document, converts the Firestore values in `fields` to ordinary JSON values, and sorts them by `createTime`, from oldest to newest. [Query construction][firestore-codec] · [Todo queries][readers]

**Firebase documentation:** `runQuery` accepts a POST request at the parent path shown above, with a `structuredQuery` body. Response rows may contain a `document`, a `readTime`, or completion information. The client must handle rows without a document. [runQuery reference][run-query]

A `GET` request for one Todo **can be constructed** from the reference code's general `getDocument` method, as shown below. The reference code does not call it to fetch an individual Todo, and this request was not tested against TodoMate.

```http
GET https://firestore.googleapis.com/v1/projects/mate-914f3/databases/(default)/documents/TodoItem/<TODO_ID>
Authorization: Bearer <FIREBASE_ID_TOKEN>
```

## Creating a Todo

**Reference code:** Creates a Todo ID by joining the Firebase UID and a random 20-character string of letters and digits. It then sends a Firestore `PATCH` request to that document path. `PATCH` creates the document if it does not exist and updates it if it does. Avoiding duplicate IDs therefore depends on the quality of the random string. The code does not set a condition that requires the document to be new. [Todo creation][client] · [Firestore requests][firestore-client]

```http
PATCH https://firestore.googleapis.com/v1/projects/mate-914f3/databases/(default)/documents/TodoItem/<UID><RANDOM_20_CHARS>
Authorization: Bearer <FIREBASE_ID_TOKEN>
Content-Type: application/json
```

```json
{
  "fields": {
    "id": {"stringValue": "<TODO_ID>"},
    "writerID": {"stringValue": "<UID>"},
    "content": {"stringValue": "Study algorithms"},
    "date": {"integerValue": "1788566400000"},
    "createTime": {"integerValue": "1788566400000"},
    "isDone": {"booleanValue": false},
    "doneTime": {"nullValue": null},
    "goalID": {"stringValue": "<GOAL_ID>"},
    "remindAt": {"nullValue": null}
  }
}
```

This example shows only some of the default fields to keep the request easy to follow. The reference code sends every creation field listed in the [Todo document schema](#todo-document-schema).

**Firebase documentation:** `PATCH` updates or inserts a document and returns a `Document` on success. Without an update mask, it updates the document using the supplied body. [PATCH reference][patch]

## Converting Firestore REST values

**Reference code:** Handles strings, booleans, nulls, integers, floating-point numbers, arrays, and objects. Integers are serialized as strings in the REST `integerValue` field. The code does not implement types such as `timestampValue`, `referenceValue`, `bytesValue`, or `geoPointValue`. [Firestore value encoder][firestore-codec]

**Firebase documentation:** `Document.fields` maps field names to `Value` objects. Check actual document data to confirm each field's exact type. [Document reference][document] · [Value reference][value]

## Open questions and next steps

- The review did not check real TodoMate Firestore data, Security Rules, composite indexes, or write permissions.
- It did not establish whether the server or app requires every field written during `TodoItem` creation, or whether existing documents can omit those fields.
- It did not verify the rules for `goalID` references or the app's timezone rules for `date`.
- It did not verify whether TodoMate's Security Rules allow the reference code's use of `PATCH` to create or update a document.
- Follow-up work should use the confirmed paths and request formats when building a minimal Firestore client. Treat inferred schema details as assumptions until they are verified.

## Sources

- [Reference repository configuration][config]
- [Reference Todo creation and update code][client]
- [Reference Todo list queries][readers]
- [Reference Firestore REST client][firestore-client]
- [Reference Firestore value encoder and query construction][firestore-codec]
- [Reference date conversion][dates]
- [Reference input schemas][schemas]
- [Firestore REST authentication][rest]
- [Firestore runQuery][run-query]
- [Firestore PATCH][patch]
- [Firestore Document][document]
- [Firestore Value][value]

[config]: https://github.com/3x-haust/todomate-api/blob/987b3fe7bf8e580794e4d7b27ac5ced05426b068/src/config.ts
[client]: https://github.com/3x-haust/todomate-api/blob/987b3fe7bf8e580794e4d7b27ac5ced05426b068/src/todomate-client.ts
[readers]: https://github.com/3x-haust/todomate-api/blob/987b3fe7bf8e580794e4d7b27ac5ced05426b068/src/todomate-readers.ts
[firestore-client]: https://github.com/3x-haust/todomate-api/blob/987b3fe7bf8e580794e4d7b27ac5ced05426b068/src/firestore-client.ts
[firestore-codec]: https://github.com/3x-haust/todomate-api/blob/987b3fe7bf8e580794e4d7b27ac5ced05426b068/src/firestore.ts
[dates]: https://github.com/3x-haust/todomate-api/blob/987b3fe7bf8e580794e4d7b27ac5ced05426b068/src/record-utils.ts
[schemas]: https://github.com/3x-haust/todomate-api/blob/987b3fe7bf8e580794e4d7b27ac5ced05426b068/src/schemas.ts
[rest]: https://firebase.google.com/docs/firestore/use-rest-api#authentication_and_authorization
[run-query]: https://firebase.google.com/docs/firestore/reference/rest/v1/projects.databases.documents/runQuery
[patch]: https://firebase.google.com/docs/firestore/reference/rest/v1/projects.databases.documents/patch
[document]: https://firebase.google.com/docs/firestore/reference/rest/v1/projects.databases.documents
[value]: https://firebase.google.com/docs/firestore/reference/rest/v1/Value
