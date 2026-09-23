-- Passkeys: N credentials, each granting the same operate level (like a
-- bearer token, but bound to an authenticator instead of a shared secret).
-- `passkey_runner` holds the one `user_handle` WebAuthn needs per relying
-- party, generated once and reused for every credential this runner ever
-- registers.

CREATE TABLE passkey_runner (
    one         INTEGER PRIMARY KEY CHECK (one = 1),   -- a single row
    user_handle BLOB    NOT NULL
);

CREATE TABLE passkey_credential (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    credential_id BLOB    NOT NULL UNIQUE,
    public_key    BLOB    NOT NULL,               -- COSE key, as the authenticator sent it
    sign_count    INTEGER NOT NULL DEFAULT 0,
    aaguid        BLOB,
    transports    TEXT,                           -- JSON list, e.g. ["internal", "hybrid"]
    label         TEXT    NOT NULL,                -- operator-supplied, for the list in the UI
    created_ns    INTEGER NOT NULL
);
