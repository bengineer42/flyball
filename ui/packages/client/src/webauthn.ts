// For Phase 3 (passkeys in the Go front). Dormant in Phase 1: only the passkey client methods use it.
/**
 * The browser side of a WebAuthn ceremony: the server hands JSON with base64url-encoded
 * `ArrayBuffer` fields (`challenge`, credential/user ids, ...); `navigator.credentials`
 * wants real `ArrayBuffer`s, and the `PublicKeyCredential` it returns has to go back to
 * the server as JSON, base64url again. Everything here is that round trip, nothing else.
 */

function base64urlToBytes(value: string): Uint8Array<ArrayBuffer> {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function bytesToBase64url(bytes: ArrayBuffer): string {
  let binary = "";
  for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The server's registration options, straight off `POST /api/auth/passkey/challenge`. */
export function decodeCreationOptions(options: Record<string, any>): PublicKeyCredentialCreationOptions {
  return {
    ...options,
    challenge: base64urlToBytes(options.challenge),
    user: { ...options.user, id: base64urlToBytes(options.user.id) },
    excludeCredentials: (options.excludeCredentials ?? []).map((c: Record<string, any>) => ({
      ...c,
      id: base64urlToBytes(c.id),
    })),
  } as unknown as PublicKeyCredentialCreationOptions;
}

/** The server's authentication options, from `POST /api/auth/passkey/login/challenge`. */
export function decodeRequestOptions(options: Record<string, any>): PublicKeyCredentialRequestOptions {
  return {
    ...options,
    challenge: base64urlToBytes(options.challenge),
    allowCredentials: (options.allowCredentials ?? []).map((c: Record<string, any>) => ({
      ...c,
      id: base64urlToBytes(c.id),
    })),
  } as unknown as PublicKeyCredentialRequestOptions;
}

/** A `PublicKeyCredential` the browser returned, as JSON for `POST /api/auth/passkey/{register,login}`. */
export function encodeCredential(credential: PublicKeyCredential): Record<string, unknown> {
  const response = credential.response as AuthenticatorAttestationResponse & AuthenticatorAssertionResponse;
  const out: Record<string, unknown> = {
    id: credential.id,
    rawId: bytesToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bytesToBase64url(response.clientDataJSON),
    },
  };
  const out_response = out.response as Record<string, unknown>;
  if (response.attestationObject) {
    out_response.attestationObject = bytesToBase64url(response.attestationObject);
    out_response.transports = response.getTransports?.() ?? [];
  }
  if (response.authenticatorData) out_response.authenticatorData = bytesToBase64url(response.authenticatorData);
  if (response.signature) out_response.signature = bytesToBase64url(response.signature);
  if (response.userHandle) out_response.userHandle = bytesToBase64url(response.userHandle);
  return out;
}
